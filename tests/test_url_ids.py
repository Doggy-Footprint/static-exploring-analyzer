import unittest

import _pathsetup  # noqa: F401
import srcscore as S


class NormHostTests(unittest.TestCase):
    def test_strips_www_and_scheme(self):
        self.assertEqual(S.norm_host("https://www.nature.com/articles/x"), "nature.com")

    def test_no_scheme_is_assumed_https(self):
        self.assertEqual(S.norm_host("arxiv.org/abs/1706.03762"), "arxiv.org")

    def test_strips_userinfo_and_port(self):
        self.assertEqual(S.norm_host("https://user:pass@example.com:8080/x"), "example.com")

    def test_lowercases(self):
        self.assertEqual(S.norm_host("https://WWW.Nature.COM/x"), "nature.com")


class HostPathTests(unittest.TestCase):
    def test_joins_host_and_path(self):
        self.assertEqual(S.host_path("https://www.nature.com/news/story"), "nature.com/news/story")

    def test_no_path_is_bare_host(self):
        self.assertEqual(S.host_path("https://nature.com"), "nature.com")


class HostMatchesTests(unittest.TestCase):
    def test_exact_match(self):
        self.assertTrue(S.host_matches("nature.com", "nature.com"))

    def test_subdomain_matches(self):
        self.assertTrue(S.host_matches("link.springer.com", "springer.com"))

    def test_unrelated_suffix_does_not_match(self):
        self.assertFalse(S.host_matches("notnature.com", "nature.com"))

    def test_sibling_domain_does_not_match(self):
        self.assertFalse(S.host_matches("nature.com.evil.com", "nature.com"))


class ExtractIdsTests(unittest.TestCase):
    def test_arxiv_new_style(self):
        ids = S.extract_ids("https://arxiv.org/abs/1706.03762")
        self.assertEqual(ids["arxiv"], "1706.03762")

    def test_arxiv_versioned_id_is_stripped(self):
        ids = S.extract_ids("https://arxiv.org/pdf/1706.03762v5")
        self.assertEqual(ids["arxiv"], "1706.03762")

    def test_doi_with_trailing_punctuation_is_stripped(self):
        ids = S.extract_ids("See https://doi.org/10.1038/s41586-020-2649-2.")
        self.assertEqual(ids["doi"], "10.1038/s41586-020-2649-2")

    def test_biorxiv_doi_takes_priority_over_generic_doi(self):
        ids = S.extract_ids(
            "https://www.biorxiv.org/content/10.1101/2020.01.01.123456v2.full")
        self.assertEqual(ids["doi"], "10.1101/2020.01.01.123456")

    def test_github_repo_is_extracted(self):
        ids = S.extract_ids("https://github.com/pytorch/pytorch")
        self.assertEqual(ids["github"], ("pytorch", "pytorch"))

    def test_github_repo_strips_dot_git_suffix(self):
        ids = S.extract_ids("https://github.com/pytorch/pytorch.git")
        self.assertEqual(ids["github"], ("pytorch", "pytorch"))

    def test_github_non_repo_paths_are_excluded(self):
        for path in ("orgs/foo", "about", "topics/ai"):
            with self.subTest(path=path):
                ids = S.extract_ids("https://github.com/" + path)
                self.assertNotIn("github", ids)

    def test_pubmed_id_is_extracted(self):
        ids = S.extract_ids("https://pubmed.ncbi.nlm.nih.gov/12345678")
        self.assertEqual(ids["pmid"], "12345678")

    def test_openreview_id_is_extracted(self):
        ids = S.extract_ids("https://openreview.net/forum?id=abcDEF123")
        self.assertEqual(ids["openreview"], "abcDEF123")

    def test_no_ids_in_plain_url(self):
        self.assertEqual(S.extract_ids("https://example.com/post/1"), {})


class MatchTierTests(unittest.TestCase):
    def setUp(self):
        self.policy = S.load_policy()

    def test_host_only_pattern_matches(self):
        tier, pat = S.match_tier("https://www.nature.com/articles/x", self.policy)
        self.assertEqual(tier, "1")
        self.assertEqual(pat, "nature.com")

    def test_path_pattern_beats_host_pattern(self):
        tier, pat = S.match_tier("https://www.nature.com/news/some-story", self.policy)
        self.assertEqual(tier, "4")
        self.assertEqual(pat, "nature.com/news")

    def test_subdomain_matches_registered_host(self):
        tier, _ = S.match_tier("https://link.springer.com/article/x", self.policy)
        self.assertEqual(tier, "2")

    def test_unregistered_domain_falls_back_to_default_tier(self):
        tier, pat = S.match_tier("https://some-unknown-blog.example.org/post", self.policy)
        self.assertEqual(tier, self.policy["defaults"]["unregistered_tier"])
        self.assertIsNone(pat)

    def test_block_tier_is_reachable(self):
        tier, _ = S.match_tier("https://www.scribd.com/document/12345", self.policy)
        self.assertEqual(tier, "block")


if __name__ == "__main__":
    unittest.main()
