from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any, BinaryIO, Iterator, TypeVar

from pydantic import BaseModel, ValidationError

from .errors import ContractError


T = TypeVar("T", bound=BaseModel)
PROTECTED_PREDICTION_KEYS = frozenset({"accuracyclaim", "evidencegrade", "metrics", "groundtruth"})
MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_JSONL_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True)
class FileSnapshot:
    """One immutable read used for length, digest, decoding, and parsing."""

    path: Path
    data: bytes
    text: str
    sha256: str
    size_bytes: int


class _DuplicateKey(ValueError):
    pass


class _NonFiniteConstant(ValueError):
    pass


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKey(key)
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise _NonFiniteConstant(value)


def _invalid_scalar(value: Any, path: str = "$") -> tuple[str, str] | None:
    if isinstance(value, float) and not math.isfinite(value):
        return "nonfinite", path
    if isinstance(value, str):
        try:
            value.encode("utf-8", errors="strict")
        except UnicodeEncodeError:
            return "unicode", path
        return None
    if isinstance(value, dict):
        for raw_key, nested in value.items():
            try:
                raw_key.encode("utf-8", errors="strict")
            except UnicodeEncodeError:
                return "unicode", f"{path}.<invalid-key>"
            found = _invalid_scalar(nested, f"{path}.{raw_key}")
            if found:
                return found
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            found = _invalid_scalar(nested, f"{path}[{index}]")
            if found:
                return found
    return None


def protected_prediction_path(value: Any, path: str = "$") -> str | None:
    if isinstance(value, dict):
        for raw_key, nested in value.items():
            normalized = re.sub(r"[^a-z0-9]", "", raw_key.casefold())
            next_path = f"{path}.{raw_key}"
            if normalized in PROTECTED_PREDICTION_KEYS:
                return next_path
            found = protected_prediction_path(nested, next_path)
            if found:
                return found
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            found = protected_prediction_path(nested, f"{path}[{index}]")
            if found:
                return found
    return None


def parse_json_object(text: str, *, location: str) -> dict[str, Any]:
    try:
        value = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except _DuplicateKey as exc:
        raise ContractError(
            "EVAL_JSON_DUPLICATE_KEY",
            f"Duplicate JSON object key: {exc}",
            path=location,
        ) from exc
    except _NonFiniteConstant as exc:
        raise ContractError(
            "EVAL_JSON_NONFINITE",
            f"Non-finite JSON number is forbidden: {exc}",
            path=location,
        ) from exc
    except RecursionError as exc:
        raise ContractError(
            "EVAL_JSON_NESTING_TOO_DEEP",
            "JSON nesting exceeds the supported depth",
            path=location,
        ) from exc
    except (json.JSONDecodeError, ValueError) as exc:
        raise ContractError("EVAL_JSON_INVALID", f"Invalid JSON: {exc}", path=location) from exc
    if not isinstance(value, dict):
        raise ContractError("EVAL_JSON_INVALID", "Each JSON document must be an object", path=location)
    try:
        invalid = _invalid_scalar(value)
    except RecursionError as exc:
        raise ContractError(
            "EVAL_JSON_NESTING_TOO_DEEP",
            "JSON nesting exceeds the supported depth",
            path=location,
        ) from exc
    if invalid:
        invalid_kind, invalid_path = invalid
        raise ContractError(
            "EVAL_JSON_NONFINITE" if invalid_kind == "nonfinite" else "EVAL_JSON_INVALID_UNICODE",
            f"Non-finite number or invalid Unicode scalar at {invalid_path}",
            path=location,
        )
    return value


def snapshot_file(path: Path, *, max_bytes: int) -> FileSnapshot:
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    descriptor: int | None = None
    try:
        path_stat = path.lstat()
        if not stat.S_ISREG(path_stat.st_mode):
            raise ContractError(
                "EVAL_FILE_NOT_REGULAR",
                "Required JSON input must be a regular file",
                path=str(path),
            )
        flags = (
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NONBLOCK", 0)
        )
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        opened_stat = os.fstat(descriptor)
        if not stat.S_ISREG(opened_stat.st_mode):
            raise ContractError(
                "EVAL_FILE_NOT_REGULAR",
                "Required JSON input must be a regular file",
                path=str(path),
            )
        if (opened_stat.st_dev, opened_stat.st_ino) != (path_stat.st_dev, path_stat.st_ino):
            raise ContractError(
                "EVAL_FILE_IDENTITY_CHANGED",
                "Required JSON input changed identity while it was being opened",
                path=str(path),
            )
        if opened_stat.st_size > max_bytes:
            raise ContractError(
                "EVAL_FILE_TOO_LARGE",
                f"JSON input exceeds the {max_bytes}-byte limit",
                path=str(path),
                details={"max_bytes": max_bytes},
            )
        with os.fdopen(descriptor, "rb", closefd=True) as handle:
            descriptor = None
            payload = handle.read(max_bytes + 1)
    except ContractError:
        raise
    except (OSError, ValueError) as exc:
        raise ContractError("EVAL_FILE_READ_FAILED", f"Cannot read required file: {exc}", path=str(path)) from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if len(payload) > max_bytes:
        raise ContractError(
            "EVAL_FILE_TOO_LARGE",
            f"JSON input exceeds the {max_bytes}-byte limit",
            path=str(path),
            details={"max_bytes": max_bytes},
        )
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ContractError("EVAL_JSON_INVALID_UTF8", f"File is not strict UTF-8: {exc}", path=str(path)) from exc
    return FileSnapshot(
        path=path,
        data=payload,
        text=text,
        sha256=hashlib.sha256(payload).hexdigest(),
        size_bytes=len(payload),
    )


@contextmanager
def open_relative_regular_file(
    root: Path,
    relative_path: str,
) -> Iterator[tuple[BinaryIO, os.stat_result, Path]]:
    """Open a root-confined regular file without following any path symlink.

    Each descendant is opened relative to an already-open directory descriptor,
    so replacing an intermediate directory after validation cannot redirect the
    read outside ``root``.
    """

    pure = PurePosixPath(relative_path)
    if (
        not relative_path
        or "\x00" in relative_path
        or pure.is_absolute()
        or "\\" in relative_path
        or pure.as_posix() != relative_path
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        raise ContractError(
            "EVAL_FILE_PATH_UNSAFE",
            "Relative input path must use normalized, root-confined POSIX syntax",
            path=relative_path,
        )
    if (
        os.open not in os.supports_dir_fd
        or not hasattr(os, "O_DIRECTORY")
        or not hasattr(os, "O_NOFOLLOW")
    ):
        raise ContractError(
            "EVAL_SECURE_OPEN_UNAVAILABLE",
            "This runtime cannot securely open root-confined evaluation artifacts",
            path=relative_path,
        )

    root_resolved = root.resolve()
    display_path = root_resolved.joinpath(*pure.parts)
    directory_descriptor: int | None = None
    file_descriptor: int | None = None
    try:
        root_stat = root_resolved.lstat()
        directory_flags = (
            os.O_RDONLY
            | os.O_DIRECTORY
            | os.O_NOFOLLOW
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NONBLOCK", 0)
        )
        directory_descriptor = os.open(root_resolved, directory_flags)
        opened_root_stat = os.fstat(directory_descriptor)
        if not stat.S_ISDIR(opened_root_stat.st_mode):
            raise ContractError(
                "EVAL_FILE_PATH_UNSAFE",
                "Evaluation dataset root must be a directory",
                path=str(root_resolved),
            )
        if (opened_root_stat.st_dev, opened_root_stat.st_ino) != (root_stat.st_dev, root_stat.st_ino):
            raise ContractError(
                "EVAL_FILE_IDENTITY_CHANGED",
                "Evaluation dataset root changed identity while it was being opened",
                path=str(root_resolved),
            )

        for component in pure.parts[:-1]:
            next_descriptor = os.open(component, directory_flags, dir_fd=directory_descriptor)
            try:
                next_stat = os.fstat(next_descriptor)
                if not stat.S_ISDIR(next_stat.st_mode):
                    raise ContractError(
                        "EVAL_FILE_PATH_UNSAFE",
                        "Intermediate evaluation path component is not a directory",
                        path=relative_path,
                    )
            except BaseException:
                try:
                    os.close(next_descriptor)
                except OSError:
                    pass
                raise
            previous_descriptor = directory_descriptor
            directory_descriptor = next_descriptor
            os.close(previous_descriptor)

        file_flags = (
            os.O_RDONLY
            | os.O_NOFOLLOW
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NONBLOCK", 0)
        )
        file_descriptor = os.open(pure.parts[-1], file_flags, dir_fd=directory_descriptor)
        opened_stat = os.fstat(file_descriptor)
        if not stat.S_ISREG(opened_stat.st_mode):
            raise ContractError(
                "EVAL_FILE_NOT_REGULAR",
                "Required evaluation input must be a regular file",
                path=relative_path,
            )
        with os.fdopen(file_descriptor, "rb", closefd=True) as handle:
            file_descriptor = None
            yield handle, opened_stat, display_path
    except ContractError:
        raise
    except (OSError, ValueError) as exc:
        raise ContractError(
            "EVAL_FILE_READ_FAILED",
            f"Cannot securely read required evaluation input: {exc}",
            path=relative_path,
        ) from exc
    finally:
        if file_descriptor is not None:
            os.close(file_descriptor)
        if directory_descriptor is not None:
            os.close(directory_descriptor)


def snapshot_relative_file(root: Path, relative_path: str, *, max_bytes: int) -> FileSnapshot:
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    with open_relative_regular_file(root, relative_path) as (handle, opened_stat, display_path):
        if opened_stat.st_size > max_bytes:
            raise ContractError(
                "EVAL_FILE_TOO_LARGE",
                f"JSON input exceeds the {max_bytes}-byte limit",
                path=relative_path,
                details={"max_bytes": max_bytes},
            )
        payload = handle.read(max_bytes + 1)
    if len(payload) > max_bytes:
        raise ContractError(
            "EVAL_FILE_TOO_LARGE",
            f"JSON input exceeds the {max_bytes}-byte limit",
            path=relative_path,
            details={"max_bytes": max_bytes},
        )
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ContractError(
            "EVAL_JSON_INVALID_UTF8",
            f"File is not strict UTF-8: {exc}",
            path=relative_path,
        ) from exc
    return FileSnapshot(
        path=display_path,
        data=payload,
        text=text,
        sha256=hashlib.sha256(payload).hexdigest(),
        size_bytes=len(payload),
    )


def read_utf8(path: Path) -> str:
    return snapshot_file(path, max_bytes=MAX_JSONL_BYTES).text


def _validation_summary(exc: ValidationError) -> str:
    messages: list[str] = []
    for item in exc.errors(include_url=False, include_input=False):
        location = ".".join(str(part) for part in item["loc"]) or "$"
        messages.append(f"{location}: {item['msg']}")
    return "; ".join(messages)[:2000]


def parse_json_model_snapshot(snapshot: FileSnapshot, model: type[T]) -> T:
    raw = parse_json_object(snapshot.text, location=str(snapshot.path))
    try:
        return model.model_validate(raw)
    except RecursionError as exc:
        raise ContractError(
            "EVAL_JSON_NESTING_TOO_DEEP",
            "JSON model nesting exceeds the supported depth",
            path=str(snapshot.path),
        ) from exc
    except ValidationError as exc:
        raise ContractError(
            "EVAL_SCHEMA_INVALID",
            _validation_summary(exc),
            path=str(snapshot.path),
        ) from exc


def load_json_model(path: Path, model: type[T]) -> T:
    return parse_json_model_snapshot(snapshot_file(path, max_bytes=MAX_JSON_BYTES), model)


def parse_jsonl_models_snapshot(
    snapshot: FileSnapshot,
    model: type[T],
    *,
    record_kind: str,
    unique_key: Callable[[T], str],
    protect_predictions: bool = False,
) -> list[T]:
    lines = snapshot.text.splitlines()
    records: list[T] = []
    seen: set[str] = set()
    duplicate_code = {
        "case": "EVAL_CASE_ID_DUPLICATE",
        "label": "EVAL_LABEL_ID_DUPLICATE",
        "prediction": "EVAL_PREDICTION_DUPLICATE",
    }.get(record_kind, "EVAL_ID_DUPLICATE")
    for line_number, line in enumerate(lines, start=1):
        location = f"{snapshot.path}:{line_number}"
        if not line.strip():
            raise ContractError("EVAL_JSONL_BLANK_LINE", "Blank JSONL lines are forbidden", path=location)
        raw = parse_json_object(line, location=location)
        if protect_predictions:
            try:
                protected_path = protected_prediction_path(raw)
            except RecursionError as exc:
                raise ContractError(
                    "EVAL_JSON_NESTING_TOO_DEEP",
                    "Prediction nesting exceeds the supported depth",
                    path=location,
                ) from exc
            if protected_path:
                raise ContractError(
                    "EVAL_PROTECTED_CLAIM_FORBIDDEN",
                    f"Protected field is forbidden in predictions at {protected_path}",
                    path=location,
                )
        try:
            record = model.model_validate(raw)
        except RecursionError as exc:
            raise ContractError(
                "EVAL_JSON_NESTING_TOO_DEEP",
                "JSONL model nesting exceeds the supported depth",
                path=location,
            ) from exc
        except ValidationError as exc:
            raise ContractError("EVAL_SCHEMA_INVALID", _validation_summary(exc), path=location) from exc
        key = unique_key(record)
        if key in seen:
            raise ContractError(duplicate_code, f"Duplicate {record_kind} identifier: {key}", path=location)
        seen.add(key)
        records.append(record)
    if not records:
        raise ContractError(
            "EVAL_EMPTY_EVALUATION_SET",
            f"{record_kind} JSONL is empty",
            path=str(snapshot.path),
        )
    return records


def load_jsonl_models(
    path: Path,
    model: type[T],
    *,
    record_kind: str,
    unique_key: Callable[[T], str],
    protect_predictions: bool = False,
) -> list[T]:
    return parse_jsonl_models_snapshot(
        snapshot_file(path, max_bytes=MAX_JSONL_BYTES),
        model,
        record_kind=record_kind,
        unique_key=unique_key,
        protect_predictions=protect_predictions,
    )


__all__ = [
    "FileSnapshot",
    "MAX_JSON_BYTES",
    "MAX_JSONL_BYTES",
    "load_json_model",
    "load_jsonl_models",
    "parse_json_object",
    "parse_json_model_snapshot",
    "parse_jsonl_models_snapshot",
    "open_relative_regular_file",
    "protected_prediction_path",
    "read_utf8",
    "snapshot_file",
    "snapshot_relative_file",
]
