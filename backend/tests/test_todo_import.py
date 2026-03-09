import unittest

from app.services.todo_import import extract_import_ids, parse_todo_markdown, stable_import_id


class TodoImportParsingTests(unittest.TestCase):
    def test_parse_skips_completed_by_default(self):
        content = """
# Phase 1
- [x] Completed item
- [ ] Open item
"""
        items = parse_todo_markdown(content, "/tmp/TODO.md")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].text, "Open item")
        self.assertEqual(items[0].heading_path, "Phase 1")

    def test_parse_can_include_completed(self):
        content = """
## Track
- [x] Done
- [ ] Next
"""
        items = parse_todo_markdown(content, "/tmp/TODO.md", include_completed=True)
        self.assertEqual(len(items), 2)
        self.assertTrue(items[0].completed)
        self.assertFalse(items[1].completed)

    def test_import_id_is_stable(self):
        first = stable_import_id(source_path="/tmp/TODO.md", heading_path="P1", text="Task A")
        second = stable_import_id(source_path="/tmp/TODO.md", heading_path="P1", text="Task A")
        self.assertEqual(first, second)
        self.assertEqual(len(first), 16)

    def test_extract_import_ids_from_description(self):
        ids = extract_import_ids("foo [todo-import-id:abc123def4567890] bar [todo-import-id:1111222233334444]")
        self.assertEqual(ids, {"abc123def4567890", "1111222233334444"})


if __name__ == "__main__":
    unittest.main()
