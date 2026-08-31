"""Tests for bounded listing, reading, and literal source search."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from coding_agent.domain import ToolCall
from coding_agent.tools import ToolRegistry
from coding_agent.tools.files import create_read_only_tools
from coding_agent.workspace import Workspace


class ReadOnlyToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.registry = ToolRegistry(create_read_only_tools(Workspace(self.root)))

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def execute(self, name: str, arguments: str):
        return self.registry.execute(ToolCall("call_1", name, arguments))

    def test_read_file_preserves_text_and_reports_pagination(self) -> None:
        (self.root / "notes.txt").write_bytes(
            "第一行\nsecond line\nthird line\n".encode("utf-8")
        )

        result = self.execute(
            "read_file",
            '{"path":"notes.txt","start_line":2,"max_lines":1}',
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.data["content"], "second line\n")
        self.assertEqual(result.data["start_line"], 2)
        self.assertEqual(result.data["end_line"], 2)
        self.assertEqual(result.data["total_lines"], 3)
        self.assertEqual(result.data["next_start_line"], 3)
        self.assertTrue(result.data["truncated"])

    def test_read_rejects_binary_and_outside_paths(self) -> None:
        (self.root / "binary.dat").write_bytes(b"text\x00binary")

        binary_result = self.execute("read_file", '{"path":"binary.dat"}')
        outside_result = self.execute("read_file", '{"path":"../secret.txt"}')

        self.assertEqual(binary_result.error.code, "BINARY_FILE")
        self.assertEqual(outside_result.error.code, "OUTSIDE_WORKSPACE")

    def test_read_rejects_line_beyond_file(self) -> None:
        (self.root / "one.txt").write_text("only\n", encoding="utf-8")

        result = self.execute(
            "read_file",
            '{"path":"one.txt","start_line":2}',
        )

        self.assertEqual(result.error.code, "INVALID_ARGUMENTS")

    def test_list_files_is_sorted_and_skips_generated_directories(self) -> None:
        (self.root / "src").mkdir()
        (self.root / "src" / "z.py").write_text("", encoding="utf-8")
        (self.root / "src" / "a.py").write_text("", encoding="utf-8")
        (self.root / ".git").mkdir()
        (self.root / ".git" / "config").write_text("private", encoding="utf-8")
        (self.root / ".env").write_text("TOKEN=private", encoding="utf-8")
        (self.root / ".env.example").write_text("TOKEN=replace-me", encoding="utf-8")

        result = self.execute("list_files", '{"path":"."}')

        self.assertTrue(result.ok)
        self.assertEqual(
            result.data["files"],
            [".env.example", "src/a.py", "src/z.py"],
        )

    def test_direct_read_and_search_cannot_bypass_protected_paths(self) -> None:
        secret = "sentinel-private-value"
        (self.root / ".env").write_text(f"TOKEN={secret}\n", encoding="utf-8")
        (self.root / ".git").mkdir()
        (self.root / ".git" / "config").write_text(secret, encoding="utf-8")

        read_env = self.execute("read_file", '{"path":".env"}')
        read_git = self.execute("read_file", '{"path":".git/config"}')
        search_git = self.execute(
            "search_text",
            '{"query":"sentinel","path":".git"}',
        )
        search_root = self.execute("search_text", '{"query":"sentinel"}')

        self.assertEqual(read_env.error.code, "PROTECTED_PATH")
        self.assertEqual(read_git.error.code, "PROTECTED_PATH")
        self.assertEqual(search_git.error.code, "PROTECTED_PATH")
        self.assertTrue(search_root.ok)
        self.assertEqual(search_root.data["count"], 0)
        self.assertNotIn(secret, search_root.to_json())

    def test_list_files_reports_truncation(self) -> None:
        for index in range(3):
            (self.root / f"{index}.txt").write_text("", encoding="utf-8")

        result = self.execute("list_files", '{"max_results":2}')

        self.assertEqual(result.data["count"], 2)
        self.assertTrue(result.data["truncated"])

    def test_search_finds_case_insensitive_matches_with_locations(self) -> None:
        (self.root / "a.py").write_text(
            "print('Hello')\n# HELLO again\n",
            encoding="utf-8",
        )

        result = self.execute("search_text", '{"query":"hello"}')

        self.assertTrue(result.ok)
        self.assertEqual(result.data["count"], 2)
        self.assertEqual(result.data["matches"][0]["line"], 1)
        self.assertEqual(result.data["matches"][0]["column"], 8)

    def test_search_skips_binary_files_and_honors_limit(self) -> None:
        (self.root / "binary.dat").write_bytes(b"needle\x00data")
        (self.root / "text.txt").write_text("needle\nneedle\n", encoding="utf-8")

        result = self.execute(
            "search_text",
            '{"query":"needle","max_results":1}',
        )

        self.assertEqual(result.data["count"], 1)
        self.assertTrue(result.data["truncated"])
        self.assertEqual(result.data["matches"][0]["path"], "text.txt")

    def test_unexpected_and_wrong_typed_arguments_are_rejected(self) -> None:
        extra = self.execute("list_files", '{"unknown":true}')
        wrong_type = self.execute("read_file", '{"path":"x","max_lines":true}')

        self.assertEqual(extra.error.code, "INVALID_ARGUMENTS")
        self.assertEqual(wrong_type.error.code, "INVALID_ARGUMENTS")

    def test_tool_schemas_are_available_to_model(self) -> None:
        names = [schema["function"]["name"] for schema in self.registry.schemas()]

        self.assertEqual(names, ["list_files", "read_file", "search_text"])


if __name__ == "__main__":
    unittest.main()
