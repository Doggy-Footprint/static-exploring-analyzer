import unittest

import _pathsetup  # noqa: F401
import srcscore as S


class ParseInputTests(unittest.TestCase):
    def test_empty_input(self):
        self.assertEqual(S.parse_input(""), [])
        self.assertEqual(S.parse_input("   \n  "), [])

    def test_plain_url_lines(self):
        out = S.parse_input("https://a.com/1\nhttps://b.com/2\n")
        self.assertEqual([o["url"] for o in out], ["https://a.com/1", "https://b.com/2"])
        self.assertEqual(out[0]["title"], "")

    def test_bulleted_lines_are_stripped(self):
        out = S.parse_input("- https://a.com/1\n* https://b.com/2\n• https://c.com/3")
        self.assertEqual([o["url"] for o in out],
                         ["https://a.com/1", "https://b.com/2", "https://c.com/3"])

    def test_url_pipe_title(self):
        out = S.parse_input("https://a.com/1 | My Title")
        self.assertEqual(out[0]["url"], "https://a.com/1")
        self.assertEqual(out[0]["title"], "My Title")

    def test_title_pipe_url(self):
        out = S.parse_input("My Title | https://a.com/1")
        self.assertEqual(out[0]["url"], "https://a.com/1")
        self.assertEqual(out[0]["title"], "My Title")

    def test_trailing_punctuation_is_stripped(self):
        out = S.parse_input("see https://a.com/1).")
        self.assertEqual(out[0]["url"], "https://a.com/1")

    def test_duplicate_urls_are_deduplicated(self):
        out = S.parse_input("https://a.com/1\nhttps://a.com/1\n")
        self.assertEqual(len(out), 1)

    def test_line_without_url_is_skipped(self):
        out = S.parse_input("no link here\nhttps://a.com/1")
        self.assertEqual(len(out), 1)

    def test_json_array_of_strings(self):
        out = S.parse_input('["https://a.com/1", "https://b.com/2"]')
        self.assertEqual([o["url"] for o in out], ["https://a.com/1", "https://b.com/2"])

    def test_json_array_of_objects(self):
        out = S.parse_input('[{"url": "https://a.com/1", "title": "A"}]')
        self.assertEqual(out[0], {"url": "https://a.com/1", "title": "A"})

    def test_json_object_with_results_key(self):
        out = S.parse_input('{"results": [{"url": "https://a.com/1"}]}')
        self.assertEqual(out[0]["url"], "https://a.com/1")

    def test_json_object_with_urls_key(self):
        out = S.parse_input('{"urls": ["https://a.com/1"]}')
        self.assertEqual(out[0]["url"], "https://a.com/1")


class TierLabelTests(unittest.TestCase):
    def test_block_prints_as_zero(self):
        self.assertEqual(S.tier_label("block"), "0")

    def test_numeric_tier_is_passed_through(self):
        self.assertEqual(S.tier_label("3"), "3")


class RenderTests(unittest.TestCase):
    def setUp(self):
        self.rows = [
            {"score": 98.0, "verdict": "PRIMARY", "tier": "3", "signals": ["cit=132.0k"],
             "flags": [], "url": "https://arxiv.org/abs/1706.03762", "title": "Attention"},
            {"score": 0.0, "verdict": "BLOCKED", "tier": "block", "signals": [],
             "flags": ["blocklist"], "url": "https://www.scribd.com/x", "title": ""},
        ]

    def test_render_table_empty(self):
        self.assertEqual(S.render_table([]), "(no sources)")

    def test_render_table_includes_summary_line(self):
        out = S.render_table(self.rows)
        self.assertIn("2 sources | 1 citable | 1 dropped", out)
        self.assertIn("PRIMARY", out)
        self.assertIn("0", out)  # blocked tier prints as 0

    def test_render_md_uses_title_or_url_and_escapes_pipe(self):
        rows = [dict(self.rows[1], title="A | B")]
        out = S.render_md(rows)
        self.assertIn("A / B", out)
        self.assertNotIn("A | B |", out)


if __name__ == "__main__":
    unittest.main()
