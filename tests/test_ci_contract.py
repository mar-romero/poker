"""CI workflow / check-script contract tests for the harness CI job.

These tests are a permanent textual regression contract (oracle O1):

- .github/workflows/harness.yml must install the pinned runtime
  dependencies (sqlalchemy==2.1.3, alembic==1.20.0) after setup-python
  and before the harness checks run.
- scripts/check-harness.sh must export PYTHONPATH=src before the
  unittest discovery invocation so CI discovers the src-layout modules.
- Fail-closed steps must not be weakened.

The tests use only the standard library and never import repository
modules (no poker, no sqlalchemy) so they run in a bare environment.
All assertions are pure text; no subprocess is executed.
"""

import re
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]

# Golden fragment: pinned dependency install run line (oracle O2).
PINS_INSTALL_FRAGMENT = "pip install sqlalchemy==2.1.3 alembic==1.20.0"
INSTALL_STEP_NAME = "Install pinned runtime dependencies"

# Ordering anchors (oracle O2 / O4).
SETUP_PYTHON_FRAGMENT = "actions/setup-python@v5"
COMPILE_HARNESS_FRAGMENT = "python scripts/compile_harness.py --check"
CHECK_HARNESS_FRAGMENT = "bash scripts/check-harness.sh"

# Golden fragment: PYTHONPATH export (oracle O3).
PYTHONPATH_EXPORT = "export PYTHONPATH=src"
UNITTEST_DISCOVER_FRAGMENT = "python -m unittest discover -s tests"

# Golden fragments: fail-closed invocations that must survive (oracle O4).
CHECK_HARNESS_PY_FRAGMENT = "python scripts/check_harness.py"
RUN_EVALS_FRAGMENT = "python scripts/run_evals.py"


class TestCIWorkflowContract(unittest.TestCase):
    """Contract over .github/workflows/harness.yml."""

    @classmethod
    def setUpClass(cls):
        cls.workflow_text = (REPO_ROOT / ".github/workflows/harness.yml").read_text(
            encoding="utf-8"
        )

    def test_workflow_installs_pinned_runtime_dependencies_before_checks(self):
        text = self.workflow_text

        # Step name present.
        self.assertIn(
            INSTALL_STEP_NAME,
            text,
            "step 'Install pinned runtime dependencies' missing from harness.yml",
        )

        # Exact pins present on the install run line (no floating specifiers).
        self.assertIn(
            PINS_INSTALL_FRAGMENT,
            text,
            "pinned install fragment 'pip install sqlalchemy==2.1.3 "
            "alembic==1.20.0' not found in harness.yml",
        )

        # Extract the install run line (the line containing the fragment).
        pip_line = next(
            (
                line
                for line in text.splitlines()
                if PINS_INSTALL_FRAGMENT in line
            ),
            None,
        )
        self.assertIsNotNone(pip_line, "pinned install run line not found")

        # Both pins must be exact-version pins...
        self.assertIn("sqlalchemy==2.1.3", pip_line)
        self.assertIn("alembic==1.20.0", pip_line)
        # ...and the line must carry no floating specifiers for these deps.
        self.assertIsNone(
            re.search(r"(sqlalchemy|alembic)\s*[><~*]", pip_line),
            "floating version specifier found on the pip install line",
        )

        # Ordering: setup-python < install < compile_harness --check < check-harness.
        self.assertLess(
            text.index(SETUP_PYTHON_FRAGMENT),
            text.index(PINS_INSTALL_FRAGMENT),
            "dependency install must come after actions/setup-python@v5",
        )
        self.assertLess(
            text.index(PINS_INSTALL_FRAGMENT),
            text.index(COMPILE_HARNESS_FRAGMENT),
            "dependency install must come before compile_harness --check",
        )
        self.assertLess(
            text.index(COMPILE_HARNESS_FRAGMENT),
            text.index(CHECK_HARNESS_FRAGMENT),
            "compile_harness --check must come before bash check-harness.sh",
        )


class TestCheckHarnessContract(unittest.TestCase):
    """Contract over scripts/check-harness.sh."""

    @classmethod
    def setUpClass(cls):
        cls.script_text = (REPO_ROOT / "scripts/check-harness.sh").read_text(
            encoding="utf-8"
        )

    def test_check_harness_exports_pythonpath_before_unittest(self):
        text = self.script_text

        # Line-anchored: a real executable export line, not a comment or
        # nested fragment.
        match = re.search(r"^export PYTHONPATH=src\s*$", text, re.MULTILINE)
        self.assertIsNotNone(
            match,
            "export PYTHONPATH=src missing from scripts/check-harness.sh",
        )

        self.assertLess(
            match.start(),
            text.index(UNITTEST_DISCOVER_FRAGMENT),
            "PYTHONPATH export must come before the unittest discovery step",
        )

    def test_check_harness_keeps_fail_closed_steps(self):
        for fragment in (
            CHECK_HARNESS_PY_FRAGMENT,
            UNITTEST_DISCOVER_FRAGMENT,
            RUN_EVALS_FRAGMENT,
        ):
            self.assertIn(
                fragment,
                self.script_text,
                f"fail-closed invocation weakened: {fragment!r} missing",
            )

    @unittest.skip("tabled: live CI smoke belongs to the workflow run itself")
    def test_optional_check_harness_dry_run(self):
        pass


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
