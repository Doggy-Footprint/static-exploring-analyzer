import copy
import json
import os
import tempfile
import unittest

import _pathsetup  # noqa: F401
import srcscore as S
import check_policy as CP


def load_golden():
    with open(CP.GOLDEN_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


class CheckSchemaTests(unittest.TestCase):
    def test_real_policy_passes_schema_check(self):
        policy, problems = CP.check_schema(S.POLICY_PATH)
        self.assertEqual(problems, [])
        self.assertIsNotNone(policy)

    def test_missing_policy_file_reports_problem(self):
        policy, problems = CP.check_schema("/nonexistent/policy.json")
        self.assertIsNone(policy)
        self.assertTrue(problems)


class CheckModesTests(unittest.TestCase):
    def setUp(self):
        self.policy = S.load_policy()

    def test_real_mode_overlays_merge_and_validate(self):
        problems = CP.check_modes(self.policy, CP.MODES_DIR)
        self.assertEqual(problems, [])

    def test_missing_modes_dir_reports_problem(self):
        problems = CP.check_modes(self.policy, "/nonexistent/modes")
        self.assertTrue(problems)

    def test_overlay_that_breaks_validation_is_reported(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "broken.json"), "w", encoding="utf-8") as f:
                json.dump({"defaults": {"field": "not-a-real-field"}}, f)
            problems = CP.check_modes(self.policy, d)
            self.assertTrue(any("field_halflife_years" in p for p in problems))

    def test_overlay_deep_merges_without_dropping_untouched_keys(self):
        merged = S.apply_mode(self.policy, "non-academic", CP.MODES_DIR)
        self.assertEqual(merged["defaults"]["field"], "cs")
        # untouched top-level keys must survive the merge unchanged
        self.assertEqual(merged["domains"], self.policy["domains"])
        self.assertEqual(merged["tiers"], self.policy["tiers"])


class CheckGoldenTests(unittest.TestCase):
    def setUp(self):
        self.policy = S.load_policy()

    def test_real_golden_cases_pass(self):
        golden = load_golden()
        problems = CP.check_golden(self.policy, golden, CP.GOLDEN_PATH, bless=False)
        self.assertEqual(problems, [])

    def test_wrong_expectation_is_reported(self):
        golden = copy.deepcopy(load_golden())
        golden["cases"][0]["expect"]["score"] = 0.1
        problems = CP.check_golden(self.policy, golden, CP.GOLDEN_PATH, bless=False)
        self.assertTrue(problems)
        self.assertIn(golden["cases"][0]["name"], problems[0])

    def test_bless_rewrites_expectations_to_match_current_scoring(self):
        golden = copy.deepcopy(load_golden())
        golden["cases"][0]["expect"]["score"] = 0.1
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "golden.json")
            problems = CP.check_golden(self.policy, golden, path, bless=True)
            self.assertEqual(problems, [])
            with open(path, "r", encoding="utf-8") as f:
                blessed = json.load(f)
            real = load_golden()
            self.assertEqual(
                blessed["cases"][0]["expect"]["score"],
                real["cases"][0]["expect"]["score"])

    def test_bless_does_not_write_file_when_nothing_changed(self):
        golden = copy.deepcopy(load_golden())
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "golden.json")
            CP.check_golden(self.policy, golden, path, bless=True)
            self.assertFalse(os.path.exists(path))


if __name__ == "__main__":
    unittest.main()
