#!/usr/bin/env python3
"""Runtime feature probes for official subscription CLIs.

Flags drift over time. The bridge uses these probes for optional parity/safety
features instead of assuming every installed CLI is the same version.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any

from harnesslib import run_with_tree_kill


def _help_argv(provider: str, executable: str) -> list[str]:
    if provider == "codex":
        return [executable, "exec", "--help"]
    return [executable, "--help"]


FEATURE_TOKENS: dict[str, dict[str, tuple[str, ...]]] = {
    "codex": {
        "sandbox": ("--sandbox",),
        "model": ("--model",),
        "json": ("--json",),
    },
    "claude": {
        "effort": ("--effort",),
        "allowed_tools": ("--allowedtools", "--allowed-tools"),
        "permission_mode": ("--permission-mode",),
        "print": ("--print", "-p"),
    },
    "copilot": {
        "allow_tool": ("--allow-tool",),
        "available_tools": ("--available-tools",),
        "acp": ("--acp",),
        "effort": ("--effort",),
    },
    "cursor": {
        "mode": ("--mode",),
        "ask_mode": ("--mode", "ask"),
        "sandbox": ("--sandbox",),
        "output_format": ("--output-format",),
    },
    "grok": {
        "effort": ("--effort",),
        "permissions": ("--allow", "--deny"),
        "mcp": ("mcp",),
        "no_subagents": ("--no-subagents",),
    },
    "gemini": {
        "approval_mode": ("--approval-mode",),
        "output_format": ("--output-format",),
        "model": ("--model",),
    },
}


@lru_cache(maxsize=32)
def probe(provider: str, executable: str) -> dict[str, Any]:
    try:
        # Same deadline + tree-kill contract as subscription_bridge._run_status:
        # shim grandchildren must not be able to wedge the probe forever.
        code, out, err = run_with_tree_kill(
            _help_argv(provider, executable), timeout=8,
        )
    except Exception as exc:
        return {"probe_ok": False, "error": str(exc), "features": {}}
    if code is None:
        return {
            "probe_ok": False,
            "error": "probe timed out after 8s; process tree killed",
            "features": {},
        }
    text = (out + "\n" + err).lower()
    ok = code == 0 or bool(text.strip())
    features: dict[str, bool] = {}
    for name, tokens in FEATURE_TOKENS.get(provider, {}).items():
        features[name] = all(token.lower() in text for token in tokens)
    return {"probe_ok": ok, "features": features, "help_excerpt": text[:1200] if text else ""}
