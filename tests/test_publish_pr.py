"""Behavioral tests for the opt-in blocking pull-request publication flow.

Two-repo origin model (pinned by the test design):
  <tmp>/repo       canonical checkout; registers <tmp>/origin.git as remote 'origin'
  <tmp>/origin.git bare remote standing in for GitHub (push/fetch/ls-remote oracle)
  <tmp>/clone      human merge actor: fetches origin, merges (or squash-emulates)
                   agent/<task> into its local main and pushes main back to origin.git

Oracles: real git for every git state; FakeGh stands in ONLY for the gh binary
with one scripted response per gh subcommand and an exact argv call log.
publish_pr.git and worktree.git are patched separately because importing names
does not share the patch target. unittest only; no monkeypatch library;
no network access.
"""

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

import harnesslib
import publish_pr
import worktree

PR_URL = 'https://github.com/example/repo/pull/7'
MERGED_AT = '2026-10-12T09:00:00Z'
OPEN_VIEW = {'state': 'OPEN', 'mergedAt': None, 'mergeCommit': None}
MUTATING_GIT_VERBS = {
    'add', 'commit', 'push', 'pull', 'rebase', 'revert', 'reset', 'rm',
    'clean', 'cherry-pick', 'update-ref', 'stash', 'merge', 'fetch',
}


class RecordingGit:
    """Passthrough to the real git binary that records every invocation."""

    def __init__(self):
        self.calls = []

    def __call__(self, *args, cwd=None, check=True):
        self.calls.append(tuple(args))
        proc = subprocess.run(
            ['git', *args], cwd=str(cwd) if cwd else None, text=True,
            encoding='utf-8', errors='replace', capture_output=True,
        )
        if check and proc.returncode != 0:
            raise subprocess.CalledProcessError(
                proc.returncode, ['git', *args], proc.stdout, proc.stderr
            )
        return proc


class FakeGh:
    """Scripted, network-free stand-in for the gh binary.

    Every invocation is recorded in ``calls`` as the exact argv list. One
    scripted response per gh subcommand; unexpected subcommands fail loudly so
    accidental gh work fails the test instead of passing silently.
    """

    def __init__(self, pr_create_stdout=PR_URL + '\n', view_payload=None, view_stdout=None):
        self.calls = []
        self._create_stdout = pr_create_stdout
        self._view_payload = OPEN_VIEW if view_payload is None else view_payload
        self._view_stdout = view_stdout

    def __call__(self, args):
        self.calls.append(list(args))
        if args[:2] == ['auth', 'status']:
            return subprocess.CompletedProcess(args, 0, stdout='', stderr='')
        if args[:2] == ['pr', 'create']:
            return subprocess.CompletedProcess(args, 0, stdout=self._create_stdout, stderr='')
        if args[:2] == ['pr', 'view']:
            payload = self._view_stdout if self._view_stdout is not None else json.dumps(self._view_payload)
            return subprocess.CompletedProcess(args, 0, stdout=payload, stderr='')
        raise AssertionError(f'unexpected gh invocation: {args!r}')

    def invoked(self, verb):
        return [args for args in self.calls if args[:2] == [verb[0], verb[1]]]

    def pr_create_args(self):
        hits = self.invoked(('pr', 'create'))
        if len(hits) != 1:
            raise AssertionError(f'expected exactly one pr create call, got {hits!r}')
        return hits[0]

    def pr_create_body(self):
        args = self.pr_create_args()
        if '--body' in args:
            return args[args.index('--body') + 1]
        if '--body-file' in args:
            return Path(args[args.index('--body-file') + 1]).read_text(encoding='utf-8')
        raise AssertionError(f'pr create argv carries no --body/--body-file: {args!r}')


class PublishPrFlowTests(unittest.TestCase):
    task = 'TASKT-001'

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.repo = base / 'repo'
        self.origin = base / 'origin.git'
        self.clone = base / 'clone'
        for directory in (self.repo, self.origin, self.clone):
            directory.mkdir()
        self._git(self.repo, 'init', '-b', 'main')
        self._git(self.repo, 'config', 'user.email', 'harness@test.invalid')
        self._git(self.repo, 'config', 'user.name', 'Harness Test')
        (self.repo / 'README.md').write_text('base\n', encoding='utf-8')
        self._git(self.repo, 'add', 'README.md')
        self._git(self.repo, 'commit', '-m', 'base')
        self._git(self.origin, 'init', '--bare', '-b', 'main')
        self._git(self.clone, 'init', '-b', 'main')
        self._git(self.clone, 'config', 'user.email', 'human@test.invalid')
        self._git(self.clone, 'config', 'user.name', 'Human Merger')
        self._git(self.repo, 'remote', 'add', 'origin', str(self.origin))
        self._git(self.clone, 'remote', 'add', 'origin', str(self.origin))
        self._git(self.repo, 'push', '-u', 'origin', 'main')
        self._git(self.clone, 'fetch', 'origin')
        self._git(self.clone, 'checkout', '-B', 'main', 'origin/main')
        self._git(self.clone, 'branch', '--set-upstream-to=origin/main', 'main')
        shutil.copytree(ROOT / 'harness', self.repo / 'harness')

        self.old_worktree_root = worktree.ROOT
        self.old_harnesslib_root = harnesslib.ROOT
        worktree.ROOT = self.repo
        harnesslib.ROOT = self.repo
        self.base_commit = self.real('rev-parse', 'HEAD', cwd=self.repo)
        self._set_policy('pr')

    def tearDown(self):
        worktree.ROOT = self.old_worktree_root
        harnesslib.ROOT = self.old_harnesslib_root
        self.tmp.cleanup()

    # -- fixture helpers ----------------------------------------------------

    def _git(self, cwd, *args):
        subprocess.run(
            ['git', *args], cwd=str(cwd), text=True, encoding='utf-8',
            errors='replace', capture_output=True, check=True,
        )

    def real(self, *args, cwd):
        proc = subprocess.run(
            ['git', *args], cwd=str(cwd), text=True, encoding='utf-8',
            errors='replace', capture_output=True, check=True,
        )
        return proc.stdout.strip()

    def _set_policy(self, mode):
        path = self.repo / 'harness' / 'publish-policy.json'
        if mode is None:
            path.unlink(missing_ok=True)
            return
        path.write_text(json.dumps({
            'schema_version': 1,
            'mode': mode,
            'pr': {'remote': 'origin', 'base_branch': 'main'},
        }), encoding='utf-8')

    def _runtime(self, files):
        rd = harnesslib.run_dir(self.task)
        rd.mkdir(parents=True, exist_ok=True)
        (rd / 'route.json').write_text(json.dumps({
            'task_id': self.task,
            'risk': 'R1',
            'agents': ['implementer', 'reviewer'],
            'human_gate': False,
            'isolation': 'worktree',
            'tdd': {},
        }), encoding='utf-8')
        (rd / 'progress.json').write_text(json.dumps({
            'task_id': self.task,
            'risk': 'R1',
            'state': 'RUNNING',
            'current_step': 'CLOSE',
            'steps': ['WORKTREE', 'IMPLEMENT', 'CHECKS', 'REVIEW', 'CLOSE'],
            'completed': ['WORKTREE', 'IMPLEMENT', 'CHECKS', 'REVIEW'],
        }), encoding='utf-8')
        (rd / 'task.json').write_text(json.dumps({
            'id': self.task,
            'files': files,
        }), encoding='utf-8')

    def _fixture_task(self):
        if getattr(self, 'path', None) is not None:
            return self.path
        self._runtime(['src/app.py'])
        created = worktree.create(self.task, execute=True)
        self.base_commit = created['base_commit']
        self.branch = created['branch']
        path = Path(created['worktree'])
        (path / 'src').mkdir(parents=True)
        (path / 'src/app.py').write_text('VALUE = 1\n', encoding='utf-8')
        self.path = path
        return path

    def _mocked(self, run_gh_side_effect):
        """Patch publish_pr.git + worktree.git (separate bindings) and gh."""
        stack = ExitStack()
        self.addCleanup(stack.close)
        publish_git = RecordingGit()
        worktree_git = RecordingGit()
        stack.enter_context(patch.object(publish_pr, 'git', side_effect=publish_git))
        stack.enter_context(patch.object(worktree, 'git', side_effect=worktree_git))
        stack.enter_context(patch.object(publish_pr, 'run_gh', side_effect=run_gh_side_effect))
        stack.enter_context(patch(
            'gate.finish_decision',
            return_value={'allow': True, 'missing': [], 'failing': []},
        ))
        return publish_git, worktree_git

    def _publish_success(self, **kwargs):
        self._fixture_task()
        self.lock_bytes_before = worktree._safe_lock(self.task).read_bytes()
        fake = FakeGh()
        publish_git, worktree_git = self._mocked(fake)
        out = publish_pr.publish(self.task, execute=True, **kwargs)
        return fake, publish_git, worktree_git, out

    def _human_merge(self, squash=False):
        """Emulate the human merge actor: integrate agent branch into origin main."""
        self._git(self.clone, 'fetch', 'origin')
        if squash:
            self._git(self.clone, 'cherry-pick', '--no-commit', f'origin/{self.branch}')
            self._git(self.clone, 'commit', '-m', 'squash merge emulation')
        else:
            self._git(self.clone, 'merge', '--no-ff', '--no-edit', f'origin/{self.branch}')
        self._git(self.clone, 'push', 'origin', 'main')
        self.origin_main = self.real('ls-remote', 'origin', 'refs/heads/main', cwd=self.repo).split()[0]

    def merged_view(self):
        return {
            'state': 'MERGED',
            'mergedAt': MERGED_AT,
            'mergeCommit': {'oid': self.origin_main},
        }

    def _snapshot_run_dir(self):
        rd = harnesslib.run_dir(self.task)
        if not rd.is_dir():
            return {}
        return {
            str(p.relative_to(rd)): (
                p.stat().st_size,
                hashlib.sha256(p.read_bytes()).hexdigest(),
            )
            for p in sorted(rd.rglob('*')) if p.is_file()
        }

    def _artifact_bytes(self, name):
        p = harnesslib.run_dir(self.task) / name
        return p.read_bytes() if p.is_file() else None

    def _assert_read_only_git(self, *logs):
        for log in logs:
            for call in log:
                self.assertNotIn(call[0], MUTATING_GIT_VERBS, call)
                if call[0] == 'branch':
                    self.assertEqual(call[1], '--show-current', call)

    def _assert_invariants(self, worktree_present=True):
        if worktree_present:
            self.assertTrue(self.path.is_dir())
            self.assertTrue(worktree._safe_lock(self.task).is_file())

    # -- B01: flag-off regression -------------------------------------------

    def test_b01_flag_off_refuses_pr_flow_and_legacy_publish_is_unchanged(self):
        self._fixture_task()
        # Flag off (policy file removed): legacy fast-forward publish is byte-identical.
        self._set_policy(None)
        with patch('gate.finish_decision', return_value={'allow': True, 'missing': [], 'failing': []}):
            result = worktree.publish(self.task, execute=True)
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual(result['changed_files'], ['src/app.py'])
        self.assertTrue((self.repo / 'src/app.py').is_file())
        self.assertFalse(self.path.exists())
        self.assertFalse(worktree._safe_lock(self.task).is_file())
        head = self.real('rev-parse', 'HEAD', cwd=self.repo)
        self.assertEqual(head, result['commit'])
        # canonical main fast-forwarded past the base (cleanup deleted the
        # local task branch, so prove the integration via ancestry instead).
        self.assertNotEqual(head, self.base_commit)
        ancestor = subprocess.run(
            ['git', 'merge-base', '--is-ancestor', self.base_commit, 'HEAD'],
            cwd=str(self.repo), capture_output=True, text=True, check=False,
        )
        self.assertEqual(ancestor.returncode, 0)
        self.assertTrue(worktree.publish_status(self.task)['published'])
        data = json.loads(worktree.publish_artifact(self.task).read_text(encoding='utf-8'))
        core = {k: v for k, v in data.items() if k != 'published_at'}
        self.assertEqual(core, {
            'schema_version': 1,
            'task_id': self.task,
            'status': 'PASS',
            'worktree': str(self.path),
            'branch': self.branch,
            'integration_branch': 'main',
            'base_commit': self.base_commit,
            'commit': head,
            'changed_files': ['src/app.py'],
            'cleaned_up': True,
        })
        self.assertTrue(data.get('published_at'))

        # publish_pr refuses while the flag is off (file absent)
        self._set_policy(None)
        fake = FakeGh()
        publish_git, worktree_git = self._mocked(fake)
        with self.assertRaisesRegex(ValueError, 'publish policy|mode is fast_forward'):
            publish_pr.publish(self.task, execute=True)
        self.assertEqual(fake.calls, [])
        self._assert_read_only_git(publish_git.calls)

        # and refuses with an explicit fast_forward mode as well
        self._set_policy('fast_forward')
        with self.assertRaisesRegex(ValueError, 'publish policy|mode is fast_forward'):
            publish_pr.publish(self.task, execute=False)

    # -- B02: plan is read-only ---------------------------------------------

    def test_b02_plan_prints_contract_without_side_effects(self):
        path = self._fixture_task()
        before = self._snapshot_run_dir()
        fake = FakeGh()
        publish_git, worktree_git = self._mocked(fake)
        plan = publish_pr.plan(self.task)
        self.assertEqual(plan, {
            'schema_version': 1,
            'task_id': self.task,
            'status': 'READY',
            'worktree': str(path),
            'branch': self.branch,
            'integration_branch': 'main',
            'base_commit': self.base_commit,
            'changed_files': ['src/app.py'],
            'mode': 'pr',
            'remote': 'origin',
            'base_branch': 'main',
            'would_push': self.branch,
            'pr_title': f'harness(task): publish {self.task}',
        })
        self.assertEqual(fake.calls, [])
        self.assertEqual(self._snapshot_run_dir(), before)
        self._assert_read_only_git(publish_git.calls, worktree_git.calls)
        self.assertIsNone(self._artifact_bytes('publish.json'))
        self.assertIsNone(self._artifact_bytes('pull-request.json'))
        # no pushed refs, no new commits
        self.assertEqual(
            self.real('ls-remote', 'origin', f'refs/heads/{self.branch}', cwd=self.repo),
            '',
        )

    # -- B03: publish --execute happy path -----------------------------------

    def test_b03_publish_records_open_pr_and_defers_integration(self):
        path = self._fixture_task()
        fake = FakeGh()
        publish_git, worktree_git = self._mocked(fake)
        out = publish_pr.publish(self.task, execute=True)

        candidate = self.real('rev-parse', 'HEAD', cwd=path)
        self.assertEqual(out['task_commit'], candidate)
        pr = json.loads(
            (harnesslib.run_dir(self.task) / 'pull-request.json').read_text(encoding='utf-8')
        )
        self.assertEqual(pr, {
            'schema_version': 1,
            'state': 'OPEN',
            'task_id': self.task,
            'branch': self.branch,
            'integration_branch': 'main',
            'base_commit': self.base_commit,
            'task_commit': candidate,
            'pr_url': PR_URL,
            'pr_number': 7,
            'pr_title': f'harness(task): publish {self.task}',
            'changed_files': ['src/app.py'],
        })
        pub = json.loads(worktree.publish_artifact(self.task).read_text(encoding='utf-8'))
        self.assertEqual(pub, {
            'schema_version': 1,
            'task_id': self.task,
            'status': 'INTEGRATED',
            'worktree': str(path),
            'branch': self.branch,
            'integration_branch': 'main',
            'base_commit': self.base_commit,
            'commit': candidate,
            'changed_files': ['src/app.py'],
        })

        # Real-git oracle: task branch commit tagged with the publish message.
        self.assertEqual(
            self.real('log', '-1', '--format=%s', cwd=path),
            f'harness(task): publish {self.task}',
        )
        # Origin has the pushed task branch; canonical main NOT advanced.
        self.assertEqual(
            self.real('ls-remote', 'origin', f'refs/heads/{self.branch}', cwd=self.repo).split()[0],
            candidate,
        )
        self.assertEqual(
            self.real('ls-remote', 'origin', 'refs/heads/main', cwd=self.repo).split()[0],
            self.base_commit,
        )
        self.assertEqual(self.real('rev-parse', 'HEAD', cwd=self.repo), self.base_commit)
        # NO worktree removal, NO lock release, publish_status not published.
        self.assertTrue(path.is_dir())
        self.assertTrue(worktree._safe_lock(self.task).is_file())
        status = worktree.publish_status(self.task)
        self.assertFalse(status['published'], status)

        # FakeGh call log: exactly one auth check + one pr create; no pr view.
        self.assertEqual(
            [args[:2] for args in fake.calls],
            [['auth', 'status'], ['pr', 'create']],
        )
        create_args = fake.pr_create_args()
        self.assertEqual(create_args[create_args.index('--base') + 1], 'main')
        self.assertEqual(create_args[create_args.index('--head') + 1], self.branch)
        self.assertEqual(
            create_args[create_args.index('--title') + 1],
            f'harness(task): publish {self.task}',
        )

    # -- B04: title/body contract --------------------------------------------

    def test_b04_publish_uses_operator_body_file(self):
        self._fixture_task()
        body_path = Path(self.tmp.name) / 'body.md'
        body_path.write_text(
            f'Owner-authored body for {self.task}\n'
            f'branch {self.branch} base {self.base_commit}\n',
            encoding='utf-8',
        )
        fake, publish_git, worktree_git, out = self._publish_success(body=str(body_path))
        create_args = fake.pr_create_args()
        self.assertIn('--body-file', create_args)
        self.assertEqual(create_args[create_args.index('--body-file') + 1], str(body_path))
        body = fake.pr_create_body()
        self.assertIn(self.task, body)
        self.assertIn(self.branch, body)
        self.assertIn(self.base_commit, body)

    def test_b04b_publish_renders_default_provenance_body(self):
        fake, publish_git, worktree_git, out = self._publish_success()
        create_args = fake.pr_create_args()
        self.assertIn('--body', create_args)
        body = fake.pr_create_body()
        self.assertIn(self.task, body)
        self.assertIn(self.branch, body)
        self.assertIn(self.base_commit, body)
        self.assertIn(out['task_commit'], body)
        self.assertIn('src/app.py', body)

    # -- B05: status read-only ------------------------------------------------

    def test_b05_status_reports_open_then_merged_read_only(self):
        self._publish_success()
        before = self._snapshot_run_dir()
        fake = FakeGh()
        publish_git, worktree_git = self._mocked(fake)
        report = publish_pr.status(self.task)
        self.assertEqual(fake.calls, [['pr', 'view', '7', '--json', 'state,mergedAt,mergeCommit']])
        self.assertEqual(report['state'], 'OPEN')
        self.assertEqual(report['merge_style'], 'unknown')
        self.assertFalse(report['published'])
        self.assertEqual(self._snapshot_run_dir(), before)
        self._assert_read_only_git(publish_git.calls, worktree_git.calls)
        self.assertEqual(self.real('rev-parse', 'HEAD', cwd=self.repo), self.base_commit)

        self._human_merge()
        fake2 = FakeGh(view_payload=self.merged_view())
        publish_git2, worktree_git2 = self._mocked(fake2)
        report2 = publish_pr.status(self.task)
        self.assertEqual(fake2.calls, [['pr', 'view', '7', '--json', 'state,mergedAt,mergeCommit']])
        self.assertEqual(report2['state'], 'MERGED')
        self.assertEqual(report2['merge_commit_oid'], self.origin_main)
        self.assertEqual(report2['merge_style'], 'merge-commit')
        # finalize has not run yet: publication is still not complete
        self.assertFalse(worktree.publish_status(self.task)['published'])
        # status must not fetch or move local main
        self.assertEqual(self.real('rev-parse', 'HEAD', cwd=self.repo), self.base_commit)
        self._assert_read_only_git(publish_git2.calls, worktree_git2.calls)
        self.assertEqual(self._snapshot_run_dir(), before)

    # -- B06: finalize happy path ---------------------------------------------

    def test_b06_finalize_after_human_merge_completes_publication(self):
        self._publish_success()
        task_commit = self.real('rev-parse', 'HEAD', cwd=self.path)
        self._human_merge()
        fake = FakeGh(view_payload=self.merged_view())
        publish_git, worktree_git = self._mocked(fake)
        out = publish_pr.finalize(self.task, execute=True)

        self.assertEqual(out['status'], 'PASS')
        self.assertEqual(fake.calls, [['pr', 'view', '7', '--json', 'state,mergedAt,mergeCommit']])
        data = json.loads(worktree.publish_artifact(self.task).read_text(encoding='utf-8'))
        core = {k: v for k, v in data.items() if k != 'published_at'}
        self.assertEqual(core, {
            'schema_version': 1,
            'task_id': self.task,
            'status': 'PASS',
            'worktree': str(self.path),
            'branch': self.branch,
            'integration_branch': 'main',
            'base_commit': self.base_commit,
            'commit': task_commit,
            'changed_files': ['src/app.py'],
            'cleaned_up': True,
        })
        self.assertTrue(data.get('published_at'))
        pr = json.loads(
            (harnesslib.run_dir(self.task) / 'pull-request.json').read_text(encoding='utf-8')
        )
        self.assertEqual(pr['state'], 'FINALIZED')
        self.assertEqual(pr['merged_at'], MERGED_AT)
        self.assertEqual(pr['merge_commit'], self.origin_main)
        self.assertFalse(self.path.exists())
        self.assertFalse(worktree._safe_lock(self.task).is_file())
        self.assertEqual(
            worktree.publish_status(self.task),
            {
                'task_id': self.task,
                'published': True,
                'commit': task_commit,
                'integration_branch': 'main',
                'changed_files': ['src/app.py'],
            },
        )
        # canonical main was fast-forwarded to the human merge commit
        self.assertEqual(self.real('rev-parse', 'HEAD', cwd=self.repo), self.origin_main)
        self.assertIn(('fetch', 'origin'), [call[:2] for call in publish_git.calls])

    # -- B07/B08/B09: finalize refusals ---------------------------------------

    def test_b07_finalize_refuses_unmerged_pr(self):
        self._publish_success()
        before_pub = self._artifact_bytes('publish.json')
        before_pr = self._artifact_bytes('pull-request.json')
        snapshot = self._snapshot_run_dir()
        fake = FakeGh(view_payload=OPEN_VIEW)
        publish_git, worktree_git = self._mocked(fake)
        with self.assertRaisesRegex(ValueError, 'not merged|human merge required'):
            publish_pr.finalize(self.task, execute=True)
        self.assertEqual(fake.calls, [['pr', 'view', '7', '--json', 'state,mergedAt,mergeCommit']])
        # no repo mutation at all
        self._assert_read_only_git(publish_git.calls, worktree_git.calls)
        self.assertEqual(self._artifact_bytes('publish.json'), before_pub)
        self.assertEqual(self._artifact_bytes('pull-request.json'), before_pr)
        self.assertEqual(self._snapshot_run_dir(), snapshot)
        self.assertEqual(self.real('rev-parse', 'HEAD', cwd=self.repo), self.base_commit)
        self._assert_invariants()

    def test_b08_finalize_refuses_squash_merge(self):
        self._publish_success()
        self._human_merge(squash=True)
        before_pub = self._artifact_bytes('publish.json')
        before_pr = self._artifact_bytes('pull-request.json')
        fake = FakeGh(view_payload=self.merged_view())
        publish_git, worktree_git = self._mocked(fake)
        with self.assertRaisesRegex(ValueError, 'squash merge'):
            publish_pr.finalize(self.task, execute=True)
        # inspected but clean: artifacts unchanged, no cleanup
        self.assertEqual(self._artifact_bytes('publish.json'), before_pub)
        self.assertEqual(self._artifact_bytes('pull-request.json'), before_pr)
        self._assert_invariants()
        self.assertFalse(worktree.publish_status(self.task)['published'])

    def test_b09_finalize_refuses_diverged_local_main(self):
        self._publish_success()
        self._human_merge()
        # manufacture divergence: unrelated commit directly on local main
        (self.repo / 'unrelated.txt').write_text('divergent\n', encoding='utf-8')
        self._git(self.repo, 'add', 'unrelated.txt')
        self._git(self.repo, 'commit', '-m', 'unrelated local work')
        diverged_head = self.real('rev-parse', 'HEAD', cwd=self.repo)
        before_pub = self._artifact_bytes('publish.json')
        before_pr = self._artifact_bytes('pull-request.json')
        fake = FakeGh(view_payload=self.merged_view())
        publish_git, worktree_git = self._mocked(fake)
        with self.assertRaisesRegex(ValueError, 'diverged'):
            publish_pr.finalize(self.task, execute=True)
        self.assertEqual(self.real('rev-parse', 'HEAD', cwd=self.repo), diverged_head)
        self.assertEqual(self._artifact_bytes('publish.json'), before_pub)
        self.assertEqual(self._artifact_bytes('pull-request.json'), before_pr)
        self._assert_invariants()

    # -- B10: finalize precondition mismatch chain -----------------------------

    def test_b10_finalize_refuses_precondition_mismatches(self):
        self._publish_success()
        rd = harnesslib.run_dir(self.task)
        pub_path = worktree.publish_artifact(self.task)
        pr_path = rd / 'pull-request.json'

        def write_publish(status=None, commit_modifier=None):
            baseline = json.loads(
                self._artifact_bytes('publish.json').decode(encoding='utf-8')
            ) if self._artifact_bytes('publish.json') is not None else {
                'schema_version': 1,
                'task_id': self.task,
                'commit': self.real('rev-parse', 'HEAD', cwd=self.path),
                'changed_files': ['src/app.py'],
            }
            data = dict(baseline)
            if status is not None:
                data['status'] = status
            if commit_modifier is not None:
                data['commit'] = commit_modifier
            pub_path.write_text(json.dumps(data), encoding='utf-8')

        fake = FakeGh()
        publish_git, worktree_git = self._mocked(fake)

        # (i) publish.json missing
        pub_path.unlink()
        with self.assertRaisesRegex(ValueError, 'publish.json missing'):
            publish_pr.finalize(self.task, execute=True)

        # (ii) publish.json status not INTEGRATED
        write_publish(status='PASS')
        with self.assertRaisesRegex(ValueError, 'INTEGRATED'):
            publish_pr.finalize(self.task, execute=True)

        # (iii) publish.json commit != pull-request.json task_commit
        write_publish(status='INTEGRATED', commit_modifier='f' * 40)
        with self.assertRaisesRegex(ValueError, 'does not match'):
            publish_pr.finalize(self.task, execute=True)

        # (iv) pull-request.json missing
        write_publish(status='INTEGRATED')
        pr_path.unlink()
        with self.assertRaisesRegex(ValueError, 'pull-request.json missing'):
            publish_pr.finalize(self.task, execute=True)

        # every refusal happened before any gh work or repo mutation
        self.assertEqual(fake.calls, [])
        self._assert_read_only_git(publish_git.calls, worktree_git.calls)
        self._assert_invariants()

    # -- B11: policy refusal ---------------------------------------------------

    def test_b11_policy_refusals_are_hard_fails(self):
        self._fixture_task()
        snapshot = self._snapshot_run_dir()
        for mode in (None, 'fast_forward'):
            self._set_policy(mode)
            fake = FakeGh()
            publish_git, worktree_git = self._mocked(fake)
            for action in (
                lambda: publish_pr.plan(self.task),
                lambda: publish_pr.publish(self.task, execute=True),
                lambda: publish_pr.status(self.task),
                lambda: publish_pr.finalize(self.task, execute=True),
            ):
                with self.assertRaisesRegex(ValueError, 'publish policy|mode is fast_forward'):
                    action()
            self.assertEqual(fake.calls, [])
            self._assert_read_only_git(publish_git.calls, worktree_git.calls)
            self.assertEqual(self._snapshot_run_dir(), snapshot)
            self.assertIsNone(self._artifact_bytes('publish.json'))
            self.assertIsNone(self._artifact_bytes('pull-request.json'))
        self._set_policy('pr')

    # -- B12: gh fails closed before any mutation ------------------------------

    def test_b12_gh_failure_fails_closed_before_mutations(self):
        self._fixture_task()
        snapshot = self._snapshot_run_dir()

        def boom(args):
            raise RuntimeError('gh CLI is not available: boom')

        fake = None
        publish_git, worktree_git = self._mocked(boom)
        with self.assertRaisesRegex(RuntimeError, 'gh CLI is not available'):
            publish_pr.publish(self.task, execute=True)
        # no push reached origin
        self.assertEqual(
            self.real('ls-remote', 'origin', f'refs/heads/{self.branch}', cwd=self.repo),
            '',
        )
        # no task-branch commit was created: the branch tip still equals base
        self.assertEqual(self.real('rev-parse', self.branch, cwd=self.repo), self.base_commit)
        self.assertEqual(self.real('rev-parse', 'HEAD', cwd=self.repo), self.base_commit)
        self.assertIsNone(self._artifact_bytes('publish.json'))
        self.assertIsNone(self._artifact_bytes('pull-request.json'))
        self.assertEqual(self._snapshot_run_dir(), snapshot)
        self.assertEqual(
            [call for call in publish_git.calls if call[0] in MUTATING_GIT_VERBS],
            [],
        )
        self._assert_invariants()

    # -- B13: remote branch collision -----------------------------------------

    def test_b13_remote_branch_collision_fails_before_pr_create(self):
        self._fixture_task()
        # pre-push a divergent agent/<task> tip to origin to force push refusal
        self._git(self.repo, 'push', 'origin', self.branch)
        self._git(self.clone, 'fetch', 'origin')
        self._git(self.clone, 'checkout', '-B', 'collider', f'origin/{self.branch}')
        (self.clone / 'other.txt').write_text('divergent\n', encoding='utf-8')
        self._git(self.clone, 'add', 'other.txt')
        self._git(self.clone, 'commit', '-m', 'divergent')
        self._git(self.clone, 'push', 'origin', f'collider:{self.branch}')
        collider_tip = self.real(
            'ls-remote', 'origin', f'refs/heads/{self.branch}', cwd=self.repo
        ).split()[0]

        fake = FakeGh()
        publish_git, worktree_git = self._mocked(fake)
        with self.assertRaisesRegex(ValueError, 'delete the stale remote branch|no force-push'):
            publish_pr.publish(self.task, execute=True)
        # gh never reached pr create
        self.assertFalse(fake.invoked(('pr', 'create')))
        # origin ref unchanged, no artifacts, worktree and lock intact
        self.assertEqual(
            self.real('ls-remote', 'origin', f'refs/heads/{self.branch}', cwd=self.repo).split()[0],
            collider_tip,
        )
        self.assertIsNone(self._artifact_bytes('publish.json'))
        self.assertIsNone(self._artifact_bytes('pull-request.json'))
        self._assert_invariants()

    # -- B14: idempotence -------------------------------------------------------

    def test_b14_publish_idempotence_refuses_second_run(self):
        self._publish_success()
        before_pub = self._artifact_bytes('publish.json')
        before_pr = self._artifact_bytes('pull-request.json')
        before_remote = self.real(
            'ls-remote', 'origin', f'refs/heads/{self.branch}', cwd=self.repo
        )
        fake2 = FakeGh()
        publish_git, worktree_git = self._mocked(fake2)
        with self.assertRaisesRegex(ValueError, 'already published|use finalize'):
            publish_pr.publish(self.task, execute=True)
        self.assertEqual(fake2.calls, [])
        self.assertEqual(self._artifact_bytes('publish.json'), before_pub)
        self.assertEqual(self._artifact_bytes('pull-request.json'), before_pr)
        self.assertEqual(
            self.real('ls-remote', 'origin', f'refs/heads/{self.branch}', cwd=self.repo),
            before_remote,
        )

    # -- B15: operator safety gates --------------------------------------------

    def test_b15_operator_safety_without_execute(self):
        path = self._fixture_task()
        before = self._snapshot_run_dir()
        fake = FakeGh()
        publish_git, worktree_git = self._mocked(fake)

        plan_out = publish_pr.plan(self.task, execute=True)
        self.assertEqual(plan_out, publish_pr.plan(self.task, execute=False))
        publish_plan = publish_pr.publish(self.task, execute=False)
        self.assertEqual(plan_out, publish_plan)
        self.assertEqual(fake.calls, [])
        self.assertEqual(self._snapshot_run_dir(), before)
        self._assert_read_only_git(publish_git.calls, worktree_git.calls)
        self.assertIsNone(self._artifact_bytes('publish.json'))
        self.assertIsNone(self._artifact_bytes('pull-request.json'))
        # finalize without a publication fails closed and stays read-only
        with self.assertRaisesRegex(ValueError, 'pull-request.json missing'):
            publish_pr.finalize(self.task, execute=False)
        self.assertEqual(self._snapshot_run_dir(), before)

    def test_b15b_finalize_without_execute_is_read_only(self):
        self._publish_success()
        before_pub = self._artifact_bytes('publish.json')
        before_pr = self._artifact_bytes('pull-request.json')
        snapshot = self._snapshot_run_dir()
        fake = FakeGh()
        publish_git, worktree_git = self._mocked(fake)
        out = publish_pr.finalize(self.task, execute=False)
        self.assertEqual(out['action'], 'finalize')
        self.assertFalse(out['execute'])
        self.assertEqual(out['task_commit'], json.loads(before_pr)['task_commit'])
        self.assertEqual(fake.calls, [])
        self.assertEqual(self._artifact_bytes('publish.json'), before_pub)
        self.assertEqual(self._artifact_bytes('pull-request.json'), before_pr)
        self.assertEqual(self._snapshot_run_dir(), snapshot)
        self.assertEqual(self.real('rev-parse', 'HEAD', cwd=self.repo), self.base_commit)
        self._assert_read_only_git(publish_git.calls)
        self._assert_invariants()

    # -- B16: lock validity across the PR window --------------------------------

    def test_b16_lock_validity_preserved_across_pr_window(self):
        self._publish_success()
        lock = worktree._load_lock(self.task)
        self.assertEqual(lock['schema_version'], 3)
        self.assertEqual(lock['task_id'], self.task)
        self.assertEqual(lock['branch'], self.branch)
        self.assertEqual(lock['integration_branch'], 'main')
        self.assertEqual(lock['base_commit'], self.base_commit)
        self.assertEqual(lock['worktree'], str(self.path))
        self.assertEqual(
            self.real('branch', '--show-current', cwd=self.path), self.branch
        )
        # publish_pr must not mutate the lock file
        self.assertEqual(worktree._safe_lock(self.task).read_bytes(), self.lock_bytes_before)

    # -- B17: provenance --------------------------------------------------------

    def test_b17_provenance_in_pr_body_and_artifacts(self):
        fake, publish_git, worktree_git, out = self._publish_success()
        rd = harnesslib.run_dir(self.task)
        pr = json.loads((rd / 'pull-request.json').read_text(encoding='utf-8'))
        pub = json.loads(worktree.publish_artifact(self.task).read_text(encoding='utf-8'))
        lock = worktree._load_lock(self.task)
        self.assertEqual(pr['task_commit'], pub['commit'])
        self.assertEqual(out['task_commit'], pr['task_commit'])
        self.assertEqual(pr['base_commit'], lock['base_commit'])
        self.assertEqual(pr['changed_files'], pub['changed_files'])
        body = fake.pr_create_body()
        self.assertIn(self.task, body)
        self.assertIn(out['task_commit'], body)
        self.assertIn(self.base_commit, body)
        self.assertIn('src/app.py', body)

    # -- run_gh wrapper fail-closed semantics -----------------------------------

    def test_run_gh_fails_closed_without_binary_and_nonzero_rc(self):
        with patch.object(
            publish_pr.subprocess, 'run', side_effect=FileNotFoundError(2, 'gh')
        ):
            with self.assertRaisesRegex(RuntimeError, 'gh CLI is not available'):
                publish_pr.run_gh(['auth', 'status'])
        failing = subprocess.CompletedProcess(
            ['gh', 'auth', 'status'], 3, stdout='', stderr='auth refused'
        )
        with patch.object(publish_pr.subprocess, 'run', return_value=failing):
            with self.assertRaisesRegex(RuntimeError, 'rc=3'):
                publish_pr.run_gh(['auth', 'status'])


if __name__ == '__main__':
    unittest.main()
