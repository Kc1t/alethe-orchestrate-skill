import importlib.util
import unittest
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None

ROOT = Path(__file__).resolve().parent.parent
CHECKER = ROOT / "tools" / "check_playbooks.py"


@unittest.skipIf(yaml is None, "PyYAML is not installed")
class ConditionPatternTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("check_playbooks", CHECKER)
        checker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(checker)
        cls.pattern = checker.condition_pattern(["claude", "codex"])

    def test_price_and_spend_conditions_take_dollars_with_up_to_two_decimals(self):
        for token in ("price-above:5", "price-above:2.5", "price-above:0.25", "spent-above:20",
                      "not spent-above:2.5", "not price-above:15", "budget-low", "claude-opus-above:50"):
            self.assertRegex(token, self.pattern)
        for token in ("price-above:", "price-above:x", "price-above:1.234", "spent-above:.5", "spent-above:5.",
                      "price-above:-1", "cost-above:5", "not  price-above:5"):
            self.assertNotRegex(token, self.pattern)


if __name__ == "__main__":
    unittest.main()
