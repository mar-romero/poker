from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("receipt_review_routing", ROOT / "scripts" / "receipt_review.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class ReceiptRoutingAndConsentTests(unittest.TestCase):
    def _route(self):
        return {
            "task_id": "TASK-1",
            "risk": "R1",
            "agents": ["implementer", "reviewer"],
            "skills": [],
            "requirements": {"review": True, "verification": False},
        }

    def test_rdd_off_high_candidate_adds_independent_verifier(self):
        with tempfile.TemporaryDirectory() as td:
            run = Path(td)
            with patch.object(mod, "run_dir", return_value=run), \
                 patch.object(mod, "mode_status", return_value={"mode": "off", "source": "test"}), \
                 patch.object(mod, "assess", return_value={"risk": "high", "subject_hash": "s"}), \
                 patch.object(mod, "_task_route", return_value=self._route()), \
                 patch.object(mod, "_small_implementer", return_value=(False, "full model")):
                plan = mod.prepare("TASK-1", apply=False)
        self.assertTrue(plan["independent_verifier"])
        self.assertIn("assess=high", " ".join(plan["reasons"]))

    def test_rdd_off_medium_small_model_adds_independent_verifier(self):
        with tempfile.TemporaryDirectory() as td:
            run = Path(td)
            with patch.object(mod, "run_dir", return_value=run), \
                 patch.object(mod, "mode_status", return_value={"mode": "off", "source": "test"}), \
                 patch.object(mod, "assess", return_value={"risk": "medium", "subject_hash": "s"}), \
                 patch.object(mod, "_task_route", return_value=self._route()), \
                 patch.object(mod, "_small_implementer", return_value=(True, "low effort")):
                plan = mod.prepare("TASK-1", apply=False)
        self.assertTrue(plan["independent_verifier"])

    def test_review_consent_is_reused_for_same_provider_session(self):
        with tempfile.TemporaryDirectory() as td:
            temp_root = Path(td)
            runtime = temp_root / ".harness" / "receipt-review"
            with patch.object(mod, "ROOT", temp_root), patch.object(mod, "RUNTIME", runtime):
                granted = mod.grant_consent("TASK-1", "opencode", "SESSION-A")
                reused = mod.consent_status("TASK-2", "opencode", "SESSION-A")
                other = mod.consent_status("TASK-2", "opencode", "SESSION-B")
        self.assertTrue(granted["granted"])
        self.assertTrue(reused["granted"])
        self.assertFalse(other["granted"])

    def test_dynamic_verifier_gets_independent_model_selection(self):
        import json
        import model_router
        import model_task_profile

        with tempfile.TemporaryDirectory() as td:
            temp_root = Path(td)
            run = temp_root / ".harness" / "runs" / "TASK-1"
            run.mkdir(parents=True)
            (run / "task.json").write_text(json.dumps({"id": "TASK-1", "description": "change code"}), encoding="utf-8")
            (run / "model-selections.json").write_text(json.dumps({
                "provider": "opencode",
                "inventory_path": None,
                "selections": [{
                    "agent": "implementer",
                    "status": "selected",
                    "action": "use",
                    "model_id": "vendor/impl",
                    "base_model_id": "vendor/impl",
                    "model_family": "impl-family",
                    "model_vendor": "vendor",
                }],
            }), encoding="utf-8")
            route = self._route()
            route["agents"].append("verifier")
            captured = {}

            def fake_select_model(**kwargs):
                captured.update(kwargs)
                return {
                    "agent": "verifier",
                    "status": "selected",
                    "action": "use",
                    "model_id": "other/verifier",
                    "base_model_id": "other/verifier",
                    "model_family": "verify-family",
                    "model_vendor": "other",
                }

            manifest = {"agents": {"verifier": {"model_class": "reasoning"}}}
            policy = {"independence": {"roles": {"verifier": {"avoid_agents": ["implementer"]}}}}
            with patch.object(mod, "ROOT", temp_root), \
                 patch.object(mod, "run_dir", return_value=run), \
                 patch.object(mod, "_active_provider", return_value="opencode"), \
                 patch.object(mod, "read_provider_active", return_value={"task_id": "TASK-1", "model_selections_path": "local"}), \
                 patch.object(mod, "provider_model_selections_path", return_value=run / "model-selections.json"), \
                 patch.object(mod, "provider_active_path", return_value=run / "active-task.json"), \
                 patch.object(mod, "load_manifest", return_value=manifest), \
                 patch.object(model_router, "load_policy", return_value=policy), \
                 patch.object(model_router, "select_model", side_effect=fake_select_model), \
                 patch.object(model_task_profile, "profile_task", return_value={}), \
                 patch.object(model_task_profile, "target_for_agent", return_value={}):
                created = mod._ensure_dynamic_agent_models("TASK-1", route, ["verifier"])

            self.assertEqual(len(created), 1)
            self.assertIn("vendor/impl", captured["avoid_models"])
            self.assertIn("impl-family", captured["avoid_families"])
            self.assertIn("vendor", captured["avoid_vendors"])
            saved = json.loads((run / "model-selections.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["selections"][-1]["agent"], "verifier")

    def test_dynamic_agent_enrichment_keeps_binding_consistent(self):
        """Regression (POKER-TEST-FIXTURE-HYGIENE-001): the --apply path must not
        poison the overlay binding. _ensure_dynamic_agent_models used to rewrite
        model-selections.json (adding the dynamic verifier) BEFORE the validating
        read of the active binding, so validate_provider_active compared the
        binding's stale recorded sha against the just-modified file and raised
        'provider active binding model selections integrity mismatch' before the
        binding refresh could run. After the enrichment, binding and selections
        file must be mutually consistent immediately."""
        import hashlib
        import json

        import harnesslib
        import model_router
        import model_task_profile

        with tempfile.TemporaryDirectory() as td:
            temp_root = Path(td)
            run = temp_root / ".harness" / "runs" / "TASK-1"
            run.mkdir(parents=True)
            (run / "task.json").write_text(json.dumps({"id": "TASK-1", "description": "change code"}), encoding="utf-8")
            # The validating read requires every shared run artifact reference
            # to resolve; only the snapshot content is parsed.
            for name in ("route.json", "context.json", "progress.json", "impact.json", "agent-budget.json"):
                (run / name).write_text("{}", encoding="utf-8")
            selections = [{
                "agent": "implementer",
                "status": "selected",
                "action": "use",
                "model_id": "vendor/impl",
                "base_model_id": "vendor/impl",
                "model_family": "impl-family",
                "model_vendor": "vendor",
            }]
            payload = {
                "schema_version": 2,
                "provider": "opencode",
                "task_id": "TASK-1",
                "inventory_path": None,
                "selections": selections,
            }
            models_path = run / "model-selections.json"
            models_path.write_text(json.dumps(payload), encoding="utf-8")
            binding = {
                "schema_version": 3,
                "provider": "opencode",
                "overlay": {"schema_version": 1, "worktree_id": "wt-test"},
                "task_id": "TASK-1",
                "task_path": "tasks/TASK-1.json",
                "route_path": ".harness/runs/TASK-1/route.json",
                "context_path": ".harness/runs/TASK-1/context.json",
                "progress_path": ".harness/runs/TASK-1/progress.json",
                "task_snapshot_path": ".harness/runs/TASK-1/task.json",
                "impact_path": ".harness/runs/TASK-1/impact.json",
                "agent_budget_path": ".harness/runs/TASK-1/agent-budget.json",
                "model_selections_path": ".harness/runs/TASK-1/model-selections.json",
                "model_selections_sha256": hashlib.sha256(models_path.read_bytes()).hexdigest(),
                "selections": selections,
            }
            binding_path = run / "active-task.json"
            binding_path.write_text(json.dumps(binding), encoding="utf-8")

            route = self._route()
            route["agents"].append("verifier")

            def fake_select_model(**kwargs):
                return {
                    "agent": "verifier",
                    "status": "selected",
                    "action": "use",
                    "model_id": "other/verifier",
                    "base_model_id": "other/verifier",
                    "model_family": "verify-family",
                    "model_vendor": "other",
                }

            manifest = {"agents": {"verifier": {"model_class": "reasoning"}}}
            policy = {"independence": {"roles": {"verifier": {"avoid_agents": ["implementer"]}}}}
            with patch.object(mod, "ROOT", temp_root), \
                 patch.object(mod, "run_dir", return_value=run), \
                 patch.object(mod, "_active_provider", return_value="opencode"), \
                 patch.object(mod, "load_manifest", return_value=manifest), \
                 patch.object(mod, "provider_model_selections_path", return_value=models_path), \
                 patch.object(mod, "provider_active_path", return_value=binding_path), \
                 patch.object(model_router, "load_policy", return_value=policy), \
                 patch.object(model_router, "select_model", side_effect=fake_select_model), \
                 patch.object(model_task_profile, "profile_task", return_value={}), \
                 patch.object(model_task_profile, "target_for_agent", return_value={}), \
                 patch.object(harnesslib, "worktree_identity", return_value={"schema_version": 1, "worktree_id": "wt-test"}), \
                 patch.object(harnesslib, "_worktree_metadata", return_value=(temp_root, temp_root)), \
                 patch.object(harnesslib, "run_dir", return_value=run), \
                 patch.object(harnesslib, "runtime_reference", side_effect=lambda value: temp_root / value), \
                 patch.object(harnesslib, "provider_model_selections_path", return_value=models_path), \
                 patch.object(harnesslib, "provider_active_path", return_value=binding_path):
                created = mod._ensure_dynamic_agent_models("TASK-1", route, ["verifier"])

                saved_binding = json.loads(binding_path.read_text(encoding="utf-8"))
                saved_payload = json.loads(models_path.read_text(encoding="utf-8"))
                self.assertEqual(
                    saved_binding["model_selections_sha256"],
                    hashlib.sha256(models_path.read_bytes()).hexdigest(),
                )
                self.assertEqual(saved_binding["selections"], saved_payload["selections"])
                # Full invariant: the validated binding read must pass now.
                harnesslib.validate_provider_active("opencode", saved_binding, root=temp_root)


if __name__ == "__main__":
    unittest.main()
