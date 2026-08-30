"""Independent verification of the mode-overlay / CLI-tuning functionality
added on top of the srcscore_core split: deep_merge, apply_mode,
apply_half_life_changes, apply_adjustments, apply_switches, and the CLI
wiring in srcscore_core.cli.main.

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


def score(url, policy=None, field="ai", use_net=False, injected=None):
    policy = policy if policy is not None else S.load_policy()
    cache = S.NullCache()
    return S.score_one({"url": url, "title": ""}, policy, cache, field, use_net, injected)


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

    def test_non_academic_keeps_peer_review_and_recency_on(self):
        merged = S.apply_mode(self.policy, "non-academic")
        self.assertTrue(S.signal_enabled(merged, "peer_review"))
        self.assertTrue(S.signal_enabled(merged, "recency_decay"))
        self.assertEqual(merged["defaults"]["field"], "cs")


# ----------------------------------------------------------------------------
# apply_half_life_changes
# ----------------------------------------------------------------------------

class ApplyHalfLifeChangesTests(unittest.TestCase):
    def setUp(self):
        self.policy = S.load_policy()

    def test_valid_override_changes_only_that_field(self):
        before = copy.deepcopy(self.policy)
        out = S.apply_half_life_changes(self.policy, ["ai:1.5"])
        self.assertEqual(out["field_halflife_years"]["ai"], 1.5)
        other_keys = set(before["field_halflife_years"]) - {"ai"}
        for k in other_keys:
            self.assertEqual(out["field_halflife_years"][k], before["field_halflife_years"][k])
        # original policy dict is untouched
        self.assertEqual(self.policy, before)

    def test_field_matching_is_case_insensitive(self):
        out = S.apply_half_life_changes(self.policy, ["AI:1.5"])
        self.assertEqual(out["field_halflife_years"]["ai"], 1.5)

    def test_unknown_field_raises_policy_error(self):
        with self.assertRaises(S.PolicyError):
            S.apply_half_life_changes(self.policy, ["not-a-real-field:2"])

    def test_malformed_pair_without_colon_raises_policy_error(self):
        with self.assertRaises(S.PolicyError):
            S.apply_half_life_changes(self.policy, ["ai-no-colon"])

    def test_empty_pairs_list_is_a_no_op_returning_same_object(self):
        out = S.apply_half_life_changes(self.policy, [])
        self.assertIs(out, self.policy)


# ----------------------------------------------------------------------------
# apply_adjustments
# ----------------------------------------------------------------------------

class ApplyAdjustmentsTests(unittest.TestCase):
    def setUp(self):
        self.policy = S.load_policy()

    def test_valid_delta_changes_only_that_leaf(self):
        before = copy.deepcopy(self.policy)
        out = S.apply_adjustments(self.policy, ["peer_review.published_bonus:+3"])
        self.assertEqual(out["peer_review"]["published_bonus"],
                          before["peer_review"]["published_bonus"] + 3)
        # everything else, including sibling keys under peer_review, is untouched
        out_minus = copy.deepcopy(out)
        out_minus["peer_review"]["published_bonus"] = before["peer_review"]["published_bonus"]
        self.assertEqual(out_minus, before)
        # original policy dict is untouched (adjustments must not mutate in place)
        self.assertEqual(self.policy, before)

    def test_negative_delta_also_works(self):
        before = self.policy["peer_review"]["preprint_penalty"]
        out = S.apply_adjustments(self.policy, ["peer_review.preprint_penalty:-2"])
        self.assertEqual(out["peer_review"]["preprint_penalty"], before - 2)

    def test_nonexistent_path_raises_policy_error(self):
        with self.assertRaises(S.PolicyError):
            S.apply_adjustments(self.policy, ["peer_review.no_such_field:1"])

    def test_path_pointing_at_a_string_raises_policy_error(self):
        with self.assertRaises(S.PolicyError):
            S.apply_adjustments(self.policy, ["defaults.field:1"])

    def test_path_pointing_at_a_dict_raises_policy_error(self):
        with self.assertRaises(S.PolicyError):
            S.apply_adjustments(self.policy, ["peer_review.unvetted:1"])

    def test_malformed_pair_raises_policy_error(self):
        with self.assertRaises(S.PolicyError):
            S.apply_adjustments(self.policy, ["no-colon-here"])


# ----------------------------------------------------------------------------
# apply_switches: policy-dict level
# ----------------------------------------------------------------------------

class ApplySwitchesPolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = S.load_policy()

    def test_disable_sets_signal_false(self):
        out = S.apply_switches(self.policy, [], ["engagement"])
        self.assertFalse(S.signal_enabled(out, "engagement"))

    def test_later_disable_wins_over_enable_of_same_switch(self):
        out = S.apply_switches(self.policy, ["peer-review"], ["peer-review"])
        self.assertFalse(S.signal_enabled(out, "peer_review"))

    def test_unknown_switch_raises_policy_error(self):
        with self.assertRaises(S.PolicyError):
            S.apply_switches(self.policy, [], ["not-a-switch"])


# ----------------------------------------------------------------------------
# apply_switches: end-to-end scoring behavior
# ----------------------------------------------------------------------------

class SwitchesAffectScoringTests(unittest.TestCase):
    def setUp(self):
        self.policy = S.load_policy()

    def test_disabling_peer_review_removes_preprint_penalty_and_flags(self):
        rec = {"scholar": {"citations": 2, "year": 2026, "age_years": 0.2,
                            "peer_reviewed": False}}
        with_pr = score("https://arxiv.org/abs/2601.99999", injected=rec)
        self.assertIn("preprint", with_pr["flags"])
        self.assertIn("unvetted", with_pr["flags"])

        off_policy = S.apply_switches(self.policy, [], ["peer-review"])
        without_pr = score("https://arxiv.org/abs/2601.99999", policy=off_policy, injected=rec)
        self.assertNotIn("preprint", without_pr["flags"])
        self.assertNotIn("unvetted", without_pr["flags"])
        self.assertFalse(any(n.startswith("published@") for n in without_pr["signals"]))
        # with the penalty removed, score should be strictly higher
        self.assertGreater(without_pr["score"], with_pr["score"])

    def test_disabling_peer_review_also_removes_published_bonus(self):
        rec = {"scholar": {"citations": 60, "year": 2022, "age_years": 3.5,
                            "peer_reviewed": True, "venue": "ICML"}}
        with_pr = score("https://arxiv.org/abs/2201.88888", injected=rec)
        self.assertTrue(any(n.startswith("published@") for n in with_pr["signals"]))

        off_policy = S.apply_switches(self.policy, [], ["peer-review"])
        without_pr = score("https://arxiv.org/abs/2201.88888", policy=off_policy, injected=rec)
        self.assertFalse(any(n.startswith("published@") for n in without_pr["signals"]))
        self.assertLess(without_pr["score"], with_pr["score"])

    def test_disabling_recency_decay_zeroes_recency_contribution(self):
        # nature.com is not a preprint host, so peer-review logic never fires
        # here and the score difference is purely the recency term.
        rec = {"scholar": {"citations": 50, "year": 2018, "age_years": 8.0,
                            "peer_reviewed": True, "venue": "Nature"}}
        with_decay = score("https://www.nature.com/articles/aged-paper", injected=rec)
        off_policy = S.apply_switches(self.policy, [], ["recency-decay"])
        without_decay = score("https://www.nature.com/articles/aged-paper",
                              policy=off_policy, injected=rec)
        self.assertNotEqual(with_decay["score"], without_decay["score"])
        self.assertEqual(S.recency_points(off_policy, 8.0, "ai", 50), 0.0)
        self.assertLess(S.recency_points(self.policy, 8.0, "ai", 50), 0.0)
        # decay is a penalty here, so removing it can only raise (or leave
        # unchanged) the total score
        self.assertGreaterEqual(without_decay["score"], with_decay["score"])

    def test_disabling_engagement_drops_github_contribution(self):
        rec = {"github": {"stars": 50000, "archived": False}}
        with_eng = score("https://example.com/some-repo-mirror", injected=rec)
        off_policy = S.apply_switches(self.policy, [], ["engagement"])
        without_eng = score("https://example.com/some-repo-mirror",
                            policy=off_policy, injected=rec)
        self.assertGreater(with_eng["score"], without_eng["score"])
        self.assertFalse(any(n.startswith("★") for n in without_eng["signals"]))
        self.assertTrue(any(n.startswith("★") for n in with_eng["signals"]))


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

    def test_bad_change_half_life_pair_exits_nonzero_with_message(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = CLI.main(["--change-half-life", "zz:2", "--no-net",
                          "-u", "https://example.com"])
        self.assertNotEqual(rc, 0)
        self.assertIn("zz", err.getvalue())

    def test_combined_mode_adjust_disable_does_not_crash(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = CLI.main([
                "--mode", "non-academic",
                "--adjust", "engagement.github.cap:-2",
                "--disable", "engagement",
                "--no-net", "-u", "https://arxiv.org/abs/2201.11111",
                "--format", "json",
            ])
        self.assertEqual(rc, 0, msg=err.getvalue())
        rows = json.loads(out.getvalue())
        self.assertEqual(len(rows), 1)
        self.assertIn("score", rows[0])

    def test_multi_value_per_flag_change_half_life(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = CLI.main([
                "--change-half-life", "ai:1", "cs:2",
                "--no-net", "-u", "https://example.com", "--format", "json",
            ])
        self.assertEqual(rc, 0, msg=err.getvalue())

    def test_bad_adjust_path_exits_nonzero(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = CLI.main(["--adjust", "no.such.path:1", "--no-net",
                          "-u", "https://example.com"])
        self.assertNotEqual(rc, 0)
        self.assertIn("no such policy path", err.getvalue())


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
