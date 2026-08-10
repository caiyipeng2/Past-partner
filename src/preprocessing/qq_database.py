"""Safe, read-only parsing for user-selected QQ SQLite database directories."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import shutil
import sqlite3
import tempfile
import uuid
from pathlib import Path
from typing import Any, TYPE_CHECKING

from src.domain.messages import NormalizedMessage

if TYPE_CHECKING:
    from src.preprocessing.parser_registry import ParserProbe, ParserSource, ParserValidation


class QqDatabaseError(ValueError):
    """Actionable failure raised without exposing database message content."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class SnapshotChangedError(QqDatabaseError):
    def __init__(self, message: str) -> None:
        super().__init__("snapshot_changed", message)


@dataclass(frozen=True, slots=True)
class SnapshotFile:
    source: Path
    copied: Path


@dataclass(frozen=True, slots=True)
class QqDatabaseSnapshot:
    root: Path
    files: tuple[SnapshotFile, ...]


@dataclass(frozen=True, slots=True)
class _MessageSchema:
    database: Path
    table: str
    sender: str
    content: str
    timestamp: str
    message_id: str | None
    sender_name: str | None
    message_type: str | None
    chat_id: str | None


_MESSAGE_TABLE_HINTS = frozenset(
    {
        "message",
        "messages",
        "msg",
        "qqmessage",
        "qqmessages",
        "qqmessages",
        "chatmessage",
        "chatmessages",
        "record",
        "records",
    }
)
_DATABASE_SUFFIXES = frozenset({".db", ".sqlite", ".sqlite3"})
_COLUMN_ALIASES = {
    "sender": (
        "sender_id",
        "sender_uin",
        "from_uin",
        "from_qq",
        "uin",
        "sender",
        "author_id",
        "author",
    ),
    "sender_name": (
        "sender_name",
        "sender_nickname",
        "nickname",
        "nick_name",
        "nick",
        "from_name",
        "author_name",
        "name",
    ),
    "content": (
        "content",
        "message_content",
        "msg_content",
        "message",
        "body",
        "text",
        "msg",
    ),
    "timestamp": (
        "timestamp",
        "created_at",
        "create_time",
        "send_time",
        "msg_time",
        "time",
        "sent_at",
    ),
    "message_id": (
        "message_id",
        "msg_id",
        "local_id",
        "server_id",
        "seq",
        "msgid",
        "id",
    ),
    "message_type": (
        "message_type",
        "msg_type",
        "content_type",
        "type",
    ),
    "chat_id": (
        "chat_id",
        "conversation_id",
        "group_id",
        "troop_uin",
        "peer_uin",
        "talker",
    ),
}
_QQ_TYPES = {
    1: "text",
    2: "image",
    3: "audio",
    4: "video",
    5: "file",
    6: "location",
    7: "emoji",
    8: "contact_card",
    10000: "system",
}


def create_qq_snapshot(
    source_root: str | Path,
    cache_root: str | Path,
    *,
    retries: int = 3,
    copy_file: Callable[[Path, Path], None] = shutil.copy2,
) -> QqDatabaseSnapshot:
    """Copy QQ databases and existing WAL/SHM sidecars consistently."""

    if retries < 1:
        raise ValueError("snapshot retries must be positive")
    source = Path(source_root).resolve()
    if not source.is_dir():
        raise QqDatabaseError(
            "source_not_directory",
            "QQ 数据库必须提供包含 SQLite 数据库文件的目录，不能直接上传单个数据库文件",
        )
    cache = Path(cache_root).resolve()
    try:
        cache.relative_to(source)
    except ValueError:
        pass
    else:
        raise QqDatabaseError("invalid_cache", "QQ 数据库快照缓存必须位于源目录之外")
    cache.mkdir(parents=True, exist_ok=True)

    for _attempt in range(retries):
        snapshot_root: Path | None = None
        copied: list[SnapshotFile] = []
        try:
            databases = _database_paths(source)
            if not databases:
                raise QqDatabaseError("empty_source", "QQ 数据库目录中没有 SQLite 数据库文件")
            sources = _with_sidecars(databases)
            _validate_source_files(source, sources)
            snapshot_root = cache / f"qq-{uuid.uuid4().hex}"
            snapshot_root.mkdir()
            before = _capture_metadata(sources)
            for source_file in sources:
                relative = source_file.relative_to(source)
                destination = snapshot_root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                copy_file(source_file, destination)
                copied.append(SnapshotFile(source_file, destination))
            databases_after = _database_paths(source)
            if not databases_after:
                raise QqDatabaseError("empty_source", "QQ 数据库目录中没有 SQLite 数据库文件")
            sources_after = _with_sidecars(databases_after)
            _validate_source_files(source, sources_after)
            after = _capture_metadata(sources_after)
        except FileNotFoundError:
            if snapshot_root is not None:
                shutil.rmtree(snapshot_root, ignore_errors=True)
            continue
        except Exception:
            if snapshot_root is not None:
                shutil.rmtree(snapshot_root, ignore_errors=True)
            raise

        if databases == databases_after and sources == sources_after and before == after:
            assert snapshot_root is not None
            return QqDatabaseSnapshot(snapshot_root, tuple(copied))
        if snapshot_root is not None:
            shutil.rmtree(snapshot_root, ignore_errors=True)

    raise SnapshotChangedError("QQ 数据库在快照期间持续变化，请退出 QQ 后重试")


class QqDatabaseParser:
    source_type = "qq_database"

    def __init__(self, snapshot_cache_root: str | Path | None = None) -> None:
        self.snapshot_cache_root = (
            Path(snapshot_cache_root).resolve() if snapshot_cache_root is not None else None
        )

    def probe(self, source: "ParserSource") -> "ParserProbe":
        from src.preprocessing.parser_registry import ParserProbe

        path = source.path
        if not path.is_dir():
            return ParserProbe(
                self.source_type,
                0.0,
                False,
                "QQ 数据库必须提供目录，单个 .db 文件不会被当作聊天记录解析",
            )
        explicit = source.metadata.get("source_type") == self.source_type
        layout_hint = _is_qq_layout_hint(path)
        if not explicit and not layout_hint:
            return ParserProbe(self.source_type, 0.0, False, "目录没有明确的 QQ 数据库来源标识")
        databases = _database_paths(path)
        if not databases:
            return ParserProbe(self.source_type, 0.0, False, "目录中没有 SQLite 数据库文件")
        confidence = 0.99 if explicit else 0.96
        return ParserProbe(self.source_type, confidence, True, "QQ 数据库目录已识别")

    def validate(self, source: "ParserSource") -> "ParserValidation":
        from src.preprocessing.parser_registry import ParserValidation

        probe = self.probe(source)
        if not source.path.is_dir():
            return ParserValidation(False, "source_not_directory", probe.reason)
        if not _database_paths(source.path):
            return ParserValidation(False, "empty_source", "QQ 数据库目录中没有 SQLite 数据库文件")
        return ParserValidation(True)

    def stream_records(self, source: "ParserSource") -> Iterator[NormalizedMessage]:
        cache_root = self.snapshot_cache_root
        if cache_root is None:
            with tempfile.TemporaryDirectory(prefix="past-partner-qq-") as temporary:
                yield from self._stream_snapshot(source, Path(temporary))
            return
        cache_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="qq-", dir=cache_root) as temporary:
            yield from self._stream_snapshot(source, Path(temporary))

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
            "snapshot": "read_only",
            "schema": "generic_messages",
        }

    def _stream_snapshot(self, source: "ParserSource", cache_root: Path) -> Iterator[NormalizedMessage]:
        snapshot = create_qq_snapshot(source.path, cache_root)
        try:
            databases = _database_paths(snapshot.root)
            if any(not _is_sqlite_database(database) for database in databases):
                raise QqDatabaseError(
                    "encrypted_database",
                    "检测到非明文 SQLite 数据库；当前版本不会自动提取或猜测密钥",
                )
            schemas = _discover_schemas(databases)
            if not schemas:
                raise QqDatabaseError(
                    "unsupported_schema",
                    "快照中未识别 QQ 支持的消息表结构（需要消息表、发送者、内容和时间字段）",
                )
            yielded = False
            for schema in schemas:
                for record in _iter_schema(schema, source.metadata, snapshot.root):
                    yielded = True
                    yield record
            if not yielded:
                raise QqDatabaseError("empty_source", "QQ 数据库中没有符合条件的消息")
        finally:
            shutil.rmtree(snapshot.root, ignore_errors=True)


def _discover_schemas(databases: Sequence[Path]) -> list[_MessageSchema]:
    schemas: list[_MessageSchema] = []
    for database in sorted(databases):
        with _open_read_only(database) as connection:
            for table in _table_names(connection):
                columns = _columns(connection, table)
                normalized = {_normalize_name(name): name for name in columns}
                if not _is_message_table(table):
                    continue
                sender = _find_column(normalized, "sender")
                content = _find_column(normalized, "content")
                timestamp = _find_column(normalized, "timestamp")
                if not sender or not content or not timestamp:
                    continue
                schemas.append(
                    _MessageSchema(
                        database=database,
                        table=table,
                        sender=sender,
                        content=content,
                        timestamp=timestamp,
                        message_id=_find_column(normalized, "message_id"),
                        sender_name=_find_column(normalized, "sender_name"),
                        message_type=_find_column(normalized, "message_type"),
                        chat_id=_find_column(normalized, "chat_id"),
                    )
                )
    return schemas


def _iter_schema(
    schema: _MessageSchema,
    metadata: Any,
    snapshot_root: Path,
) -> Iterator[NormalizedMessage]:
    database = snapshot_root / schema.database.relative_to(snapshot_root)
    with _open_read_only(database) as connection:
        table = _quote_identifier(schema.table)
        selected = [
            f"{_quote_identifier(schema.sender)} AS \"__qq_sender\"",
            f"{_quote_identifier(schema.content)} AS \"__qq_content\"",
            f"{_quote_identifier(schema.timestamp)} AS \"__qq_timestamp\"",
        ]
        for alias, column in (
            ("__qq_sender_name", schema.sender_name),
            ("__qq_message_type", schema.message_type),
            ("__qq_chat_id", schema.chat_id),
        ):
            selected.append(
                f"{_quote_identifier(column)} AS \"{alias}\"" if column else f"NULL AS \"{alias}\""
            )
        identity = _quote_identifier(schema.message_id) if schema.message_id else "rowid"
        selected.append(f"{identity} AS \"__qq_id\"")
        query = (
            f"SELECT {', '.join(selected)} FROM {table} "
            f"ORDER BY {_quote_identifier(schema.timestamp)}, {identity}"
        )
        try:
            rows = connection.execute(query)
            for row in rows:
                sender_id = _text(row["__qq_sender"])
                content = _text(row["__qq_content"])
                if not sender_id or not content:
                    continue
                sender_name = _text(row["__qq_sender_name"])
                if not sender_name and sender_id == _text(metadata.get("self_id")):
                    sender_name = _text(metadata.get("self_name")) or "我"
                yield NormalizedMessage.from_mapping(
                    {
                        "sender_id": sender_id,
                        "sender_name": sender_name or sender_id,
                        "content": content,
                        "timestamp": _timestamp(row["__qq_timestamp"]),
                        "message_type": _message_type(row["__qq_message_type"]),
                        "attachments": (),
                    }
                )
        except sqlite3.DatabaseError as exc:
            raise QqDatabaseError(
                "corrupt_database",
                "QQ 数据库无法以只读 SQLite 数据库读取，可能已损坏或不是受支持版本",
            ) from exc


def _database_paths(root: Path) -> list[Path]:
    root = root.resolve()
    if not root.is_dir():
        return []
    result: list[Path] = []
    for path in sorted(root.rglob("*")):
        if path.suffix.casefold() not in _DATABASE_SUFFIXES or not path.is_file():
            continue
        resolved = path.resolve()
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise QqDatabaseError("invalid_source", "数据库路径必须保持在用户选择的目录内") from exc
        result.append(resolved)
    return result


def _with_sidecars(databases: Sequence[Path]) -> list[Path]:
    sources = set(databases)
    for database in databases:
        for suffix in ("-wal", "-shm"):
            sidecar = Path(f"{database}{suffix}")
            if sidecar.is_file():
                sources.add(sidecar)
    return sorted(sources)


def _validate_source_files(source: Path, paths: Sequence[Path]) -> None:
    for path in paths:
        try:
            path.resolve().relative_to(source)
        except ValueError as exc:
            raise QqDatabaseError(
                "invalid_source",
                "数据库及 WAL/SHM 文件必须保持在用户选择的目录内",
            ) from exc


def _capture_metadata(paths: Sequence[Path]) -> tuple[tuple[Path, int, int], ...]:
    return tuple((path, path.stat().st_size, path.stat().st_mtime_ns) for path in paths)


def _is_sqlite_database(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return handle.read(16) == b"SQLite format 3\x00"
    except OSError:
        return False


@contextmanager
def _open_read_only(path: Path) -> Iterator[sqlite3.Connection]:
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        yield connection
    except sqlite3.DatabaseError as exc:
        raise QqDatabaseError(
            "corrupt_database",
            "QQ 数据库无法以只读 SQLite 数据库打开，可能已损坏或不是受支持版本",
        ) from exc
    finally:
        if connection is not None:
            connection.close()


def _table_names(connection: sqlite3.Connection) -> list[str]:
    return [
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    ]


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    quoted = _quote_identifier(table)
    return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({quoted})")}


def _find_column(normalized_columns: dict[str, str], role: str) -> str | None:
    for alias in _COLUMN_ALIASES[role]:
        column = normalized_columns.get(_normalize_name(alias))
        if column:
            return column
    return None


def _is_message_table(table: str) -> bool:
    normalized = _normalize_name(table)
    return normalized in _MESSAGE_TABLE_HINTS or "message" in normalized or normalized.startswith("msg")


def _is_qq_layout_hint(path: Path) -> bool:
    normalized = path.name.casefold()
    return normalized in {"qq", "qq_db", "qq_database", "qqdata", "qq_storage"} or "qq" in normalized or "群" in path.name


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _normalize_name(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())


def _text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace").strip("\x00").strip()
    if isinstance(value, memoryview):
        return _text(value.tobytes())
    if value is None:
        return ""
    return str(value).strip()


def _timestamp(value: object) -> str:
    if isinstance(value, str):
        return value.strip() or "unknown"
    try:
        timestamp = float(value or 0)
    except (TypeError, ValueError):
        return "unknown"
    if timestamp > 10_000_000_000:
        timestamp /= 1000
    try:
        return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return "unknown"


def _message_type(value: object) -> str:
    if isinstance(value, bool):
        return "text"
    if isinstance(value, int):
        return _QQ_TYPES.get(value, f"type_{value}")
    text = _text(value).casefold()
    if not text:
        return "text"
    if text.isdecimal():
        return _QQ_TYPES.get(int(text), f"type_{text}")
    return {
        "txt": "text",
        "photo": "image",
        "pic": "image",
        "voice": "audio",
        "video": "video",
    }.get(text, text)


if __name__ == "__main__":
    raise SystemExit("qq_database is a parser module, not a command-line entry point")
