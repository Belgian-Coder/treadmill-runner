"""Contain filesystem access for harness install, export, and promotion operations."""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable


def absolute_path(path: Path) -> Path:
    """Return an absolute lexical path without following filesystem links."""

    expanded = path.expanduser()
    if not expanded.is_absolute():
        expanded = Path.cwd() / expanded
    return Path(os.path.abspath(os.fspath(expanded)))


def _has_unsupported_windows_root_namespace(path: Path) -> bool:
    if os.name != "nt":
        return False
    text = os.fspath(path).replace("/", "\\").casefold()
    return text.startswith(("\\\\?\\", "\\\\.\\", "\\??\\", "\\\\??\\"))


def normalize_relative_path(value: object) -> str:
    """Normalize a repository-relative path and reject cross-platform escapes."""

    if not isinstance(value, str):
        raise ValueError("path must be a string")
    text = value.replace("\\", "/")
    if not text:
        raise ValueError("path must not be empty")
    if text != text.strip():
        raise ValueError("leading or trailing whitespace is not allowed")
    if "\x00" in text:
        raise ValueError("path contains a NUL byte")
    if text.startswith("/") or text.startswith("//"):
        raise ValueError("absolute and UNC paths are not allowed")
    if len(text) >= 2 and text[1] == ":":
        raise ValueError("drive-qualified paths are not allowed")
    if ":" in text:
        raise ValueError("colon and alternate-data-stream paths are not allowed")
    parts = text.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("empty, current-directory, and traversal components are not allowed")
    reserved = {"CON", "PRN", "AUX", "NUL", "CLOCK$"}
    reserved.update(f"COM{index}" for index in range(1, 10))
    reserved.update(f"LPT{index}" for index in range(1, 10))
    for part in parts:
        if part.endswith((".", " ")):
            raise ValueError("components ending in a dot or space are not allowed")
        device_name = part.split(".", 1)[0].upper()
        if device_name in reserved:
            raise ValueError(f"Windows reserved device component is not allowed: {part}")
    return "/".join(parts)


def _is_reparse_stat(metadata: os.stat_result) -> bool:
    if stat.S_ISLNK(metadata.st_mode):
        return True
    attributes = getattr(metadata, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(reparse_flag and attributes & reparse_flag)


@dataclass(frozen=True)
class UnsafeHarnessPathError(ValueError):
    """A requested path escaped or crossed an indirection boundary."""

    path: str
    root: str
    operation: str
    reason: str

    def __str__(self) -> str:
        return f"{self.path}: {self.reason} ({self.operation})"

    def as_report_row(self) -> dict[str, str]:
        return {
            "path": self.path,
            "root": self.root,
            "operation": self.operation,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class RootRelationship:
    """Filesystem-identity relationship between two declared roots."""

    kind: str
    relative_path: str = ""
    reason: str = ""

    @property
    def overlaps(self) -> bool:
        return self.kind != "distinct"


def _identity_error(root: Path, operation: str, reason: str) -> UnsafeHarnessPathError:
    return UnsafeHarnessPathError(path=".", root=str(root), operation=operation, reason=reason)


def _identity_chain(
    root: Path,
    *,
    operation: str,
) -> list[tuple[Path, tuple[str, ...]]]:
    """Return existing ancestors with the unresolved suffix from each ancestor."""

    candidate = absolute_path(root)
    if _has_unsupported_windows_root_namespace(root) or _has_unsupported_windows_root_namespace(candidate):
        raise _identity_error(candidate, operation, "Windows device and extended-length namespace roots are not supported")
    suffix: tuple[str, ...] = ()
    while True:
        try:
            metadata = os.lstat(candidate)
        except FileNotFoundError:
            parent = candidate.parent
            if parent == candidate:
                raise _identity_error(candidate, operation, "no existing ancestor is available for identity comparison")
            if not candidate.name:
                raise _identity_error(candidate, operation, "could not preserve the unresolved root suffix")
            suffix = (candidate.name, *suffix)
            candidate = parent
            continue
        except OSError as exc:
            raise _identity_error(candidate, operation, f"could not inspect root identity ancestor: {exc}") from exc
        if _is_reparse_stat(metadata):
            raise _identity_error(
                candidate,
                operation,
                f"root identity ancestor is a symbolic link, junction, or reparse point: {candidate}",
            )
        if suffix and not stat.S_ISDIR(metadata.st_mode):
            raise _identity_error(candidate, operation, f"root identity ancestor is not a directory: {candidate}")
        break

    chain: list[tuple[Path, tuple[str, ...]]] = []
    current = candidate
    current_suffix = suffix
    while True:
        try:
            metadata = os.lstat(current)
        except OSError as exc:
            raise _identity_error(current, operation, f"could not inspect root identity chain: {exc}") from exc
        if _is_reparse_stat(metadata):
            raise _identity_error(
                current,
                operation,
                f"root identity chain contains a symbolic link, junction, or reparse point: {current}",
            )
        chain.append((current, current_suffix))
        parent = current.parent
        if parent == current:
            break
        if not current.name:
            raise _identity_error(current, operation, "could not preserve the root identity chain suffix")
        current_suffix = (current.name, *current_suffix)
        current = parent
    return chain


def _normalized_suffix(parts: tuple[str, ...]) -> tuple[str, ...]:
    if os.name == "nt":
        return tuple(os.path.normcase(part) for part in parts)
    return parts


def _relationship_from_suffixes(
    first: tuple[str, ...],
    second: tuple[str, ...],
) -> RootRelationship:
    normalized_first = _normalized_suffix(first)
    normalized_second = _normalized_suffix(second)
    if normalized_first == normalized_second:
        return RootRelationship("same")
    if len(first) < len(second) and normalized_second[: len(first)] == normalized_first:
        return RootRelationship("first-ancestor-of-second", "/".join(second[len(first) :]))
    if len(second) < len(first) and normalized_first[: len(second)] == normalized_second:
        return RootRelationship("second-ancestor-of-first", "/".join(first[len(second) :]))
    return RootRelationship("distinct")


def _lexical_root_relationship(first: Path, second: Path) -> RootRelationship:
    first_text = os.path.normcase(os.path.normpath(os.fspath(first)))
    second_text = os.path.normcase(os.path.normpath(os.fspath(second)))
    if first_text == second_text:
        return RootRelationship("same")
    try:
        second_relative = os.path.relpath(second_text, first_text)
        first_relative = os.path.relpath(first_text, second_text)
    except ValueError:
        return RootRelationship("distinct")
    second_parts = Path(second_relative).parts
    if second_parts and second_parts[0] != os.pardir and not os.path.isabs(second_relative):
        return RootRelationship("first-ancestor-of-second", Path(second_relative).as_posix())
    first_parts = Path(first_relative).parts
    if first_parts and first_parts[0] != os.pardir and not os.path.isabs(first_relative):
        return RootRelationship("second-ancestor-of-first", Path(first_relative).as_posix())
    return RootRelationship("distinct")


def root_relationship(
    first_root: Path,
    second_root: Path,
    *,
    operation: str,
) -> RootRelationship:
    """Compare roots by filesystem identity, including unresolved descendant suffixes."""

    first = absolute_path(first_root)
    second = absolute_path(second_root)
    first_chain = _identity_chain(first, operation=operation)
    second_chain = _identity_chain(second, operation=operation)
    samefile = getattr(os.path, "samefile", None)
    if samefile is None:
        lexical = _lexical_root_relationship(first, second)
        if lexical.kind != "distinct":
            return lexical
        return RootRelationship("ambiguous", reason="filesystem identity comparison is unavailable")
    for first_path, first_suffix in first_chain:
        for second_path, second_suffix in second_chain:
            try:
                same_identity = samefile(first_path, second_path)
            except NotImplementedError:
                lexical = _lexical_root_relationship(first, second)
                if lexical.kind != "distinct":
                    return lexical
                return RootRelationship("ambiguous", reason="filesystem identity comparison is unavailable")
            except OSError as exc:
                raise _identity_error(
                    first,
                    operation,
                    f"could not compare root filesystem identity for {first_path} and {second_path}: {exc}",
                ) from exc
            if same_identity:
                return _relationship_from_suffixes(first_suffix, second_suffix)
    return RootRelationship("distinct")


def add_unsafe_path(rows: list[dict[str, str]], error: UnsafeHarnessPathError) -> None:
    row = error.as_report_row()
    if row not in rows:
        rows.append(row)


def sorted_unsafe_paths(rows: Iterable[dict[str, str]]) -> list[dict[str, str]]:
    return sorted(
        (dict(row) for row in rows),
        key=lambda row: (
            str(row.get("path", "")),
            str(row.get("operation", "")),
            str(row.get("reason", "")),
            str(row.get("root", "")),
        ),
    )


class HarnessPathGuard:
    """Guard one declared root without following symlinks, junctions, or reparses."""

    def __init__(self, root: Path, *, label: str) -> None:
        self._unsupported_root_namespace = _has_unsupported_windows_root_namespace(root)
        self.root = absolute_path(root)
        self._unsupported_root_namespace = self._unsupported_root_namespace or _has_unsupported_windows_root_namespace(
            self.root
        )
        self.label = label

    def _unsafe(self, path: str, operation: str, reason: str) -> UnsafeHarnessPathError:
        return UnsafeHarnessPathError(path=path, root=str(self.root), operation=operation, reason=reason)

    def _check_root_namespace(self, *, operation: str, reported_path: str) -> None:
        if self._unsupported_root_namespace:
            raise self._unsafe(
                reported_path,
                operation,
                "Windows device and extended-length namespace roots are not supported",
            )

    def _inspect_existing_components(self, candidate: Path, *, reported_path: str, operation: str) -> None:
        anchor = Path(candidate.anchor)
        current = anchor
        parts = candidate.parts[1:] if candidate.anchor else candidate.parts
        for part in parts:
            current = current / part
            try:
                metadata = os.lstat(current)
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise self._unsafe(reported_path, operation, f"could not inspect path component {current}: {exc}") from exc
            if _is_reparse_stat(metadata):
                raise self._unsafe(
                    reported_path,
                    operation,
                    f"path component is a symbolic link, junction, or reparse point: {current}",
                )
            if current != candidate and not stat.S_ISDIR(metadata.st_mode):
                raise self._unsafe(
                    reported_path,
                    operation,
                    f"path ancestor is not a directory: {current}",
                )

    def check_root(self, *, operation: str, reported_path: str = ".") -> Path:
        self._check_root_namespace(operation=operation, reported_path=reported_path)
        self._inspect_existing_components(self.root, reported_path=reported_path, operation=operation)
        return self.root

    def check(self, relative: object, *, operation: str) -> Path:
        raw = str(relative) if isinstance(relative, str) else repr(relative)
        self._check_root_namespace(operation=operation, reported_path=raw)
        try:
            normalized = normalize_relative_path(relative)
        except ValueError as exc:
            raise self._unsafe(raw, operation, str(exc)) from exc
        candidate = self.root.joinpath(*normalized.split("/"))
        try:
            if os.path.commonpath((os.fspath(self.root), os.fspath(candidate))) != os.fspath(self.root):
                raise self._unsafe(normalized, operation, "path is outside the declared root")
        except ValueError as exc:
            raise self._unsafe(normalized, operation, "path is on a different filesystem root") from exc
        self._inspect_existing_components(candidate, reported_path=normalized, operation=operation)
        resolved_root = self.root.resolve(strict=False)
        resolved_candidate = candidate.resolve(strict=False)
        try:
            resolved_candidate.relative_to(resolved_root)
        except ValueError as exc:
            raise self._unsafe(normalized, operation, "resolved path is outside the declared root") from exc
        return candidate

    def check_file_destination(self, relative: object, *, operation: str) -> Path:
        candidate = self.check(relative, operation=operation)
        try:
            metadata = os.lstat(candidate)
        except FileNotFoundError:
            return candidate
        except OSError as exc:
            raise self._unsafe(str(relative), operation, f"could not inspect destination: {exc}") from exc
        if not stat.S_ISREG(metadata.st_mode):
            raise self._unsafe(str(relative), operation, "file destination exists and is not a regular file")
        return candidate

    def _metadata(self, relative: object, *, operation: str) -> os.stat_result | None:
        candidate = self.check(relative, operation=operation)
        try:
            return os.lstat(candidate)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise self._unsafe(str(relative), operation, f"could not inspect path: {exc}") from exc

    def root_exists(self, *, operation: str) -> bool:
        root = self.check_root(operation=operation)
        try:
            os.lstat(root)
        except FileNotFoundError:
            return False
        return True

    def root_is_dir(self, *, operation: str) -> bool:
        root = self.check_root(operation=operation)
        try:
            metadata = os.lstat(root)
        except FileNotFoundError:
            return False
        return stat.S_ISDIR(metadata.st_mode)

    def exists(self, relative: object, *, operation: str) -> bool:
        return self._metadata(relative, operation=operation) is not None

    def is_file(self, relative: object, *, operation: str) -> bool:
        metadata = self._metadata(relative, operation=operation)
        return metadata is not None and stat.S_ISREG(metadata.st_mode)

    def is_dir(self, relative: object, *, operation: str) -> bool:
        metadata = self._metadata(relative, operation=operation)
        return metadata is not None and stat.S_ISDIR(metadata.st_mode)

    def stat_size(self, relative: object, *, operation: str) -> int:
        metadata = self._metadata(relative, operation=operation)
        if metadata is None or not stat.S_ISREG(metadata.st_mode):
            raise self._unsafe(str(relative), operation, "path is not a regular file")
        return metadata.st_size

    def read_text(self, relative: object, *, operation: str, encoding: str = "utf-8") -> str:
        candidate = self.check(relative, operation=operation)
        metadata = self._metadata(relative, operation=operation)
        if metadata is None or not stat.S_ISREG(metadata.st_mode):
            raise self._unsafe(str(relative), operation, "path is not a regular file")
        try:
            return candidate.read_text(encoding=encoding)
        except OSError as exc:
            raise self._unsafe(str(relative), operation, f"could not read file: {exc}") from exc

    def sha256(self, relative: object, *, operation: str) -> str:
        candidate = self.check(relative, operation=operation)
        metadata = self._metadata(relative, operation=operation)
        if metadata is None or not stat.S_ISREG(metadata.st_mode):
            raise self._unsafe(str(relative), operation, "path is not a regular file")
        digest = hashlib.sha256()
        try:
            with candidate.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
        except OSError as exc:
            raise self._unsafe(str(relative), operation, f"could not hash file: {exc}") from exc
        return digest.hexdigest()

    def ensure_root(self, *, operation: str) -> None:
        root = self.check_root(operation=operation)
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise self._unsafe(".", operation, f"could not create root directory: {exc}") from exc
        self.check_root(operation=operation)

    def ensure_parent(self, relative: object, *, operation: str) -> Path:
        candidate = self.check_file_destination(relative, operation=operation)
        try:
            candidate.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise self._unsafe(str(relative), operation, f"could not create destination parent: {exc}") from exc
        return self.check_file_destination(relative, operation=operation)

    def write_text(
        self,
        relative: object,
        text: str,
        *,
        operation: str,
        encoding: str = "utf-8",
        newline: str | None = "\n",
    ) -> Path:
        candidate = self.ensure_parent(relative, operation=operation)
        candidate = self.check_file_destination(relative, operation=operation)
        try:
            candidate.write_text(text, encoding=encoding, newline=newline)
        except OSError as exc:
            raise self._unsafe(str(relative), operation, f"could not write file: {exc}") from exc
        return candidate

    def copy_from(
        self,
        source: "HarnessPathGuard",
        relative: object,
        *,
        operation: str,
    ) -> Path:
        normalized = normalize_relative_path(relative)
        source_path = source.check(normalized, operation=f"{operation}:source-read")
        if not source.is_file(normalized, operation=f"{operation}:source-read"):
            raise source._unsafe(normalized, operation, "source is not a regular file")
        target_path = self.ensure_parent(normalized, operation=f"{operation}:target-write")
        source_path = source.check(normalized, operation=f"{operation}:source-read")
        target_path = self.check_file_destination(normalized, operation=f"{operation}:target-write")
        try:
            shutil.copy2(source_path, target_path, follow_symlinks=False)
        except OSError as exc:
            raise self._unsafe(normalized, operation, f"could not copy file: {exc}") from exc
        return target_path

    def walk_files(
        self,
        include_root: object,
        *,
        operation: str,
        excluded: Callable[[str], bool] | None = None,
    ) -> tuple[list[Path], list[str], list[UnsafeHarnessPathError]]:
        """Enumerate regular files in stable order without following indirections."""

        files: list[Path] = []
        excluded_paths: list[str] = []
        errors: list[UnsafeHarnessPathError] = []
        try:
            normalized_root = normalize_relative_path(include_root)
            start = self.check(normalized_root, operation=operation)
            metadata = self._metadata(normalized_root, operation=operation)
        except (ValueError, UnsafeHarnessPathError) as exc:
            if isinstance(exc, UnsafeHarnessPathError):
                errors.append(exc)
            else:
                errors.append(self._unsafe(str(include_root), operation, str(exc)))
            return files, excluded_paths, errors
        if metadata is None:
            return files, excluded_paths, errors
        if stat.S_ISREG(metadata.st_mode):
            if excluded and excluded(normalized_root):
                excluded_paths.append(normalized_root)
            else:
                files.append(start)
            return files, excluded_paths, errors
        if not stat.S_ISDIR(metadata.st_mode):
            errors.append(self._unsafe(normalized_root, operation, "include root is not a regular file or directory"))
            return files, excluded_paths, errors

        def visit(directory: Path, relative_directory: str) -> None:
            try:
                entries = sorted(os.scandir(directory), key=lambda entry: entry.name)
            except OSError as exc:
                errors.append(self._unsafe(relative_directory, operation, f"could not enumerate directory: {exc}"))
                return
            for entry in entries:
                relative = f"{relative_directory}/{entry.name}" if relative_directory else entry.name
                try:
                    normalized = normalize_relative_path(relative)
                    metadata = entry.stat(follow_symlinks=False)
                except (OSError, ValueError) as exc:
                    errors.append(self._unsafe(relative, operation, f"could not inspect directory entry: {exc}"))
                    continue
                if _is_reparse_stat(metadata):
                    errors.append(
                        self._unsafe(
                            normalized,
                            operation,
                            f"path component is a symbolic link, junction, or reparse point: {entry.path}",
                        )
                    )
                    continue
                if stat.S_ISDIR(metadata.st_mode):
                    visit(Path(entry.path), normalized)
                elif stat.S_ISREG(metadata.st_mode):
                    if excluded and excluded(normalized):
                        excluded_paths.append(normalized)
                    else:
                        files.append(Path(entry.path))
                else:
                    errors.append(self._unsafe(normalized, operation, "directory entry is not a regular file or directory"))

        visit(start, normalized_root)
        return files, excluded_paths, errors

    def existing_paths(self, *, operation: str) -> tuple[list[str], list[UnsafeHarnessPathError]]:
        """Return stable file paths, plus empty directories, for reuse detection."""

        errors: list[UnsafeHarnessPathError] = []
        paths: list[str] = []
        try:
            root = self.check_root(operation=operation)
            metadata = os.lstat(root)
        except FileNotFoundError:
            return paths, errors
        except UnsafeHarnessPathError as exc:
            return paths, [exc]
        if not stat.S_ISDIR(metadata.st_mode):
            return paths, errors

        def visit(directory: Path, prefix: str) -> bool:
            try:
                entries = sorted(os.scandir(directory), key=lambda entry: entry.name)
            except OSError as exc:
                errors.append(self._unsafe(prefix or ".", operation, f"could not enumerate directory: {exc}"))
                return False
            has_content = False
            for entry in entries:
                relative = f"{prefix}/{entry.name}" if prefix else entry.name
                has_content = True
                try:
                    normalized = normalize_relative_path(relative)
                    entry_metadata = entry.stat(follow_symlinks=False)
                except (OSError, ValueError) as exc:
                    errors.append(self._unsafe(relative, operation, f"could not inspect directory entry: {exc}"))
                    continue
                if _is_reparse_stat(entry_metadata):
                    errors.append(
                        self._unsafe(
                            normalized,
                            operation,
                            f"path component is a symbolic link, junction, or reparse point: {entry.path}",
                        )
                    )
                elif stat.S_ISDIR(entry_metadata.st_mode):
                    child_has_content = visit(Path(entry.path), normalized)
                    if not child_has_content:
                        paths.append(normalized + "/")
                elif stat.S_ISREG(entry_metadata.st_mode):
                    paths.append(normalized)
                else:
                    errors.append(self._unsafe(normalized, operation, "directory entry is not a regular file or directory"))
            return has_content

        visit(root, "")
        return sorted(set(paths)), errors

    def audit_existing_tree(self, *, operation: str) -> list[UnsafeHarnessPathError]:
        """Reject every indirection or special entry in an existing write-effect root."""

        try:
            root = self.check_root(operation=operation)
            metadata = os.lstat(root)
        except FileNotFoundError:
            return []
        except UnsafeHarnessPathError as exc:
            return [exc]
        except OSError as exc:
            return [self._unsafe(".", operation, f"could not inspect root: {exc}")]
        if not stat.S_ISDIR(metadata.st_mode):
            return [self._unsafe(".", operation, "initialization target root is not a directory")]
        _paths, errors = self.existing_paths(operation=operation)
        return errors
