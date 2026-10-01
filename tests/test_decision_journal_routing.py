import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from task_router import route


class DecisionJournalRoutingTests(unittest.TestCase):
    def task(self, task_id, text, risk=None):
        t={"id":task_id,"description":text,"request":{"canonical_english":text},"tags":[],"files":["src/poker/core.py"],"risk_factors":{}}
        if risk is not None:
            t["risk"]=risk
        return t

    def test_material_task_routes_decision_journaler(self):
        out=route(self.task("ADR-ROUTE-001", "Implement weighted range equity calculation with deterministic tests", "R2"))
        self.assertIn("decision-journaler", out["agents"])
        self.assertIn("decision-journal", out["skills"])

    def test_r0_mechanical_docs_does_not_force_decision_journaler(self):
        out=route(self.task("ADR-ROUTE-002", "Fix documentation typo", "R0"))
        self.assertNotIn("decision-journaler", out["agents"])

    def test_agent_is_read_only_and_skill_is_registered(self):
        manifest=json.loads((ROOT/"harness"/"manifest.yaml").read_text())
        agent=manifest["agents"]["decision-journaler"]
        self.assertEqual("read-only", agent["mode"])
        self.assertIn("decision-journal", agent["skills"])
        self.assertIn("decision-journal", manifest["agents"]["implementer"]["skills"])


if __name__ == "__main__":
    unittest.main()
