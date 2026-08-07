"""Bounded, manifest-driven parsing for QQ backup ZIP packages."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath
import stat
import tempfile
from typing import Any, TYPE_CHECKING
import zipfile

from src.domain.messages import NormalizedMessage

if TYPE_CHECKING:
    from src.preprocessing.parser_registry import ParserProbe, ParserSource, ParserValidation


MAX_ARCHIVE_ENTRIES = 1024
MAX_ENTRY_BYTES = 64 * 1024 * 1024
MAX_EXPANDED_BYTES = 256 * 1024 * 1024
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_COMPRESSION_RATIO = 100
MAX_MANIFEST_FILES = 256
_SUPPORTED_FILE_TYPES = frozenset({"generic_json", "generic_jsonl", "qq_text", "qq_html"})


class QqBackupError(ValueError):
    """Actionable archive failure without exposing archive payload content."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class _ArchiveFile:
    name: str
    source_type: str


@dataclass(frozen=True, slots=True)
class _ArchivePlan:
    files: tuple[_ArchiveFile, ...]
    records: tuple[dict[str, Any], ...]


class QqBackupParser:
    source_type = "qq_backup"

    def probe(self, source: "ParserSource") -> "ParserProbe":
        from src.preprocessing.parser_registry import ParserProbe

        explicit = source.metadata.get("source_type") == self.source_type
        if not source.path.is_file():
            return ParserProbe(self.source_type, 0.0, False, "QQ 备份包必须是 ZIP 文件")
        if not _is_zip(source.path):
            if source.path.suffix.casefold() == ".zip":
                return ParserProbe(self.source_type, 0.98, True, "ZIP 备份包无法读取")
            return ParserProbe(self.source_type, 0.0, False, "文件不是 ZIP 备份包")
        try:
            _inspect_archive(source.path)
        except QqBackupError as exc:
            if exc.code == "unsupported_manifest" and "平台标识" in str(exc) and not explicit:
                return ParserProbe(self.source_type, 0.0, False, str(exc))
            if explicit or source.path.suffix.casefold() == ".zip":
                return ParserProbe(self.source_type, 0.98, True, str(exc))
            return ParserProbe(self.source_type, 0.0, False, str(exc))
        return ParserProbe(
            self.source_type,
            0.99 if explicit else 0.97,
            True,
            "QQ manifest ZIP 备份包已识别",
        )

    def validate(self, source: "ParserSource") -> "ParserValidation":
        from src.preprocessing.parser_registry import ParserValidation

        if not source.path.is_file():
            return ParserValidation(False, "source_not_file", "QQ 备份包必须是 ZIP 文件")
        try:
            _inspect_archive(source.path)
        except QqBackupError as exc:
            return ParserValidation(False, exc.code, str(exc))
        return ParserValidation(True)

    def stream_records(self, source: "ParserSource") -> Iterator[NormalizedMessage]:
        plan = _inspect_archive(source.path)
        for record in plan.records:
            yield NormalizedMessage.from_mapping(record)
        if not plan.files:
            return

        with tempfile.TemporaryDirectory(prefix="past-partner-qq-backup-") as temporary:
            extraction_root = Path(temporary)
            try:
                _extract_files(source.path, plan.files, extraction_root)
                from src.preprocessing.parser_registry import ParserRegistry

                registry = ParserRegistry.with_builtins()
                for archive_file in plan.files:
                    extracted = extraction_root / PurePosixPath(archive_file.name)
                    result = registry.parse(extracted, {"source_type": archive_file.source_type})
                    yield from result.records
            except QqBackupError:
                raise
            except (OSError, KeyError, RuntimeError, zipfile.BadZipFile) as exc:
                raise QqBackupError("corrupt_archive", "QQ 备份包无法安全读取，可能已损坏") from exc

    def summarize(
        self,
        records: Sequence[NormalizedMessage],
        warnings: Sequence[str],
        confidence: float,
    ) -> dict[str, Any]:
        return {
            "record_count": len(records),
            "warning_count": len(warnings),
            "unsupported_count": 0,
            "confidence": confidence,
            "snapshot": "bounded_archive",
            "schema": "manifest_v1",
        }


def _inspect_archive(path: Path) -> _ArchivePlan:
    try:
        with zipfile.ZipFile(path, "r") as archive:
            infos = archive.infolist()
            if not infos:
                raise QqBackupError("unsupported_manifest", "QQ 备份包不能为空")
            if len(infos) > MAX_ARCHIVE_ENTRIES:
                raise QqBackupError("archive_limits_exceeded", "QQ 备份包条目数量超过安全上限")

            by_name: dict[str, zipfile.ZipInfo] = {}
            expanded_bytes = 0
            for info in infos:
                _validate_member_name(info.filename)
                normalized_name = info.filename.casefold()
                if normalized_name in by_name:
                    raise QqBackupError("duplicate_entry", "QQ 备份包包含重复条目")
                by_name[normalized_name] = info
                if info.is_dir():
                    continue
                mode = (info.external_attr >> 16) & 0xFFFF
                if stat.S_ISLNK(mode):
                    raise QqBackupError("unsupported_entry", "QQ 备份包不支持符号链接条目")
                if normalized_name.endswith(".zip"):
                    raise QqBackupError("nested_archive", "QQ 备份包不支持嵌套 ZIP")
                if info.file_size > MAX_ENTRY_BYTES:
                    raise QqBackupError("archive_limits_exceeded", "QQ 备份包单个条目超过安全大小上限")
                expanded_bytes += info.file_size
                if expanded_bytes > MAX_EXPANDED_BYTES:
                    raise QqBackupError("archive_limits_exceeded", "QQ 备份包展开后超过安全大小上限")
                if info.file_size and (
                    info.compress_size == 0
                    or info.file_size / max(info.compress_size, 1) > MAX_COMPRESSION_RATIO
                ):
                    raise QqBackupError("compression_ratio_exceeded", "QQ 备份包压缩比超过安全上限")

            manifest_info = _manifest_info(by_name)
            if manifest_info is None:
                raise QqBackupError("unsupported_manifest", "QQ 备份包缺少根目录 manifest.json")
            if manifest_info.file_size > MAX_MANIFEST_BYTES:
                raise QqBackupError("archive_limits_exceeded", "QQ manifest 超过安全大小上限")
            try:
                manifest = json.loads(archive.read(manifest_info).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError, OSError, zipfile.BadZipFile) as exc:
                raise QqBackupError("unsupported_manifest", "QQ manifest 不是有效 UTF-8 JSON") from exc
            if not isinstance(manifest, dict):
                raise QqBackupError("unsupported_manifest", "QQ manifest 必须是 JSON 对象")
            if manifest.get("schema_version") != 1:
                raise QqBackupError("unsupported_manifest", "QQ manifest schema_version 不受支持")
            platform = manifest.get("platform", manifest.get("source_type"))
            if not isinstance(platform, str) or platform.casefold() not in {"qq", "qq_backup"}:
                raise QqBackupError("unsupported_manifest", "QQ manifest 平台标识不受支持")

            raw_records = manifest.get("records")
            raw_files = manifest.get("files")
            if raw_records is not None and raw_files is not None:
                raise QqBackupError("unsupported_manifest", "QQ manifest 不能同时包含 records 和 files")
            if raw_records is not None:
                if not isinstance(raw_records, list) or not all(isinstance(item, dict) for item in raw_records):
                    raise QqBackupError("unsupported_manifest", "QQ manifest records 格式不受支持")
                _reject_unlisted_files(by_name, manifest_info.filename)
                return _ArchivePlan((), tuple(dict(item) for item in raw_records))
            if not isinstance(raw_files, list) or not raw_files:
                raise QqBackupError("unsupported_manifest", "QQ manifest 必须声明 files 或 records")
            if len(raw_files) > MAX_MANIFEST_FILES:
                raise QqBackupError("archive_limits_exceeded", "QQ manifest 文件数量超过安全上限")
            files = tuple(_normalize_file(item, by_name, manifest_info.filename) for item in raw_files)
            _reject_unlisted_files(by_name, manifest_info.filename, {item.name.casefold() for item in files})
            return _ArchivePlan(files, ())
    except QqBackupError:
        raise
    except (OSError, zipfile.BadZipFile, ValueError) as exc:
        raise QqBackupError("corrupt_archive", "QQ 备份包无法读取，可能已损坏") from exc


def _extract_files(path: Path, files: Sequence[_ArchiveFile], destination_root: Path) -> None:
    root = destination_root.resolve()
    expanded_bytes = 0
    try:
        with zipfile.ZipFile(path, "r") as archive:
            for archive_file in files:
                target = destination_root / PurePosixPath(archive_file.name)
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    target.resolve().relative_to(root)
                except ValueError as exc:
                    raise QqBackupError("path_traversal", "QQ 备份包条目超出安全目录") from exc
                with archive.open(archive_file.name, "r") as source, target.open("wb") as output:
                    written = 0
                    while True:
                        chunk = source.read(64 * 1024)
                        if not chunk:
                            break
                        written += len(chunk)
                        expanded_bytes += len(chunk)
                        if written > MAX_ENTRY_BYTES or expanded_bytes > MAX_EXPANDED_BYTES:
                            raise QqBackupError(
                                "archive_limits_exceeded",
                                "QQ 备份包实际展开数据超过安全大小上限",
                            )
                        output.write(chunk)
    except QqBackupError:
        raise
    except (OSError, KeyError, RuntimeError, zipfile.BadZipFile) as exc:
        raise QqBackupError("corrupt_archive", "QQ 备份包无法安全读取，可能已损坏") from exc


def _normalize_file(value: object, by_name: dict[str, zipfile.ZipInfo], manifest_name: str) -> _ArchiveFile:
    if not isinstance(value, dict):
        raise QqBackupError("unsupported_manifest", "QQ manifest files 项格式不受支持")
    raw_name = value.get("path")
    if not isinstance(raw_name, str):
        raise QqBackupError("unsupported_manifest", "QQ manifest 文件路径缺失")
    _validate_member_name(raw_name)
    if raw_name.casefold() == manifest_name.casefold():
        raise QqBackupError("unsupported_manifest", "QQ manifest 不能把自身作为消息文件")
    info = by_name.get(raw_name.casefold())
    if info is None or info.is_dir():
        raise QqBackupError("unsupported_manifest", "QQ manifest 引用了不存在的文件")
    raw_type = value.get("source_type", value.get("format"))
    source_type = _normalize_source_type(raw_type, raw_name)
    return _ArchiveFile(raw_name, source_type)


def _normalize_source_type(raw_type: object, name: str) -> str:
    if isinstance(raw_type, str) and raw_type:
        source_type = {
            "json": "generic_json",
            "jsonl": "generic_jsonl",
            "txt": "qq_text",
            "text": "qq_text",
            "html": "qq_html",
            "htm": "qq_html",
        }.get(raw_type.casefold(), raw_type.casefold())
    else:
        source_type = {
            ".json": "generic_json",
            ".jsonl": "generic_jsonl",
            ".txt": "qq_text",
            ".html": "qq_html",
            ".htm": "qq_html",
        }.get(Path(name).suffix.casefold(), "")
    if source_type not in _SUPPORTED_FILE_TYPES:
        raise QqBackupError("unsupported_manifest", "QQ manifest 声明了不受支持的消息文件格式")
    return source_type


def _manifest_info(by_name: dict[str, zipfile.ZipInfo]) -> zipfile.ZipInfo | None:
    for name in ("manifest.json", "qq-manifest.json"):
        info = by_name.get(name)
        if info is not None and not info.is_dir():
            return info
    return None


def _reject_unlisted_files(
    by_name: dict[str, zipfile.ZipInfo],
    manifest_name: str,
    listed_names: set[str] | None = None,
) -> None:
    listed_names = listed_names or set()
    for name, info in by_name.items():
        if info.is_dir() or name == manifest_name.casefold():
            continue
        if name not in listed_names:
            raise QqBackupError("unsupported_manifest", "QQ manifest 未覆盖备份包中的全部文件")


def _validate_member_name(name: str) -> None:
    if not name or "\x00" in name or "\\" in name:
        raise QqBackupError("path_traversal", "QQ 备份包包含不安全的文件路径")
    path = PurePosixPath(name)
    if path.is_absolute() or ":" in path.parts[0] or any(part == ".." for part in path.parts):
        raise QqBackupError("path_traversal", "QQ 备份包包含不安全的文件路径")


def _is_zip(path: Path) -> bool:
    try:
        return zipfile.is_zipfile(path)
    except OSError:
        return False


if __name__ == "__main__":
    raise SystemExit("qq_backup is a parser module, not a command-line entry point")
