import tempfile
import unittest
from pathlib import Path

from radar.obsidian import export_idea


class ObsidianExportTests(unittest.TestCase):
    def test_safe_filename_content_and_preserved_user_edits(self):
        idea = {"id": "knee-1", "title": '무릎: 연구/비교? "결과"',
                "savedAt": "2026-09-19T20:00:00+09:00", "pico": "대상과 비교군",
                "evidence": [{"pmid": "123", "title": "근거 논문"}]}
        with tempfile.TemporaryDirectory() as temporary:
            path = export_idea(idea, temporary)
            self.assertTrue(path.name.startswith("2026-09-19 - 무릎"))
            self.assertFalse(any(c in path.name for c in '<>:"/\\|?*'))
            content = path.read_text("utf-8")
            self.assertIn(idea["title"], content)
            self.assertIn(idea["savedAt"], content)
            self.assertIn("https://pubmed.ncbi.nlm.nih.gov/123/", content)
            path.write_text("사용자가 편집한 노트", encoding="utf-8")
            self.assertEqual(export_idea({**idea, "title": "바뀐 제목"}, temporary), path)
            self.assertEqual(path.read_text("utf-8"), "사용자가 편집한 노트")
            other = export_idea({**idea, "id": "knee-2"}, temporary)
            self.assertNotEqual(path, other)

    def test_missing_folder_is_reported(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(OSError):
                export_idea({"id": "1"}, Path(temporary) / "missing")


if __name__ == "__main__":
    unittest.main()
