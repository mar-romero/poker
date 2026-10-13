#!/usr/bin/env python
"""Opt-in blocking pull-request publication flow for task worktrees.

Activated by``harness/publish-policy.json`` with ``mode == 'pr'``.  When the
flag is on, publication of a task worktree:

1. pushes the task branch (``agent/<task>``) to the configured remote,
2. opens a GitHub pull request against the integration branch via ``gh``,
3. records ``pull-request.json`` plus ``publish.json`` (status INTEGRATED) in
   the task run dir, and
4. deliberately does NOT fast-forward the canonical branch, remove the
   worktree or release the writer lock.

After a human merges the PR on GitHub, an explicit ``finalize`` step verifies
that the task commit became an ancestor of ``origin/<base_branch>`` (it must be
a merge or rebase merge, never a squash), fast-forwards the canonical branch,
releases the worktree/writer lock and rewrites ``publish.json`` with status
PASS.  The finish gate contract (``gate.finish_decision`` publication
requirement) stays unchanged because it only ever looks at ``publish.json``
through ``worktree.publish_status``.

``mode: fast_forward`` or an absent policy file restores the legacy
``scripts/worktree.py`` fast-forward flow, which this module refuses to bypass.
All git primitives are reused from ``scripts/worktree.py`` by import (same
underscore-import precedent as ``scripts/receipt_review.py``).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path

from harnesslib import git, load_json, run_dir, runtime_root, safe_task_id, write_json_atomic
from worktree import (
    _assert_authorized_surface,
    _assert_scope_expansion_covers_changes,
    _authorized_surface,
    _candidate_files,
    _cleanup_published_worktree,
    _committed_files,
    _dirty_files,
    _load_lock,
    _publication_preconditions,
    _root_has_tracked_changes,
    _root_untracked_overlap,
    _safe_lock,
    _safe_worktree,
    publish_artifact,
    publish_status,
)

POLICY_PATH = 'harness/publish-policy.json'

# pull-request.json state machine; artifacts only ever contain OPEN/FINALIZED
# on disk (MERGED is derived live from gh, never cached in the artifact).
PR_STATES = ('OPEN', 'MERGED', 'FINALIZED')


def _policy():
    """Load the publication policy and fail closed unless mode is 'pr'."""
    try:
        data = load_json(POLICY_PATH)
    except FileNotFoundError as exc:
        raise ValueError(
            'publish policy is missing; PR publication is disabled and the '
            'legacy scripts/worktree.py fast-forward flow applies (flag off)'
        ) from exc
    if not isinstance(data, dict) or data.get('mode') != 'pr':
        label = data.get('mode') if isinstance(data, dict) else None
        raise ValueError(
            f'publish policy mode is {label!r}; publish_pr requires policy mode "pr"; '
            'the legacy fast-forward flow applies (flag off)'
        )
    pr = data.get('pr')
    if not isinstance(pr, dict):
        raise ValueError('publish policy pr section missing')
    remote = pr.get('remote')
    base_branch = pr.get('base_branch')
    if not isinstance(remote, str) or not remote:
        raise ValueError('publish policy pr.remote missing')
    if not isinstance(base_branch, str) or not base_branch:
        raise ValueError('publish policy pr.base_branch missing')
    return {'mode': 'pr', 'remote': remote, 'base_branch': base_branch}


def run_gh(args):
    """Run the gh binary and fail closed on absence, non-zero rc or unusable
    output. Returns the completed process; callers parse ``stdout``."""
    cmd = ['gh', *(str(a) for a in args)]
    try:
        proc = subprocess.run(
            cmd, text=True, encoding='utf-8', errors='replace',
            capture_output=True, check=False,
        )
    except OSError as exc:
        raise RuntimeError(f'gh CLI is not available: {exc}') from exc
    if proc.returncode != 0:
        detail = ((proc.stderr or '') + (proc.stdout or '')).strip()
        raise RuntimeError(f'gh command failed with rc={proc.returncode}: {detail}')
    return proc


def _gh_check_authenticated():
    run_gh(['auth', 'status'])


def _gh_pr_create(base_branch, branch, title, body_argv):
    proc = run_gh(['pr', 'create', '--base', base_branch, '--head', branch,
                   '--title', title, *body_argv])
    out = (proc.stdout or '').strip()
    match = re.search(r'https?://\S*/pull/(\d+)\S*', out)
    if not out or not match:
        raise RuntimeError('gh pr create returned no parseable pull request URL')
    return out, int(match.group(1))


def _gh_pr_view(pr_number):
    proc = run_gh(['pr', 'view', str(pr_number), '--json', 'state,mergedAt,mergeCommit'])
    try:
        data = json.loads(proc.stdout or '')
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f'gh pr view returned unparseable JSON: {exc}'
        ) from exc
    if not isinstance(data, dict) or data.get('state') not in ('OPEN', 'MERGED', 'CLOSED'):
        raise RuntimeError(
            f'gh pr view state is not a recognized PR state: {data.get("state")!r}'
        )
    state = data['state']
    merged_at = data.get('mergedAt') or None
    merge_commit = data.get('mergeCommit')
    merge_oid = merge_commit.get('oid') if isinstance(merge_commit, dict) else None
    if state == 'MERGED' and (not merged_at or not merge_oid):
        raise RuntimeError(
            'gh reports MERGED without mergedAt/mergeCommit metadata; '
            'refusing to guess the merge style'
        )
    return state, merged_at, merge_oid


def _origin_head(remote, branch):
    """Read-only remote head lookup (ls-remote never updates local refs)."""
    result = git('ls-remote', '--heads', remote, branch, cwd=runtime_root(), check=False)
    for line in (result.stdout or '').splitlines():
        parts = line.split()
        if len(parts) >= 2:
            return parts[0]
    return None


def _task_identity(task):
    """Shared plan/publish guard chain mirroring worktree.publish L788-828."""
    meta = _load_lock(task)
    path = _safe_worktree(task)
    if not path.is_dir():
        raise ValueError('assigned task worktree does not exist')
    current_branch = git('branch', '--show-current', cwd=path).stdout.strip()
    if current_branch != meta['branch']:
        raise ValueError(
            f'assigned worktree branch mismatch: expected {meta["branch"]}, got {current_branch}'
        )
    for key in ('branch', 'integration_branch', 'base_commit'):
        if not meta.get(key):
            raise ValueError(
                f'writer lock lacks {key}; '
                'recreate the task worktree with the current harness before publishing'
            )
    root = runtime_root()
    root_branch = git('branch', '--show-current', cwd=root).stdout.strip()
    if root_branch != meta['integration_branch']:
        raise ValueError(
            f'canonical integration branch changed: '
            f'expected {meta["integration_branch"]}, got {root_branch}'
        )
    branch_head = git('rev-parse', 'HEAD', cwd=path).stdout.strip()
    incorporated = git(
        'merge-base', '--is-ancestor', 'HEAD', branch_head, cwd=root, check=False
    )
    if incorporated.returncode != 0:
        raise ValueError(
            'canonical HEAD is not incorporated into the task branch; '
            'publication requires explicit reconciliation'
        )
    integration_head = git('rev-parse', 'HEAD', cwd=root).stdout.strip()
    allowed = _authorized_surface(task)
    changed = _candidate_files(path, integration_head)
    _assert_authorized_surface(changed, allowed)
    _assert_scope_expansion_covers_changes(task, changed)
    if not changed:
        raise ValueError('worktree has no task changes to publish')
    return meta, path, changed, integration_head, root


def _plan_output(task, policy):
    meta, path, changed, _integration_head, _root = _task_identity(task)
    return {
        'schema_version': 1,
        'task_id': task,
        'status': 'READY',
        'worktree': str(path),
        'branch': meta['branch'],
        'integration_branch': meta['integration_branch'],
        'base_commit': meta['base_commit'],
        'changed_files': changed,
        'mode': 'pr',
        'remote': policy['remote'],
        'base_branch': policy['base_branch'],
        'would_push': meta['branch'],
        'pr_title': f'harness(task): publish {task}',
    }


def plan(task, execute=False):
    """Read-only publication plan; never contacts gh and never mutates state."""
    task = safe_task_id(task)
    policy = _policy()
    _publication_preconditions(task)
    return _plan_output(task, policy)


def _refuse_if_already_published(task):
    """Idempotence gate: fail closed when this task is already in the PR flow.

    Runs BEFORE any push or pr create (pinned by the test design).
    """
    pr_path = run_dir(task) / 'pull-request.json'
    if pr_path.is_file():
        try:
            pr = json.loads(pr_path.read_text(encoding='utf-8'))
        except Exception as exc:
            raise ValueError(f'existing pull-request.json is unreadable: {exc}') from exc
        if isinstance(pr, dict) and pr.get('state') in PR_STATES:
            raise ValueError(
                'already published in PR mode; use finalize '
                f'(pull-request.json state={pr.get("state")})'
            )
    pub = publish_artifact(task)
    if pub.is_file():
        try:
            data = json.loads(pub.read_text(encoding='utf-8'))
        except Exception as exc:
            raise ValueError(f'existing publish.json is unreadable: {exc}') from exc
        if isinstance(data, dict) and data.get('status') in ('INTEGRATED', 'PASS'):
            raise ValueError(
                'already published in PR mode; use finalize '
                f'(publish.json status={data.get("status")})'
            )


def _render_body(task, meta, candidate, committed):
    return (
        'Task publication provenance\n'
        f'task_id: {task}\n'
        f'branch: {meta["branch"]}\n'
        f'base_commit: {meta["base_commit"]}\n'
        f'task_commit: {candidate}\n'
        f'integration_branch: {meta["integration_branch"]}\n'
        f'changed_files: {", ".join(committed)}\n'
    )


def _publish(task, execute, title, body):
    task = safe_task_id(task)
    policy = _policy()
    _refuse_if_already_published(task)

    # Resolve operator input before any external work; a bad --body path must
    # fail closed before a push (value is handed to gh via --body-file).
    final_title = title or f'harness(task): publish {task}'
    if body:
        try:
            Path(body).read_text(encoding='utf-8')
        except OSError as exc:
            raise ValueError(f'operator body file is unreadable: {exc}') from exc
        body_argv = ['--body-file', str(body)]
    else:
        body_argv = ['--body', None]  # rendered later, once commits exist

    _publication_preconditions(task)
    if not execute:
        # Same contract as `plan`: would-be plan JSON, zero side effects.
        return _plan_output(task, policy)

    # gh must be available and authenticated BEFORE any git mutation.
    _gh_check_authenticated()
    meta, path, changed, integration_head, root = _task_identity(task)

    if _root_has_tracked_changes():
        raise ValueError(
            'canonical repository has tracked local changes; '
            'commit or clean them before publication'
        )
    overlap = _root_untracked_overlap(changed)
    if overlap:
        raise ValueError(
            'canonical repository has untracked files overlapping the task '
            'publication surface: ' + ', '.join(overlap)
        )

    allowed = _authorized_surface(task)
    dirty = _dirty_files(path)
    _assert_authorized_surface(dirty, allowed)
    _assert_scope_expansion_covers_changes(task, dirty)
    if dirty:
        git('add', '-A', '--', *dirty, cwd=path)
        staged = sorted(
            line.strip().replace('\\', '/')
            for line in git(
                'diff', '--cached', '--no-renames', '--name-only', '--', cwd=path
            ).stdout.splitlines()
            if line.strip()
        )
        _assert_authorized_surface(staged, allowed)
        if staged:
            git('commit', '-m', f'harness(task): publish {task}', cwd=path)

    candidate = git('rev-parse', 'HEAD', cwd=path).stdout.strip()
    committed = _committed_files(path, integration_head)
    _assert_authorized_surface(committed, allowed)
    if candidate == integration_head or not committed:
        raise ValueError('publication produced no committed task change')

    if body_argv[0] == '--body':
        body_argv[1] = _render_body(task, meta, candidate, committed)

    # Push is the first irreversible external step; refuse closed on failure
    # (remote branch collision) and never force-push.
    try:
        git('push', '-u', 'origin', meta['branch'], cwd=root)
    except (subprocess.CalledProcessError, OSError) as exc:
        raise ValueError(
            f'pushing {meta["branch"]} to origin failed; no force-push is performed. '
            'If the remote branch collides with a stale publication attempt, '
            'delete the stale remote branch on GitHub first: '
            f'{exc}'
        ) from exc

    pr_url, pr_number = _gh_pr_create(
        policy['base_branch'], meta['branch'], final_title, body_argv
    )

    pr_record = {
        'schema_version': 1,
        'state': 'OPEN',
        'task_id': task,
        'branch': meta['branch'],
        'integration_branch': meta['integration_branch'],
        'base_commit': meta['base_commit'],
        'task_commit': candidate,
        'pr_url': pr_url,
        'pr_number': pr_number,
        'pr_title': final_title,
        'changed_files': committed,
    }
    write_json_atomic(run_dir(task) / 'pull-request.json', pr_record)

    integrated = {
        'schema_version': 1,
        'task_id': task,
        'status': 'INTEGRATED',
        'worktree': str(path),
        'branch': meta['branch'],
        'integration_branch': meta['integration_branch'],
        'base_commit': meta['base_commit'],
        'commit': candidate,
        'changed_files': committed,
    }
    write_json_atomic(publish_artifact(task), integrated)
    return pr_record


def publish(task, execute=False, title=None, body=None):
    return _publish(task, execute, title, body)


def status(task, execute=False):
    """Read-only PR state report backed by gh live data; writes nothing."""
    del execute  # status is read-only with and without --execute
    task = safe_task_id(task)
    policy = _policy()
    pr_path = run_dir(task) / 'pull-request.json'
    if not pr_path.is_file():
        raise ValueError('pull-request.json missing; publication in PR mode has not started')
    try:
        pr = json.loads(pr_path.read_text(encoding='utf-8'))
    except Exception as exc:
        raise ValueError(f'pull-request.json is unreadable: {exc}') from exc
    if not isinstance(pr, dict) or pr.get('task_id') != task:
        raise ValueError('pull-request.json task_id mismatch')

    state, merged_at, merge_oid = _gh_pr_view(pr.get('pr_number'))
    origin_head = _origin_head(policy['remote'], pr['integration_branch'])
    return {
        'task_id': task,
        'mode': 'pr',
        'state': state,
        'pr_url': pr.get('pr_url'),
        'pr_number': pr.get('pr_number'),
        'pr_title': pr.get('pr_title'),
        'task_commit': pr.get('task_commit'),
        'merged_at': merged_at,
        'merge_commit_oid': merge_oid,
        'merge_style': 'merge-commit' if (merge_oid and merge_oid == origin_head) else 'unknown',
        'origin_head': origin_head,
        'published': bool(publish_status(task).get('published')),
    }


def finalize(task, execute=False):
    """Complete the PR publication after a human merge on GitHub."""
    task = safe_task_id(task)
    policy = _policy()
    pr_path = run_dir(task) / 'pull-request.json'
    if not pr_path.is_file():
        raise ValueError('pull-request.json missing; finalize requires a published open pull request')
    try:
        pr = json.loads(pr_path.read_text(encoding='utf-8'))
    except Exception as exc:
        raise ValueError(f'pull-request.json is unreadable: {exc}') from exc
    if not isinstance(pr, dict) or pr.get('task_id') != task:
        raise ValueError('pull-request.json task_id mismatch')
    if pr.get('state') != 'OPEN':
        raise ValueError(
            f'pull-request.json state is {pr.get("state")!r}; finalize requires state OPEN'
        )
    pub_path = publish_artifact(task)
    if not pub_path.is_file():
        raise ValueError(
            'publish.json missing; finalize requires publish.json status '
            'INTEGRATED from the PR publication'
        )
    try:
        integrated = json.loads(pub_path.read_text(encoding='utf-8'))
    except Exception as exc:
        raise ValueError(f'publish.json is unreadable: {exc}') from exc
    if not isinstance(integrated, dict) or integrated.get('status') != 'INTEGRATED':
        raise ValueError(
            f'publish.json status is {integrated.get("status")!r}; '
            'finalize requires INTEGRATED (PASS publications belong to the legacy flow)'
        )
    if integrated.get('commit') != pr.get('task_commit'):
        raise ValueError(
            'publish.json commit does not match pull-request.json task_commit'
        )

    if not execute:
        return {
            'task_id': task,
            'mode': 'pr',
            'action': 'finalize',
            'execute': False,
            'pr_url': pr.get('pr_url'),
            'pr_number': pr.get('pr_number'),
            'task_commit': pr.get('task_commit'),
            'base_branch': policy['base_branch'],
            'ready_for_execute': True,
        }

    # gh must confirm the human merge BEFORE any canonical mutation.
    state, merged_at, merge_oid = _gh_pr_view(pr.get('pr_number'))
    if state != 'MERGED':
        raise ValueError(
            f'PR not merged yet; human merge required on GitHub (gh state={state})'
        )
    base = policy['base_branch']
    root = runtime_root()

    try:
        git('fetch', 'origin', cwd=root)
    except (subprocess.CalledProcessError, OSError) as exc:
        raise ValueError(f'git fetch origin failed; refusing finalize: {exc}') from exc

    # Squash merges are incompatible with the finish gate contract: refuse
    # closed when the task commit did not survive the merge.
    ancestor = git(
        'merge-base', '--is-ancestor', pr['task_commit'], f'origin/{base}',
        cwd=root, check=False,
    )
    if ancestor.returncode != 0:
        raise ValueError(
            f'task commit is not an ancestor of origin/{base} (squash merge); '
            'merge the PR with a merge commit or rebase merge, or re-run '
            'publication in fast_forward mode via scripts/worktree.py publish'
        )

    branch = git('branch', '--show-current', cwd=root).stdout.strip()
    if branch != pr['integration_branch']:
        raise ValueError(
            f'canonical repository is on {branch!r}; '
            f'finalize requires {pr["integration_branch"]!r} checked out'
        )

    try:
        git('merge', '--ff-only', f'origin/{base}', cwd=root)
    except (subprocess.CalledProcessError, OSError) as exc:
        raise ValueError(
            f'canonical {pr["integration_branch"]} diverged from origin/{base}; '
            'reconcile manually and re-run finalize (no force update performed): '
            f'{exc}'
        ) from exc

    still = git(
        'merge-base', '--is-ancestor', pr['task_commit'], 'HEAD', cwd=root, check=False
    )
    if still.returncode != 0:
        raise ValueError(
            'task commit is not an ancestor of canonical HEAD after update; '
            'aborting finalize'
        )

    # Release worktree + lock and rewrite publish.json with status PASS,
    # mirroring worktree._finalize_integrated_artifact's document shape (the
    # commit stays the task commit; canonical HEAD advanced past it via the
    # human merge, which is exactly what the finish gate expects).
    meta = dict(integrated)
    cleanup = _cleanup_published_worktree(task, meta)
    meta['status'] = 'PASS'
    meta['cleaned_up'] = cleanup['cleaned_up']
    if cleanup['cleanup_warning']:
        meta['cleanup_warning'] = cleanup['cleanup_warning']
    else:
        meta.pop('cleanup_warning', None)
    meta['published_at'] = dt.datetime.now(dt.timezone.utc).isoformat()
    write_json_atomic(publish_artifact(task), meta)

    pr['state'] = 'FINALIZED'
    pr['merged_at'] = merged_at
    pr['merge_commit'] = merge_oid
    write_json_atomic(pr_path, pr)

    checked = publish_status(task)
    if not checked.get('published'):
        raise ValueError(
            'finalize completed but publish_status still reports unpublished: '
            f'{checked.get("reason")!r}'
        )
    return meta


def main():
    ap = argparse.ArgumentParser(
        description='Blocking pull-request publication flow (policy mode: pr)'
    )
    sub = ap.add_subparsers(dest='cmd', required=True)

    p = sub.add_parser('plan')
    p.add_argument('task')
    p.add_argument('--execute', action='store_true')

    p = sub.add_parser('publish')
    p.add_argument('task')
    p.add_argument('--execute', action='store_true')
    p.add_argument('--title', help='pull request title override')
    p.add_argument('--body', help='path to an operator-authored body file (passed to gh via --body-file)')

    p = sub.add_parser('status')
    p.add_argument('task')
    p.add_argument('--execute', action='store_true')

    p = sub.add_parser('finalize')
    p.add_argument('task')
    p.add_argument('--execute', action='store_true')

    args = ap.parse_args()
    if args.cmd == 'plan':
        out = plan(args.task, args.execute)
    elif args.cmd == 'publish':
        out = publish(args.task, args.execute, args.title, args.body)
    elif args.cmd == 'status':
        out = status(args.task, args.execute)
    else:
        out = finalize(args.task, args.execute)
    print(json.dumps(out, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
