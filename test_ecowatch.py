import tempfile
import unittest
from pathlib import Path

import ecowatch

SAMPLE = b'''<?xml version="1.0"?><rss version="2.0"><channel><title>Sample</title><item><title>Oil spill threatens coastal wildlife</title><link>https://example.test/story</link><description><![CDATA[An oil spill has contaminated coastal water. Wildlife groups are monitoring the affected shore. More details here.]]></description><pubDate>Thu, 29 Oct 2026 10:00:00 GMT</pubDate><guid>story-1</guid></item></channel></rss>'''


class EcoWatchTests(unittest.TestCase):
    def test_parse_rss_extracts_fields_and_strips_markup(self):
        items = ecowatch.parse_feed(SAMPLE, "Sample Source")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "Oil spill threatens coastal wildlife")
        self.assertEqual(items[0]["source"], "Sample Source")
        self.assertIn("oil spill", items[0]["description"].lower())
        self.assertEqual(items[0]["date"], "Thu, 29 Oct 2026 10:00:00 GMT")

    def test_parse_atom_extracts_link_and_published_date(self):
        atom = b'''<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>tag:example.test,2026:1</id><title>Wetland habitat restored</title><link href="https://example.test/atom"/><summary>Native wildlife returns to a restored wetland.</summary><published>2026-10-29T09:00:00Z</published></entry></feed>'''
        items = ecowatch.parse_feed(atom, "Atom Source")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["link"], "https://example.test/atom")
        self.assertEqual(items[0]["date"], "2026-10-29T09:00:00Z")
        self.assertEqual(items[0]["id"], "tag:example.test,2026:1")

    def test_classification_matches_category_keywords(self):
        item = {"title": "Plastic waste in river", "description": "Illegal dumping causes pollution"}
        self.assertEqual(ecowatch.classify_item(item), "waste management")

    def test_concern_severity_returns_reason_keywords(self):
        item = {"title": "Toxic spill contaminates water", "description": "Fish die after chemical leak"}
        concern = ecowatch.assess_concern(item)
        self.assertEqual(concern["severity"], "high")
        self.assertTrue(any(word in concern["matched_keywords"] for word in ("toxic", "spill", "contaminates", "die", "leak")))

    def test_change_detection_only_returns_unseen_then_persists(self):
        with tempfile.TemporaryDirectory() as temp:
            state_path = Path(temp) / "state.json"
            item = {"id": "stable-1", "title": "T", "link": "https://e.test/1"}
            self.assertEqual(ecowatch.only_new_items([item], state_path), [item])
            self.assertEqual(ecowatch.only_new_items([item], state_path), [])

    def test_last_report_retains_latest_nonempty_run_and_updates_check_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "last_report.json"
            first = {"id": "one", "title": "First"}
            data = ecowatch.update_last_report(path, [first], [{"name": "Feed", "status": "OK"}], "10:00", 1)
            self.assertEqual(data["items"], [first])
            second = ecowatch.update_last_report(path, [], [{"name": "Feed", "status": "OK"}], "11:00", 0)
            self.assertEqual(second["items"], [first])
            self.assertEqual(second["last_checked"], "11:00")
            self.assertEqual(second["new_this_run"], 0)

    def test_reset_state_removes_existing_state_file(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "state.json"
            path.write_text('{"seen":["old"]}', encoding="utf-8")
            ecowatch.reset_state(path)
            self.assertFalse(path.exists())

    def test_summary_is_extract_from_description(self):
        summary = ecowatch.extractive_summary("First sentence. Second sentence! Third.")
        self.assertEqual(summary, "First sentence. Second sentence!")


if __name__ == "__main__":
    unittest.main()
