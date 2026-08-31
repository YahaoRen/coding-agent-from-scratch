"""Tests that model-supplied paths cannot escape the workspace."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from coding_agent.workspace import Workspace, WorkspaceError


class WorkspaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name, "work")
        self.root.mkdir()
        self.workspace = Workspace(self.root)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_resolves_normal_file_and_hides_absolute_root(self) -> None:
        nested = self.root / "src"
        nested.mkdir()
        file_path = nested / "main.py"
        file_path.write_text("print('ok')\n", encoding="utf-8")

        resolved = self.workspace.resolve_file("src/main.py")

        self.assertEqual(resolved, file_path.resolve())
        self.assertEqual(self.workspace.display_path(resolved), "src/main.py")

    def test_dot_resolves_to_workspace_directory(self) -> None:
        self.assertEqual(self.workspace.resolve_directory("."), self.root.resolve())

    def test_parent_escape_is_rejected(self) -> None:
        outside = self.root.parent / "outside.txt"
        outside.write_text("secret", encoding="utf-8")

        with self.assertRaises(WorkspaceError) as raised:
            self.workspace.resolve("../outside.txt")

        self.assertEqual(raised.exception.code, "OUTSIDE_WORKSPACE")

    def test_absolute_paths_are_rejected(self) -> None:
        with self.assertRaises(WorkspaceError) as raised:
            self.workspace.resolve(str(self.root.resolve()))

        self.assertEqual(raised.exception.code, "ABSOLUTE_PATH")

    def test_windows_drive_and_unc_paths_are_rejected_on_every_platform(self) -> None:
        for path_text in (r"C:\Windows\system.ini", r"\\server\share\file.txt"):
            with self.subTest(path=path_text):
                with self.assertRaises(WorkspaceError) as raised:
                    self.workspace.resolve(path_text, must_exist=False)
                self.assertEqual(raised.exception.code, "ABSOLUTE_PATH")

    def test_similar_directory_prefix_does_not_bypass_boundary(self) -> None:
        sibling = self.root.parent / "work-evil"
        sibling.mkdir()
        secret = sibling / "secret.txt"
        secret.write_text("secret", encoding="utf-8")

        with self.assertRaises(WorkspaceError) as raised:
            self.workspace.resolve("../work-evil/secret.txt")

        self.assertEqual(raised.exception.code, "OUTSIDE_WORKSPACE")

    def test_missing_path_has_stable_error_code(self) -> None:
        with self.assertRaises(WorkspaceError) as raised:
            self.workspace.resolve("missing.txt")

        self.assertEqual(raised.exception.code, "NOT_FOUND")

    def test_nonexistent_path_can_be_resolved_for_future_write(self) -> None:
        target = self.workspace.resolve("new.txt", must_exist=False)

        self.assertEqual(target, self.root.resolve() / "new.txt")

    def test_credential_files_and_internal_metadata_are_protected(self) -> None:
        (self.root / ".env").write_text("TOKEN=secret", encoding="utf-8")
        (self.root / ".git").mkdir()
        (self.root / ".git" / "config").write_text("private", encoding="utf-8")

        for path_text in (".env", ".git", ".git/config"):
            with self.subTest(path=path_text):
                with self.assertRaises(WorkspaceError) as raised:
                    self.workspace.resolve(path_text)
                self.assertEqual(raised.exception.code, "PROTECTED_PATH")

    def test_dotenv_example_remains_visible(self) -> None:
        example = self.root / ".env.example"
        example.write_text("TOKEN=replace-me", encoding="utf-8")

        self.assertEqual(self.workspace.resolve_file(".env.example"), example.resolve())

    def test_cli_can_protect_a_custom_configuration_path(self) -> None:
        custom = self.root / "model.config"
        custom.write_text("private", encoding="utf-8")
        workspace = Workspace(self.root, protected_paths=(custom,))

        with self.assertRaises(WorkspaceError) as raised:
            workspace.resolve_file("model.config")

        self.assertEqual(raised.exception.code, "PROTECTED_PATH")

    def test_protected_files_cannot_be_created_or_edited(self) -> None:
        with self.assertRaises(WorkspaceError) as raised:
            self.workspace.resolve_for_write(".env.local")

        self.assertEqual(raised.exception.code, "PROTECTED_PATH")

    def test_file_and_directory_types_are_checked(self) -> None:
        file_path = self.root / "file.txt"
        file_path.write_text("text", encoding="utf-8")

        with self.assertRaises(WorkspaceError) as file_error:
            self.workspace.resolve_file(".")
        with self.assertRaises(WorkspaceError) as directory_error:
            self.workspace.resolve_directory("file.txt")

        self.assertEqual(file_error.exception.code, "NOT_A_FILE")
        self.assertEqual(directory_error.exception.code, "NOT_A_DIRECTORY")

    @unittest.skipIf(not hasattr(os, "symlink"), "symbolic links are unavailable")
    def test_symlink_to_outside_is_rejected(self) -> None:
        outside = self.root.parent / "outside-target.txt"
        outside.write_text("secret", encoding="utf-8")
        link = self.root / "link.txt"
        try:
            link.symlink_to(outside)
        except OSError as error:
            self.skipTest(f"symbolic links are not permitted: {error}")

        with self.assertRaises(WorkspaceError) as raised:
            self.workspace.resolve_file("link.txt")

        self.assertEqual(raised.exception.code, "OUTSIDE_WORKSPACE")


if __name__ == "__main__":
    unittest.main()
