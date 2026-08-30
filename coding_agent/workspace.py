"""Resolve every model-supplied path inside one fixed workspace root."""

from __future__ import annotations

from pathlib import Path, PurePosixPath, PureWindowsPath


class WorkspaceError(ValueError):
    """A safe path error with a stable code for tool responses."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class Workspace:
    """The only component allowed to turn model path text into real paths."""

    def __init__(self, root: Path) -> None:
        try:
            resolved_root = root.expanduser().resolve(strict=True)
        except (OSError, RuntimeError) as error:
            raise WorkspaceError("INVALID_WORKSPACE", "Workspace does not exist") from error
        if not resolved_root.is_dir():
            raise WorkspaceError("INVALID_WORKSPACE", "Workspace must be a directory")
        self._root = resolved_root

    @property
    def root(self) -> Path:
        return self._root

    def resolve(self, relative_path: str, *, must_exist: bool = True) -> Path:
        """Resolve a relative path and reject every route outside the root."""

        normalized = self._validate_path_text(relative_path)
        unresolved_target = self._root / normalized
        try:
            target = unresolved_target.resolve(strict=False)
            self._ensure_inside(target)
            if must_exist:
                target = unresolved_target.resolve(strict=True)
        except FileNotFoundError as error:
            raise WorkspaceError("NOT_FOUND", f"Path not found: {relative_path}") from error
        except WorkspaceError:
            raise
        except (OSError, RuntimeError) as error:
            raise WorkspaceError("INVALID_PATH", f"Invalid path: {relative_path}") from error

        self._ensure_inside(target)
        return target

    def _ensure_inside(self, target: Path) -> None:
        try:
            target.relative_to(self._root)
        except ValueError as error:
            raise WorkspaceError(
                "OUTSIDE_WORKSPACE",
                "Path must stay inside the workspace",
            ) from error

    def resolve_file(self, relative_path: str) -> Path:
        target = self.resolve(relative_path)
        if not target.is_file():
            raise WorkspaceError("NOT_A_FILE", f"Not a file: {relative_path}")
        return target

    def resolve_directory(self, relative_path: str = ".") -> Path:
        target = self.resolve(relative_path)
        if not target.is_dir():
            raise WorkspaceError("NOT_A_DIRECTORY", f"Not a directory: {relative_path}")
        return target

    def resolve_for_write(self, relative_path: str) -> Path:
        """Resolve a writable file path with an existing in-workspace parent."""

        normalized = self._validate_path_text(relative_path)
        unresolved_target = self._root / normalized
        if unresolved_target.is_symlink():
            raise WorkspaceError(
                "SYMLINK_NOT_ALLOWED",
                "Writing through symbolic links is not allowed",
            )
        target = self.resolve(relative_path, must_exist=False)
        try:
            parent = target.parent.resolve(strict=True)
        except FileNotFoundError as error:
            raise WorkspaceError(
                "PARENT_NOT_FOUND",
                f"Parent directory does not exist: {relative_path}",
            ) from error
        self._ensure_inside(parent)
        if not parent.is_dir():
            raise WorkspaceError(
                "PARENT_NOT_FOUND",
                f"Parent is not a directory: {relative_path}",
            )
        if target.exists() and not target.is_file():
            raise WorkspaceError("NOT_A_FILE", f"Not a file: {relative_path}")
        return target

    def display_path(self, path: Path) -> str:
        """Return a stable slash-separated path without exposing the root."""

        try:
            return path.resolve().relative_to(self._root).as_posix() or "."
        except ValueError as error:
            raise WorkspaceError(
                "OUTSIDE_WORKSPACE",
                "Path must stay inside the workspace",
            ) from error

    @staticmethod
    def _validate_path_text(relative_path: str) -> str:
        if not isinstance(relative_path, str):
            raise WorkspaceError("INVALID_PATH", "Path must be text")
        path_text = relative_path.strip()
        if not path_text:
            raise WorkspaceError("INVALID_PATH", "Path cannot be empty")
        if "\x00" in path_text:
            raise WorkspaceError("INVALID_PATH", "Path cannot contain NUL bytes")

        windows_path = PureWindowsPath(path_text)
        posix_path = PurePosixPath(path_text)
        if windows_path.is_absolute() or windows_path.drive or posix_path.is_absolute():
            raise WorkspaceError("ABSOLUTE_PATH", "Only relative paths are allowed")
        if ":" in path_text:
            raise WorkspaceError("INVALID_PATH", "Path cannot contain ':'")
        return path_text
