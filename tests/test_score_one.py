import unittest

import _pathsetup  # noqa: F401
import srcscore as S


def score(url, field="ai", use_net=False, injected=None):
    policy = S.load_policy()
    cache = S.NullCache()
    return S.score_one({"url": url, "title": ""}, policy, cache, field, use_net, injected)


class BlockAndRetractionTests(unittest.TestCase):
    def test_blocklisted_host_scores_zero(self):
        row = score("https://www.scribd.com/document/1")
        self.assertEqual(row["score"], 0.0)
        self.assertEqual(row["verdict"], "BLOCKED")
        self.assertIn("blocklist", row["flags"])

    def test_retracted_paper_is_blocked_regardless_of_citations(self):
        row = score(
            "https://www.nature.com/articles/x",
            injected={"scholar": {"citations": 9000, "year": 2020, "age_years": 5.0,
                                   "peer_reviewed": True, "is_retracted": True}})
        self.assertEqual(row["score"], 0.0)
        self.assertEqual(row["verdict"], "BLOCKED")
        self.assertIn("RETRACTED", row["flags"])


class PeerReviewTests(unittest.TestCase):
    def test_published_preprint_gets_bonus_not_penalty(self):
        row = score(
            "https://arxiv.org/abs/2201.11111",
            injected={"scholar": {"citations": 60, "year": 2022, "age_years": 3.5,
                                   "peer_reviewed": True, "venue": "ICML"}})
        self.assertTrue(any(sig.startswith("published@") for sig in row["signals"]))
        self.assertNotIn("preprint", row["flags"])

    def test_unpublished_preprint_gets_penalty_flag(self):
        row = score(
            "https://arxiv.org/abs/2601.22222",
            injected={"scholar": {"citations": 120, "year": 2026, "age_years": 0.5,
                                   "peer_reviewed": False}})
        self.assertIn("preprint", row["flags"])

    def test_new_uncited_preprint_stacks_unvetted_penalty(self):
        row = score(
            "https://arxiv.org/abs/2608.33333",
            injected={"scholar": {"citations": 0, "year": 2026, "age_years": 0.2,
                                   "peer_reviewed": False}})
        self.assertIn("preprint", row["flags"])
        self.assertIn("unvetted", row["flags"])

    def test_old_low_cite_preprint_does_not_get_unvetted_flag(self):
        row = score(
            "https://arxiv.org/abs/1301.44444",
            injected={"scholar": {"citations": 5, "year": 2013, "age_years": 13.0,
                                   "peer_reviewed": False}})
        self.assertIn("preprint", row["flags"])
        self.assertNotIn("unvetted", row["flags"])
        self.assertIn("low-cite", row["flags"])

    def test_peer_review_only_applies_to_preprint_hosts(self):
        row = score(
            "https://www.nature.com/articles/x",
            injected={"scholar": {"citations": 5, "year": 2013, "age_years": 13.0,
                                   "peer_reviewed": False}})
        self.assertNotIn("preprint", row["flags"])


class CitationGapTests(unittest.TestCase):
    def test_uncited_old_paper_is_flagged(self):
        row = score(
            "https://arxiv.org/abs/1111.11111",
            injected={"scholar": {"citations": 0, "year": 2015, "age_years": 5.0,
                                   "peer_reviewed": True, "venue": "X"}})
        self.assertIn("uncited", row["flags"])

    def test_first_matching_gap_rule_wins(self):
        # 0 citations, 5y old matches both "uncited" (>=2y) and "low-cite" (>=4y);
        # uncited is listed first in policy.json so it must win alone.
        row = score(
            "https://arxiv.org/abs/1111.22222",
            injected={"scholar": {"citations": 0, "year": 2015, "age_years": 5.0,
                                   "peer_reviewed": True, "venue": "X"}})
        gap_flags = [f for f in row["flags"] if f in ("uncited", "low-cite")]
        self.assertEqual(gap_flags, ["uncited"])

    def test_well_cited_paper_has_no_gap_flag(self):
        row = score(
            "https://arxiv.org/abs/1111.33333",
            injected={"scholar": {"citations": 500, "year": 2015, "age_years": 5.0,
                                   "peer_reviewed": True, "venue": "X"}})
        self.assertNotIn("uncited", row["flags"])
        self.assertNotIn("low-cite", row["flags"])


class SeoAndNoIndexTests(unittest.TestCase):
    def test_listicle_path_is_penalized(self):
        row = score("https://medium.com/best-python-libraries-2026")
        self.assertIn("seo-path", row["flags"])

    def test_normal_path_has_no_seo_flag(self):
        row = score("https://medium.com/some-normal-post")
        self.assertNotIn("seo-path", row["flags"])

    def test_academic_id_absent_from_every_database_is_flagged_no_index(self):
        # use_net=True with an injected-but-empty scholar record simulates "we
        # looked it up and nothing came back" without making a real HTTP call.
        row = score(
            "https://arxiv.org/abs/1706.03762", use_net=True,
            injected={"scholar": {}, "github": None, "hn": None})
        self.assertIn("no-index", row["flags"])

    def test_no_academic_id_never_gets_no_index(self):
        row = score("https://arxiv.org/some-non-paper-page", use_net=True,
                    injected={"scholar": {}, "github": None, "hn": None})
        self.assertNotIn("no-index", row["flags"])

    def test_fresh_arxiv_id_within_grace_gets_reduced_flag_not_flat_penalty(self):
        # ~2 weeks old, pinned via override so this test doesn't depend on
        # wall-clock date.
        row = score(
            "https://arxiv.org/abs/2608.10101", use_net=True,
            injected={"scholar": {}, "github": None, "hn": None,
                      "arxiv_age_years": 0.04})
        self.assertIn("no-index-recent", row["flags"])
        self.assertNotIn("no-index", row["flags"])
        self.assertEqual(row["score"], 52.0)  # 60 (tier 3) - 8 (grace penalty)
        self.assertEqual(row["verdict"], "SKIM")

    def test_arxiv_id_two_months_old_still_within_grace(self):
        row = score(
            "https://arxiv.org/abs/2606.20202", use_net=True,
            injected={"scholar": {}, "github": None, "hn": None,
                      "arxiv_age_years": 0.167})  # ~2 months
        self.assertIn("no-index-recent", row["flags"])
        self.assertEqual(row["score"], 52.0)

    def test_arxiv_id_eight_months_old_past_grace_gets_flat_no_index(self):
        row = score(
            "https://arxiv.org/abs/2512.30303", use_net=True,
            injected={"scholar": {}, "github": None, "hn": None,
                      "arxiv_age_years": 0.667})  # ~8 months
        self.assertIn("no-index", row["flags"])
        self.assertNotIn("no-index-recent", row["flags"])
        self.assertEqual(row["score"], 55.0)  # 60 - 5 (flat, unchanged)

    def test_very_old_unindexed_arxiv_id_unchanged_by_grace(self):
        # 1706.03762 decodes to 2017-06, many years past the grace window;
        # confirms real (non-overridden) id decoding still works.
        row = score(
            "https://arxiv.org/abs/1706.03762", use_net=True,
            injected={"scholar": {}, "github": None, "hn": None})
        self.assertIn("no-index", row["flags"])
        self.assertNotIn("no-index-recent", row["flags"])

    def test_doi_no_index_penalty_unaffected_by_arxiv_grace_logic(self):
        row = score(
            "https://doi.org/10.1234/example.5678", use_net=True,
            injected={"scholar": {}, "github": None, "hn": None})
        self.assertIn("no-index", row["flags"])
        self.assertNotIn("no-index-recent", row["flags"])

    def test_arxiv_id_age_years_decodes_new_and_old_schemes(self):
        self.assertAlmostEqual(
            S.arxiv_id_age_years("2506.03762"), S.age_years("2025-06-01", None))
        self.assertAlmostEqual(
            S.arxiv_id_age_years("hep-th/9901001"), S.age_years("1999-01-01", None))
        self.assertIsNone(S.arxiv_id_age_years(""))
        self.assertIsNone(S.arxiv_id_age_years("not-an-id"))


class ClampingTests(unittest.TestCase):
    def test_score_never_exceeds_100(self):
        row = score(
            "https://www.nature.com/articles/x",
            injected={"scholar": {"citations": 10_000_000, "year": 2024, "age_years": 0.1,
                                   "peer_reviewed": True, "venue": "Nature"}})
        self.assertLessEqual(row["score"], 100.0)

    def test_score_never_goes_below_zero(self):
        row = score(
            "https://www.w3schools.com/best-top-10-in-2023",
            injected={"scholar": {"citations": 0, "year": 1990, "age_years": 40.0,
                                   "peer_reviewed": False}})
        self.assertGreaterEqual(row["score"], 0.0)


if __name__ == "__main__":
    unittest.main()
