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


class CheckDocsTests(unittest.TestCase):
    def setUp(self):
        self.policy = S.load_policy()
        self.golden = load_golden()

    def test_real_docs_are_in_sync(self):
        problems = CP.check_docs(self.policy, self.golden, CP.DOC_PATH, fix=False)
        self.assertEqual(problems, [])

    def test_stale_block_is_detected_without_fix(self):
        with open(CP.DOC_PATH, "r", encoding="utf-8") as f:
            real_text = f.read()
        stale = real_text.replace(
            "<!-- policy:tiers -->\n| Tier | Base | Definition |",
            "<!-- policy:tiers -->\nSTALE CONTENT", 1)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "scoring.md")
            with open(path, "w", encoding="utf-8") as f:
                f.write(stale)
            problems = CP.check_docs(self.policy, self.golden, path, fix=False)
            self.assertTrue(any("tiers" in p for p in problems))
            # file must be untouched when fix is False
            with open(path, "r", encoding="utf-8") as f:
                self.assertEqual(f.read(), stale)

    def test_fix_rewrites_stale_block_to_match_policy(self):
        with open(CP.DOC_PATH, "r", encoding="utf-8") as f:
            real_text = f.read()
        stale = real_text.replace(
            "<!-- policy:tiers -->\n| Tier | Base | Definition |",
            "<!-- policy:tiers -->\nSTALE CONTENT", 1)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "scoring.md")
            with open(path, "w", encoding="utf-8") as f:
                f.write(stale)
            problems = CP.check_docs(self.policy, self.golden, path, fix=True)
            self.assertEqual(problems, [])
            # a second, non-fix pass over the now-fixed file must be clean
            problems2 = CP.check_docs(self.policy, self.golden, path, fix=False)
            self.assertEqual(problems2, [])

    def test_missing_marker_is_reported(self):
        with open(CP.DOC_PATH, "r", encoding="utf-8") as f:
            real_text = f.read()
        broken = real_text.replace("<!-- policy:tiers -->", "", 1)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "scoring.md")
            with open(path, "w", encoding="utf-8") as f:
                f.write(broken)
            problems = CP.check_docs(self.policy, self.golden, path, fix=False)
            self.assertTrue(any("expected exactly one" in p for p in problems))


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
