"""Independent verification of the mode-overlay functionality added on top
of the srcscore_core split: deep_merge, apply_mode, and the --mode wiring
in srcscore_core.cli.main.

Run offline only (NullCache / --no-net / injected fixtures), same convention
as the rest of tests/.
"""

import copy
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

import _pathsetup  # noqa: F401
import srcscore as S
import check_policy as CP
from srcscore_core import cli as CLI


# ----------------------------------------------------------------------------
# deep_merge
# ----------------------------------------------------------------------------

class DeepMergeTests(unittest.TestCase):
    def test_nested_dict_keys_merge_recursively(self):
        base = {"a": {"x": 1, "y": 2}, "b": 5}
        overlay = {"a": {"y": 99}}
        out = S.deep_merge(base, overlay)
        self.assertEqual(out, {"a": {"x": 1, "y": 99}, "b": 5})

    def test_overlay_completely_replaces_a_list(self):
        base = {"items": [1, 2, 3], "b": 1}
        overlay = {"items": [9]}
        out = S.deep_merge(base, overlay)
        self.assertEqual(out["items"], [9])

    def test_overlay_wins_on_scalar_conflict(self):
        base = {"n": 1, "s": "old"}
        overlay = {"n": 2, "s": "new"}
        out = S.deep_merge(base, overlay)
        self.assertEqual(out, {"n": 2, "s": "new"})

    def test_keys_overlay_does_not_mention_are_untouched(self):
        base = {"a": 1, "b": {"c": 2, "d": 3}, "e": [1, 2]}
        overlay = {"b": {"c": 20}}
        out = S.deep_merge(base, overlay)
        self.assertEqual(out["a"], 1)
        self.assertEqual(out["b"]["d"], 3)
        self.assertEqual(out["e"], [1, 2])

    def test_base_dict_is_not_mutated(self):
        base = {"a": {"x": 1}}
        base_copy = copy.deepcopy(base)
        S.deep_merge(base, {"a": {"x": 2}})
        self.assertEqual(base, base_copy)

    def test_readme_key_in_overlay_is_ignored(self):
        base = {"a": 1}
        overlay = {"_readme": "explains this overlay", "a": 2}
        out = S.deep_merge(base, overlay)
        self.assertNotIn("_readme", out)
        self.assertEqual(out["a"], 2)


# ----------------------------------------------------------------------------
# apply_mode against the five real overlay files
# ----------------------------------------------------------------------------

class ApplyModeRealFilesTests(unittest.TestCase):
    def setUp(self):
        self.policy = S.load_policy()

    def test_all_five_modes_load_merge_and_validate(self):
        for mode in ("academic", "non-academic", "community-opinion", "news", "official-docs"):
            with self.subTest(mode=mode):
                merged = S.apply_mode(self.policy, mode)
                S.validate_policy(merged, "mode:%s" % mode)

    def test_academic_mode_is_a_true_no_op(self):
        merged = S.apply_mode(self.policy, "academic")
        self.assertEqual(merged, self.policy)

    def test_official_docs_disables_all_three_signals(self):
        merged = S.apply_mode(self.policy, "official-docs")
        for sig in ("recency_decay", "peer_review", "engagement"):
            self.assertFalse(S.signal_enabled(merged, sig))
        # untouched sections still equal the base policy
        self.assertEqual(merged["domains"], self.policy["domains"])
        self.assertEqual(merged["tiers"], self.policy["tiers"])

    def test_news_mode_overrides_recency_but_keeps_engagement_hn_min_tier(self):
        merged = S.apply_mode(self.policy, "news")
        self.assertEqual(merged["recency"]["decay"]["grace_years"], 0.01)
        self.assertFalse(S.signal_enabled(merged, "peer_review"))
        self.assertTrue(S.signal_enabled(merged, "engagement"))
        self.assertEqual(merged["defaults"]["field"], "news")
        self.assertIn("news", merged["field_halflife_years"])
        # base ai/cs/... half-lives survive the merge
        self.assertEqual(merged["field_halflife_years"]["ai"],
                          self.policy["field_halflife_years"]["ai"])

    def test_community_opinion_sets_new_default_field_and_halflife(self):
        merged = S.apply_mode(self.policy, "community-opinion")
        self.assertEqual(merged["defaults"]["field"], "opinion")
        self.assertEqual(merged["field_halflife_years"]["opinion"], 0.75)
        self.assertFalse(S.signal_enabled(merged, "peer_review"))

    def test_community_opinion_carries_a_trusted_people_list(self):
        merged = S.apply_mode(self.policy, "community-opinion")
        self.assertIn("trusted_people", merged)
        self.assertIn("x.com", merged["trusted_people"]["hosts"])
        self.assertGreater(len(merged["trusted_people"]["hosts"]["x.com"]), 0)

    def test_non_academic_keeps_peer_review_and_recency_on(self):
        merged = S.apply_mode(self.policy, "non-academic")
        self.assertTrue(S.signal_enabled(merged, "peer_review"))
        self.assertTrue(S.signal_enabled(merged, "recency_decay"))
        self.assertEqual(merged["defaults"]["field"], "cs")


# ----------------------------------------------------------------------------
# CLI-level tests
# ----------------------------------------------------------------------------

class CliTests(unittest.TestCase):
    def test_unknown_mode_choice_rejected_by_argparse(self):
        buf = io.StringIO()
        with redirect_stderr(buf):
            with self.assertRaises(SystemExit) as cm:
                CLI.main(["--mode", "not-a-real-mode", "--no-net", "-u", "https://example.com"])
        self.assertEqual(cm.exception.code, 2)
        self.assertIn("invalid choice", buf.getvalue())

    def test_mode_flag_selects_overlay_end_to_end(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = CLI.main([
                "--mode", "non-academic", "--no-net",
                "-u", "https://arxiv.org/abs/2201.11111",
                "--format", "json",
            ])
        self.assertEqual(rc, 0, msg=err.getvalue())
        rows = json.loads(out.getvalue())
        self.assertEqual(len(rows), 1)
        self.assertIn("score", rows[0])


# ----------------------------------------------------------------------------
# check_policy.check_modes
# ----------------------------------------------------------------------------

class CheckModesBrokenOverlayTests(unittest.TestCase):
    def setUp(self):
        self.policy = S.load_policy()

    def test_real_five_modes_pass_with_zero_problems(self):
        problems = CP.check_modes(self.policy, CP.MODES_DIR)
        self.assertEqual(problems, [])

    def test_overlay_setting_defaults_field_to_unknown_field_is_caught(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "broken_field.json"), "w", encoding="utf-8") as f:
                json.dump({"defaults": {"field": "totally-bogus-field"}}, f)
            problems = CP.check_modes(self.policy, d)
        self.assertTrue(problems)
        self.assertTrue(any("field_halflife_years" in p for p in problems))

    def test_overlay_breaking_verdict_band_ordering_is_caught(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "broken_bands.json"), "w", encoding="utf-8") as f:
                json.dump({"verdicts": {"bands": [{"name": "X", "min": 10}]}}, f)
            problems = CP.check_modes(self.policy, d)
        self.assertTrue(problems)
        self.assertTrue(any("lowest verdict band" in p for p in problems))


if __name__ == "__main__":
    unittest.main()
