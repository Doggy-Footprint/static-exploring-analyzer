import math
import unittest

import _pathsetup  # noqa: F401
import srcscore as S


class CitationPointsTests(unittest.TestCase):
    def setUp(self):
        self.policy = S.load_policy()

    def test_zero_citations_gives_zero_points(self):
        cum, vel = S.citation_points(self.policy, 0, age=3.0)
        self.assertEqual(cum, 0.0)
        self.assertEqual(vel, 0.0)

    def test_matches_formula_below_cap(self):
        cfg = self.policy["citations"]
        c, age = 60, 3.5
        cum, vel = S.citation_points(self.policy, c, age)
        expect_cum = cfg["cumulative"]["coefficient"] * math.log10(1 + c)
        expect_vel = cfg["velocity"]["coefficient"] * math.log10(
            1 + c / max(cfg["velocity"]["min_age_years"], age))
        self.assertAlmostEqual(cum, min(cfg["cumulative"]["cap"], expect_cum))
        self.assertAlmostEqual(vel, min(cfg["velocity"]["cap"], expect_vel))

    def test_massive_citations_are_capped(self):
        cfg = self.policy["citations"]
        cum, vel = S.citation_points(self.policy, 132000, age=9.2)
        self.assertEqual(cum, cfg["cumulative"]["cap"])
        self.assertEqual(vel, cfg["velocity"]["cap"])

    def test_velocity_uses_age_floor_for_brand_new_papers(self):
        cfg = self.policy["citations"]
        floor = cfg["velocity"]["min_age_years"]
        cum1, vel1 = S.citation_points(self.policy, 10, age=0.01)
        cum2, vel2 = S.citation_points(self.policy, 10, age=floor)
        self.assertAlmostEqual(vel1, vel2)


class RecencyPointsTests(unittest.TestCase):
    def setUp(self):
        self.policy = S.load_policy()

    def test_brand_new_gets_top_fresh_bonus(self):
        pts = S.recency_points(self.policy, age=0.2, field="ai", citations=0)
        self.assertEqual(pts, self.policy["recency"]["fresh_bonuses"][0]["points"])

    def test_second_fresh_band(self):
        pts = S.recency_points(self.policy, age=1.5, field="ai", citations=0)
        self.assertEqual(pts, self.policy["recency"]["fresh_bonuses"][1]["points"])

    def test_decay_grows_negative_with_age(self):
        p1 = S.recency_points(self.policy, age=3.0, field="ai", citations=0)
        p2 = S.recency_points(self.policy, age=8.0, field="ai", citations=0)
        self.assertLess(p2, p1)
        self.assertLessEqual(p1, 0.0)

    def test_field_halflife_changes_decay_rate(self):
        p_ai = S.recency_points(self.policy, age=6.0, field="ai", citations=0)
        p_med = S.recency_points(self.policy, age=6.0, field="med", citations=0)
        # AI has a shorter half-life, so the same age decays harder.
        self.assertLess(p_ai, p_med)

    def test_classic_exemption_waives_decay_entirely(self):
        r = self.policy["recency"]
        pts = S.recency_points(
            self.policy, age=20.0, field="ai", citations=r["classic_exemption_citations"])
        self.assertGreaterEqual(pts, 0.0)

    def test_classic_softening_reduces_but_does_not_zero_decay(self):
        r = self.policy["recency"]
        soft_c = r["classic_softening"]["citations"]
        full = S.recency_points(self.policy, age=8.0, field="ai", citations=0)
        softened = S.recency_points(self.policy, age=8.0, field="ai", citations=soft_c)
        self.assertLess(full, softened)
        self.assertLess(softened, 0.0)


class EngagementPointsTests(unittest.TestCase):
    def setUp(self):
        self.policy = S.load_policy()

    def test_no_signals_gives_zero(self):
        pts, notes = S.engagement_points(self.policy, gh=None, hn=None)
        self.assertEqual(pts, 0.0)
        self.assertEqual(notes, [])

    def test_github_stars_are_capped(self):
        cfg = self.policy["engagement"]["github"]
        pts, notes = S.engagement_points(
            self.policy, gh={"stars": 10_000_000, "archived": False}, hn=None)
        self.assertEqual(pts, cfg["cap"])
        self.assertIn("★", notes[0])

    def test_archived_repo_applies_penalty(self):
        cfg = self.policy["engagement"]["github"]
        pts, notes = S.engagement_points(
            self.policy, gh={"stars": 100, "archived": True}, hn=None)
        expect = min(cfg["cap"], cfg["coefficient"] * math.log10(101)) + cfg["archived_penalty"]
        self.assertAlmostEqual(pts, expect)
        self.assertIn("archived", notes)

    def test_hackernews_points_are_capped(self):
        cfg = self.policy["engagement"]["hackernews"]
        pts, _ = S.engagement_points(self.policy, gh=None, hn={"points": 999999, "comments": 1})
        self.assertEqual(pts, cfg["cap"])


class HumanTests(unittest.TestCase):
    def test_small_number_is_unchanged(self):
        self.assertEqual(S.human(42), "42")

    def test_thousands_get_k_suffix(self):
        self.assertEqual(S.human(1500), "1.5k")

    def test_millions_get_m_suffix(self):
        self.assertEqual(S.human(2_500_000), "2.5M")

    def test_none_is_zero(self):
        self.assertEqual(S.human(None), "0")


if __name__ == "__main__":
    unittest.main()
