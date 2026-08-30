"""Tests for approval-gated atomic writes and exact edits."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from coding_agent.domain import ToolCall
from coding_agent.policy import AllowAllPolicy
from coding_agent.tools import ToolRegistry, create_write_tools
from coding_agent.workspace import Workspace


class EditingToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.registry = ToolRegistry(create_write_tools(Workspace(self.root)))
        self.allow = AllowAllPolicy()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def execute(self, name: str, arguments: str, *, approved: bool = True):
        policy = self.allow if approved else None
        return self.registry.execute(ToolCall("call_1", name, arguments), policy)

    def test_write_is_denied_by_default_without_touching_disk(self) -> None:
        result = self.execute(
            "write_file",
            '{"path":"new.txt","content":"hello"}',
            approved=False,
        )

        self.assertEqual(result.error.code, "PERMISSION_DENIED")
        self.assertFalse((self.root / "new.txt").exists())

    def test_write_creates_new_utf8_file_when_approved(self) -> None:
        result = self.execute(
            "write_file",
            '{"path":"new.txt","content":"你好\\n"}',
        )

        self.assertTrue(result.ok)
        self.assertTrue(result.data["created"])
        self.assertEqual((self.root / "new.txt").read_bytes(), "你好\n".encode("utf-8"))

    def test_write_requires_explicit_overwrite(self) -> None:
        target = self.root / "existing.txt"
        target.write_text("old", encoding="utf-8")

        denied = self.execute(
            "write_file",
            '{"path":"existing.txt","content":"new"}',
        )
        allowed = self.execute(
            "write_file",
            '{"path":"existing.txt","content":"new","overwrite":true}',
        )

        self.assertEqual(denied.error.code, "ALREADY_EXISTS")
        self.assertTrue(allowed.ok)
        self.assertEqual(target.read_text(encoding="utf-8"), "new")

    def test_exact_edit_changes_one_match_and_returns_diff(self) -> None:
        target = self.root / "main.py"
        target.write_bytes(b"value = 1\r\nprint(value)\r\n")

        result = self.execute(
            "edit_file",
            '{"path":"main.py","old_text":"value = 1","new_text":"value = 2"}',
        )

        self.assertTrue(result.ok)
        self.assertIn("-value = 1", result.data["diff"])
        self.assertIn("+value = 2", result.data["diff"])
        self.assertEqual(target.read_bytes(), b"value = 2\r\nprint(value)\r\n")

    def test_ambiguous_edit_does_not_modify_file(self) -> None:
        target = self.root / "repeat.txt"
        original = b"same\nsame\n"
        target.write_bytes(original)

        result = self.execute(
            "edit_file",
            '{"path":"repeat.txt","old_text":"same","new_text":"changed"}',
        )

        self.assertEqual(result.error.code, "AMBIGUOUS_MATCH")
        self.assertEqual(target.read_bytes(), original)

    def test_replace_all_must_be_explicit(self) -> None:
        target = self.root / "repeat.txt"
        target.write_bytes(b"same\nsame\n")

        result = self.execute(
            "edit_file",
            '{"path":"repeat.txt","old_text":"same","new_text":"new","replace_all":true}',
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.data["replacements"], 2)
        self.assertEqual(target.read_bytes(), b"new\nnew\n")

    def test_missing_text_and_outside_path_leave_files_unchanged(self) -> None:
        target = self.root / "file.txt"
        target.write_text("original", encoding="utf-8")

        missing = self.execute(
            "edit_file",
            '{"path":"file.txt","old_text":"absent","new_text":"new"}',
        )
        outside = self.execute(
            "write_file",
            '{"path":"../outside.txt","content":"bad"}',
        )

        self.assertEqual(missing.error.code, "TEXT_NOT_FOUND")
        self.assertEqual(outside.error.code, "OUTSIDE_WORKSPACE")
        self.assertEqual(target.read_text(encoding="utf-8"), "original")

    def test_missing_parent_is_reported_without_creating_directories(self) -> None:
        result = self.execute(
            "write_file",
            '{"path":"missing/new.txt","content":"text"}',
        )

        self.assertEqual(result.error.code, "PARENT_NOT_FOUND")
        self.assertFalse((self.root / "missing").exists())


if __name__ == "__main__":
    unittest.main()
