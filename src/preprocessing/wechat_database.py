"""Safe, read-only parsing for user-selected WeChat SQLite database directories."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import shutil
import sqlite3
import tempfile
import uuid
from pathlib import Path
from typing import Any, TYPE_CHECKING

from src.domain.messages import NormalizedMessage

if TYPE_CHECKING:
    from src.preprocessing.parser_registry import ParserProbe, ParserSource, ParserValidation


class WeChatDatabaseError(ValueError):
    """Actionable failure raised without exposing database message content."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class SnapshotChangedError(WeChatDatabaseError):
    def __init__(self, message: str) -> None:
        super().__init__("snapshot_changed", message)


@dataclass(frozen=True, slots=True)
class SnapshotFile:
    source: Path
    copied: Path


@dataclass(frozen=True, slots=True)
class WeChatDatabaseSnapshot:
    root: Path
    files: tuple[SnapshotFile, ...]


def create_wechat_snapshot(
    source_root: str | Path,
    cache_root: str | Path,
    *,
    retries: int = 3,
    copy_file: Callable[[Path, Path], None] = shutil.copy2,
) -> WeChatDatabaseSnapshot:
    """Copy selected database files and existing WAL/SHM sidecars consistently."""

    if retries < 1:
        raise ValueError("snapshot retries must be positive")
    source = Path(source_root).resolve()
    if not source.is_dir():
        raise WeChatDatabaseError(
            "source_not_directory",
            "微信数据库必须提供包含 .db 文件的目录，不能直接上传单个数据库文件",
        )
    cache = Path(cache_root).resolve()
    try:
        cache.relative_to(source)
    except ValueError:
        pass
    else:
        raise WeChatDatabaseError(
            "invalid_cache",
            "微信数据库快照缓存必须位于源目录之外",
        )
    cache.mkdir(parents=True, exist_ok=True)

    for _attempt in range(retries):
        snapshot_root: Path | None = None
        copied: list[SnapshotFile] = []
        try:
            databases = _database_paths(source)
            sources = _with_sidecars(databases)
            _validate_source_files(source, sources)
            snapshot_root = cache / f"wechat-{uuid.uuid4().hex}"
            snapshot_root.mkdir()
            before = _capture_metadata(sources)
            for source_file in sources:
                relative = source_file.relative_to(source)
                destination = snapshot_root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                copy_file(source_file, destination)
                copied.append(SnapshotFile(source_file, destination))
            databases_after = _database_paths(source)
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
            return WeChatDatabaseSnapshot(snapshot_root, tuple(copied))
        if snapshot_root is not None:
            shutil.rmtree(snapshot_root, ignore_errors=True)

    raise SnapshotChangedError("微信数据库在快照期间持续变化，请退出微信后重试")


class WeChatDatabaseParser:
    source_type = "wechat_database"

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
                "微信数据库必须提供目录，单个 .db 文件不会被当作聊天记录解析",
            )
        explicit = source.metadata.get("source_type") == self.source_type
        layout_hint = path.name.casefold() in {"db_storage", "msg", "wechat", "wechat_db"}
        if not explicit and not layout_hint:
            return ParserProbe(self.source_type, 0.0, False, "目录没有明确的微信数据库来源标识")
        databases = _database_paths(path)
        if not databases:
            return ParserProbe(self.source_type, 0.0, False, "目录中没有 .db 数据库文件")
        confidence = 0.99 if explicit else 0.96
        return ParserProbe(self.source_type, confidence, True, "微信数据库目录已识别")

    def validate(self, source: "ParserSource") -> "ParserValidation":
        from src.preprocessing.parser_registry import ParserValidation

        probe = self.probe(source)
        if not source.path.is_dir():
            return ParserValidation(False, "source_not_directory", probe.reason)
        databases = _database_paths(source.path)
        if not databases:
            return ParserValidation(False, "empty_source", "微信数据库目录中没有 .db 文件")
        return ParserValidation(True)

    def stream_records(self, source: "ParserSource") -> Iterator[NormalizedMessage]:
        cache_root = self.snapshot_cache_root
        if cache_root is None:
            with tempfile.TemporaryDirectory(prefix="past-partner-wechat-") as temporary:
                yield from self._stream_snapshot(source, Path(temporary))
            return
        cache_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="wechat-", dir=cache_root) as temporary:
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
            "unsupported_count": sum(
                1 for record in records if record.content.startswith("[unsupported message type:")
            ),
            "confidence": confidence,
            "snapshot": "read_only",
        }

    def _stream_snapshot(self, source: "ParserSource", cache_root: Path) -> Iterator[NormalizedMessage]:
        snapshot = create_wechat_snapshot(source.path, cache_root)
        try:
            databases = _database_paths(snapshot.root)
            if any(not _is_sqlite_database(database) for database in databases):
                raise WeChatDatabaseError(
                    "encrypted_database",
                    "检测到非明文 SQLite 数据库；当前版本不会自动提取或猜测密钥",
                )
            if _looks_like_v3(snapshot.root):
                yield from _iter_v3(snapshot.root, source.metadata)
            elif _looks_like_v4(snapshot.root):
                if not _chat_id(source.metadata):
                    raise WeChatDatabaseError(
                        "chat_id_required",
                        "微信 4.x 数据库导出需要提供完整 chat_id，不能用昵称猜测会话",
                    )
                yield from _iter_v4(snapshot.root, source.metadata)
            else:
                raise WeChatDatabaseError(
                    "unsupported_schema",
                    "快照中未识别微信 3.x/4.x 消息表结构",
                )
        finally:
            shutil.rmtree(snapshot.root, ignore_errors=True)


_V3_TYPES = {
    1: "text",
    3: "image",
    34: "audio",
    42: "contact_card",
    43: "video",
    47: "emoji",
    48: "location",
    49: "app",
    50: "call",
    10000: "system",
}
_V4_TYPES = {
    1: "text",
    2: "text",
    3: "image",
    34: "audio",
    42: "contact_card",
    43: "video",
    47: "emoji",
    48: "location",
    50: "call",
    10000: "system",
}


def _iter_v3(root: Path, metadata: dict[str, Any] | Any) -> Iterator[NormalizedMessage]:
    contacts = _v3_contacts(root / "MicroMsg.db")
    self_id = _text(metadata.get("self_id")) or "self"
    requested_chat = _chat_id(metadata)
    rows: list[tuple[tuple[int, int, int], sqlite3.Row, Path]] = []
    databases = sorted(
        path for path in root.rglob("MSG*.db") if path.is_file() and path.name != "MicroMsg.db"
    )
    if not databases:
        raise WeChatDatabaseError("unsupported_schema", "微信 3.x 数据库目录缺少 MSG*.db")
    for database in databases:
        with _open_read_only(database) as connection:
            if not _table_exists(connection, "MSG"):
                continue
            _require_columns(
                connection,
                "MSG",
                {"localId", "MsgSvrID", "Type", "SubType", "IsSender", "CreateTime", "StrTalker", "StrContent"},
            )
            where = ["StrTalker = ?"] if requested_chat else []
            parameters: list[Any] = [requested_chat] if requested_chat else []
            query = (
                "SELECT localId, MsgSvrID, Type, SubType, IsSender, CreateTime, "
                "StrTalker, StrContent FROM MSG"
            )
            if where:
                query += " WHERE " + " AND ".join(where)
            for row in connection.execute(query, parameters):
                key = (
                    int(row["CreateTime"] or 0),
                    int(row["MsgSvrID"] or 0),
                    int(row["localId"] or 0),
                )
                rows.append((key, row, database))
    if not rows:
        raise WeChatDatabaseError("empty_source", "微信数据库中没有符合条件的消息")

    seen: set[tuple[int, int, int]] = set()
    for _key, row, database in sorted(rows, key=lambda item: item[0]):
        identity = (
            int(row["MsgSvrID"] or 0),
            int(row["localId"] or 0),
            int(row["CreateTime"] or 0),
        )
        if identity in seen:
            continue
        seen.add(identity)
        chat_id = str(row["StrTalker"] or requested_chat or "unknown")
        is_self = bool(row["IsSender"])
        sender_id = self_id if is_self else chat_id
        content = _text(row["StrContent"])
        message_type = int(row["Type"] or -1)
        subtype = int(row["SubType"] or 0)
        if message_type != 1:
            content = f"[unsupported message type: {message_type}/{subtype}]"
        if not content:
            continue
        yield _message(
            sender_id=sender_id,
            sender_name=_contact_name(contacts, sender_id),
            content=content,
            timestamp=_timestamp(row["CreateTime"]),
            message_type=_V3_TYPES.get(message_type, f"type_{message_type}_{subtype}"),
            source={"wechat_version": "3.x", "database": database.relative_to(root).as_posix()},
        )


def _iter_v4(root: Path, metadata: dict[str, Any] | Any) -> Iterator[NormalizedMessage]:
    chat_id = _chat_id(metadata)
    if not chat_id:
        raise WeChatDatabaseError("chat_id_required", "微信 4.x 导出需要完整 chat_id")
    contacts = _v4_contacts(root / "contact" / "contact.db")
    table_name = f"Msg_{hashlib.md5(chat_id.encode('utf-8')).hexdigest()}"
    rows: list[tuple[tuple[int, int, int, int], sqlite3.Row, Path]] = []
    databases = sorted((root / "message").glob("message_*.db"))
    for database in databases:
        with _open_read_only(database) as connection:
            if not _table_exists(connection, table_name):
                continue
            _require_columns(
                connection,
                table_name,
                {"local_id", "server_id", "local_type", "sort_seq", "real_sender_id", "create_time", "message_content"},
            )
            columns = _columns(connection, table_name)
            content_type_sql = "m.WCDB_CT_message_content" if "WCDB_CT_message_content" in columns else "NULL"
            has_name_map = _table_exists(connection, "Name2Id")
            sender_join = "LEFT JOIN Name2Id AS n ON m.real_sender_id = n.rowid" if has_name_map else ""
            sender_column = "n.user_name" if has_name_map else "NULL"
            query = f'''
                SELECT m.local_id, m.server_id, m.local_type, m.sort_seq,
                       {sender_column} AS sender_username, m.create_time,
                       m.message_content, {content_type_sql} AS content_type
                FROM "{table_name}" AS m
                {sender_join}
                ORDER BY m.sort_seq, m.create_time, m.server_id, m.local_id
            '''
            rows.extend(
                (
                    (
                        int(row["sort_seq"] or 0),
                        int(row["create_time"] or 0),
                        int(row["server_id"] or 0),
                        int(row["local_id"] or 0),
                    ),
                    row,
                    database,
                )
                for row in connection.execute(query)
            )
    if not rows:
        raise WeChatDatabaseError("chat_not_found", "未找到指定完整 chat_id 对应的微信 4.x 消息表")

    self_id = _text(metadata.get("self_id"))
    seen: set[tuple[int, int, int]] = set()
    for _key, row, database in rows:
        identity = (
            int(row["server_id"] or 0),
            int(row["local_id"] or 0),
            int(row["create_time"] or 0),
        )
        if identity in seen:
            continue
        seen.add(identity)
        sender_id = _text(row["sender_username"]) or chat_id
        local_type = int(row["local_type"] or -1) & 0xFFFFFFFF
        content = _decode_v4_content(row["message_content"], row["content_type"])
        if not content:
            continue
        yield _message(
            sender_id=sender_id,
            sender_name=_contact_name(contacts, sender_id),
            content=content,
            timestamp=_timestamp(row["create_time"]),
            message_type=_V4_TYPES.get(local_type, f"type_{local_type}"),
            source={"wechat_version": "4.x", "database": database.relative_to(root).as_posix()},
        )


def _message(*, sender_id: str, sender_name: str, content: str, timestamp: str, message_type: str, source: dict[str, str]) -> NormalizedMessage:
    return NormalizedMessage.from_mapping(
        {
            "sender_id": sender_id,
            "sender_name": sender_name,
            "content": content,
            "timestamp": timestamp,
            "message_type": message_type,
            "attachments": (),
            "source": source,
        }
    )


def _database_paths(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    # Windows can receive a user-selected directory through an 8.3 alias
    # (for example, TEMP may be E:\\CODEXC~1\\Temp). Resolve the trust
    # boundary before comparing resolved children so aliases do not look like
    # directory escapes while symlink/junction escapes remain rejected.
    root = root.resolve()
    result = []
    for path in sorted(root.rglob("*.db")):
        resolved = path.resolve()
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise WeChatDatabaseError("invalid_source", "数据库路径必须保持在用户选择的目录内") from exc
        if path.is_file():
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
            raise WeChatDatabaseError(
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
        raise WeChatDatabaseError(
            "corrupt_database",
            "微信数据库无法以只读 SQLite 数据库打开，可能已损坏或不是受支持版本",
        ) from exc
    finally:
        if connection is not None:
            connection.close()


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row["name"]) for row in connection.execute(f'PRAGMA table_info("{table}")')}


def _require_columns(connection: sqlite3.Connection, table: str, required: set[str]) -> None:
    if not _table_exists(connection, table):
        raise WeChatDatabaseError("unsupported_schema", f"数据库缺少消息表 {table}")
    missing = sorted(required - _columns(connection, table))
    if missing:
        raise WeChatDatabaseError(
            "unsupported_schema",
            f"数据库表 {table} 缺少必要字段: {', '.join(missing)}",
        )


def _looks_like_v3(root: Path) -> bool:
    micro = next((path for path in root.glob("*.db") if path.name.casefold() == "micromsg.db"), None)
    if micro is None:
        return False
    for database in root.rglob("MSG*.db"):
        if database.name != micro.name:
            with _open_read_only(database) as connection:
                if _table_exists(connection, "MSG"):
                    return True
    return False


def _looks_like_v4(root: Path) -> bool:
    return (root / "contact" / "contact.db").is_file() and any(
        (root / "message").glob("message_*.db")
    )


def _v3_contacts(database: Path) -> dict[str, str]:
    if not database.is_file():
        return {}
    with _open_read_only(database) as connection:
        if not _table_exists(connection, "Contact"):
            return {}
        columns = _columns(connection, "Contact")
        required = {"UserName", "Remark", "NickName"}
        if not required.issubset(columns):
            return {}
        return {
            _text(row["UserName"]): _text(row["Remark"]) or _text(row["NickName"]) or _text(row["UserName"])
            for row in connection.execute("SELECT UserName, Remark, NickName FROM Contact")
            if _text(row["UserName"])
        }


def _v4_contacts(database: Path) -> dict[str, str]:
    if not database.is_file():
        return {}
    with _open_read_only(database) as connection:
        if not _table_exists(connection, "contact"):
            return {}
        columns = _columns(connection, "contact")
        required = {"username", "remark", "nick_name"}
        if not required.issubset(columns):
            return {}
        return {
            _text(row["username"]): _text(row["remark"]) or _text(row["nick_name"]) or _text(row["username"])
            for row in connection.execute("SELECT username, remark, nick_name FROM contact")
            if _text(row["username"])
        }


def _contact_name(contacts: dict[str, str], identifier: str) -> str:
    return contacts.get(identifier) or identifier


def _decode_v4_content(value: object, content_type: object) -> str:
    if isinstance(value, str):
        return value.strip("\x00").strip()
    if isinstance(value, memoryview):
        value = value.tobytes()
    if isinstance(value, bytes):
        try:
            return value.strip(b"\x00").decode("utf-8").strip()
        except UnicodeDecodeError:
            if content_type == 4:
                return "[compressed message content unavailable]"
    return ""


def _timestamp(value: object) -> str:
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


def _chat_id(metadata: Any) -> str | None:
    value = metadata.get("chat_id") if hasattr(metadata, "get") else None
    return _text(value) or None


def _text(value: object) -> str:
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8").strip("\x00").strip()
        except UnicodeDecodeError:
            return ""
    return str(value).strip() if value is not None else ""
