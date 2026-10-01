import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from task_router import route


class PokerSpecialistRoutingTests(unittest.TestCase):
    def routed(self, task_id, description, files=None, important=False):
        return route({
            "id": task_id,
            "description": description,
            "files": files or [],
            "risk_factors": {"important_calculation": important},
        })

    def test_quantitative_probability_routes_quant_specialist(self):
        r = self.routed(
            "POKER-Q-TEST",
            "Implement Bayesian poker statistics with credible intervals and Monte Carlo standard error.",
            ["src/poker/math/statistics.py"],
            important=True,
        )
        self.assertIn("quantitative-analyst", r["agents"])
        self.assertIn("probability-statistics", r["skills"])
        self.assertIn("monte-carlo-simulation", r["skills"])

    def test_analytics_metric_routes_analytics_engineer(self):
        r = self.routed(
            "POKER-A-TEST",
            "Implement poker VPIP statistics with explicit legal opportunity denominators and materialized features.",
            ["src/poker/stats/engine.py"],
            important=True,
        )
        self.assertIn("analytics-engineer", r["agents"])
        self.assertIn("data-analytics-engineering", r["skills"])

    def test_data_science_routes_model_specialist(self):
        r = self.routed(
            "POKER-DS-TEST",
            "Build an opponent model with Bayesian priors, calibration, recency weighting and drift detection.",
            ["src/poker/opponent/model.py"],
            important=True,
        )
        self.assertIn("data-scientist", r["agents"])
        self.assertIn("data-science-modeling", r["skills"])
        self.assertIn("experiment-design-calibration", r["skills"])

    def test_solver_routes_strategy_and_game_theory(self):
        r = self.routed(
            "POKER-GT-TEST",
            "Implement poker CFR+ solver exploitability and best-response validation.",
            ["src/poker/solver/cfr.py"],
            important=True,
        )
        self.assertIn("poker-strategy-analyst", r["agents"])
        self.assertIn("game-theory-cfr", r["skills"])

    def test_non_poker_generic_model_does_not_route_poker_specialists(self):
        r = self.routed(
            "GENERIC-TEST",
            "Implement a generic application configuration model and schema.",
            ["src/config/model.py"],
        )
        specialists = {
            "quantitative-analyst",
            "analytics-engineer",
            "data-scientist",
            "poker-strategy-analyst",
        }
        self.assertTrue(specialists.isdisjoint(r["agents"]))


if __name__ == "__main__":
    unittest.main()
