"""Encrypted, owner-scoped persistence for distributed task leases."""

from __future__ import annotations

from contextlib import closing
from dataclasses import replace
from datetime import timedelta
import json
import re
from typing import Any, Mapping
from uuid import uuid4

from src.domain.task_queue import (
    MAX_TASK_PAYLOAD_BYTES,
    TaskRecord,
    TaskState,
    TaskValidationError,
    parse_timestamp,
    utc_now,
)
from src.services.authenticated_encryption import (
    AuthenticationError,
    AuthenticatedEncryptionService,
    InvalidEncryptedPayloadError,
)
from src.services.metadata_store import (
    MetadataIntegrityError,
    MetadataStore,
    require_metadata_store,
)


class TaskQueueError(RuntimeError):
    """Stable queue error that never includes payloads or driver details."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class TaskQueue:
    """Durable queue shared by local and production metadata adapters.

    The queue deliberately stores only routing and lease fields in plaintext.
    A worker can claim a task without knowing the owner beforehand, but every
    mutation after claiming carries the durable owner ID and lease owner.
    """

    _RECORD_VERSION = 1
    _AAD_PREFIX = "past-partner/task/v1/"
    _MAX_LEASE_SECONDS = 60 * 60
    _IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    _FAILURE_CODE = re.compile(r"^[a-z][a-z0-9_]{0,127}$")

    def __init__(
        self,
        metadata_store: MetadataStore,
        encryption: AuthenticatedEncryptionService,
    ) -> None:
        self.metadata_store = require_metadata_store(metadata_store)
        self.encryption = encryption
        self.metadata_store.migrate()

    def enqueue(
        self,
        owner_id: str,
        task_type: str,
        payload: Mapping[str, Any],
        *,
        max_attempts: int = 3,
        now: str | None = None,
        task_id: str | None = None,
    ) -> TaskRecord:
        timestamp = self._timestamp(now)
        try:
            task = TaskRecord(
                id=task_id or uuid4().hex,
                owner_id=owner_id,
                task_type=task_type,
                state=TaskState.QUEUED,
                attempts=0,
                max_attempts=max_attempts,
                available_at=timestamp,
                created_at=timestamp,
                updated_at=timestamp,
                payload=payload,
            )
        except TaskValidationError as exc:
            raise TaskQueueError(exc.code, str(exc)) from exc
        envelope = self._encode(task.owner_id, task.id, task.payload, None)
        try:
            with self.metadata_store.transaction(immediate=self._is_sqlite) as connection:
                connection.execute(
                    """
                    INSERT INTO task_queue
                        (id, owner_id, task_type, state, attempts, max_attempts,
                         available_at, leased_until, lease_owner, failure_code,
                         retryable, created_at, updated_at, encrypted_payload)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        task.id,
                        task.owner_id,
                        task.task_type,
                        task.state.value,
                        task.attempts,
                        task.max_attempts,
                        task.available_at,
                        None,
                        None,
                        None,
                        0,
                        task.created_at,
                        task.updated_at,
                        envelope,
                    ),
                )
        except MetadataIntegrityError as exc:
            raise TaskQueueError(
                "task_exists", "task ID already exists or owner is unavailable"
            ) from exc
        return task

    def get(self, owner_id: str, task_id: str) -> TaskRecord | None:
        owner = self._owner(owner_id)
        if not isinstance(task_id, str) or not task_id:
            return None
        with closing(self.metadata_store.connect()) as connection:
            row = connection.execute(
                """
                SELECT id, owner_id, task_type, state, attempts, max_attempts,
                       available_at, leased_until, lease_owner, failure_code,
                       retryable, created_at, updated_at, encrypted_payload
                FROM task_queue WHERE id = ? AND owner_id = ?
                """,
                (task_id, owner),
            ).fetchone()
        return self._decode(row) if row is not None else None

    def list(self, owner_id: str) -> list[TaskRecord]:
        owner = self._owner(owner_id)
        with closing(self.metadata_store.connect()) as connection:
            rows = connection.execute(
                """
                SELECT id, owner_id, task_type, state, attempts, max_attempts,
                       available_at, leased_until, lease_owner, failure_code,
                       retryable, created_at, updated_at, encrypted_payload
                FROM task_queue WHERE owner_id = ?
                ORDER BY created_at, id
                """,
                (owner,),
            ).fetchall()
        return [self._decode(row) for row in rows]

    def claim(
        self,
        worker_id: str,
        *,
        now: str | None = None,
        lease_seconds: int = 60,
    ) -> TaskRecord | None:
        worker = self._worker(worker_id)
        timestamp = self._timestamp(now)
        lease_until = self._plus_seconds(timestamp, lease_seconds)
        with self.metadata_store.transaction(immediate=self._is_sqlite) as connection:
            query = """
                SELECT id, owner_id, task_type, state, attempts, max_attempts,
                       available_at, leased_until, lease_owner, failure_code,
                       retryable, created_at, updated_at, encrypted_payload
                FROM task_queue
                WHERE attempts < max_attempts
                  AND ((state = 'queued' AND available_at <= ?)
                    OR (state = 'leased' AND leased_until IS NOT NULL AND leased_until <= ?))
                ORDER BY available_at, created_at, id
            """
            query += " LIMIT 1"
            if not self._is_sqlite:
                query += " FOR UPDATE SKIP LOCKED"
            row = connection.execute(query, (timestamp, timestamp)).fetchone()
            if row is None:
                return None
            current = self._decode(row)
            claimed = replace(
                current,
                state=TaskState.LEASED,
                attempts=current.attempts + 1,
                available_at=timestamp,
                leased_until=lease_until,
                lease_owner=worker,
                updated_at=timestamp,
                retryable=False,
            )
            connection.execute(
                """
                UPDATE task_queue
                SET state = ?, attempts = ?, available_at = ?, leased_until = ?,
                    lease_owner = ?, updated_at = ?, retryable = ?
                WHERE id = ? AND state = ? AND attempts = ?
                """,
                (
                    claimed.state.value,
                    claimed.attempts,
                    claimed.available_at,
                    claimed.leased_until,
                    claimed.lease_owner,
                    claimed.updated_at,
                    0,
                    claimed.id,
                    current.state.value,
                    current.attempts,
                ),
            )
            return claimed

    def renew(
        self,
        owner_id: str,
        task_id: str,
        worker_id: str,
        *,
        now: str | None = None,
        lease_seconds: int = 60,
    ) -> TaskRecord:
        owner = self._owner(owner_id)
        worker = self._worker(worker_id)
        timestamp = self._timestamp(now)
        lease_until = self._plus_seconds(timestamp, lease_seconds)
        with self.metadata_store.transaction(immediate=self._is_sqlite) as connection:
            current = self._locked_owned(connection, owner, task_id)
            self._require_lease(current, worker, timestamp)
            renewed = replace(current, leased_until=lease_until, updated_at=timestamp)
            connection.execute(
                "UPDATE task_queue SET leased_until = ?, updated_at = ? WHERE id = ? AND owner_id = ?",
                (lease_until, timestamp, task_id, owner),
            )
            return renewed

    def complete(
        self,
        owner_id: str,
        task_id: str,
        worker_id: str,
        *,
        result: Mapping[str, Any] | None = None,
        now: str | None = None,
    ) -> TaskRecord:
        owner = self._owner(owner_id)
        worker = self._worker(worker_id)
        timestamp = self._timestamp(now)
        with self.metadata_store.transaction(immediate=self._is_sqlite) as connection:
            current = self._locked_owned(connection, owner, task_id)
            self._require_lease(current, worker, timestamp)
            envelope = self._encode(owner, task_id, current.payload, result)
            completed = replace(
                current,
                state=TaskState.SUCCEEDED,
                leased_until=None,
                lease_owner=None,
                failure_code=None,
                result=result,
                retryable=False,
                updated_at=timestamp,
            )
            connection.execute(
                """
                UPDATE task_queue
                SET state = ?, leased_until = NULL, lease_owner = NULL,
                    retryable = 0, updated_at = ?, encrypted_payload = ?
                WHERE id = ? AND owner_id = ?
                """,
                (completed.state.value, timestamp, envelope, task_id, owner),
            )
            return completed

    def fail(
        self,
        owner_id: str,
        task_id: str,
        worker_id: str,
        failure_code: str,
        *,
        retryable: bool,
        now: str | None = None,
    ) -> TaskRecord:
        owner = self._owner(owner_id)
        worker = self._worker(worker_id)
        if not isinstance(failure_code, str) or not self._FAILURE_CODE.fullmatch(failure_code):
            raise TaskQueueError("task_invalid", "task failure code is invalid")
        timestamp = self._timestamp(now)
        with self.metadata_store.transaction(immediate=self._is_sqlite) as connection:
            current = self._locked_owned(connection, owner, task_id)
            self._require_lease(current, worker, timestamp)
            terminal = not retryable or current.attempts >= current.max_attempts
            failed = replace(
                current,
                state=TaskState.FAILED if terminal else TaskState.QUEUED,
                available_at=timestamp,
                leased_until=None,
                lease_owner=None,
                failure_code=failure_code,
                retryable=not terminal,
                updated_at=timestamp,
            )
            connection.execute(
                """
                UPDATE task_queue
                SET state = ?, available_at = ?, leased_until = NULL, lease_owner = NULL,
                    failure_code = ?, retryable = ?, updated_at = ?
                WHERE id = ? AND owner_id = ?
                """,
                (
                    failed.state.value,
                    timestamp,
                    failure_code,
                    int(failed.retryable),
                    timestamp,
                    task_id,
                    owner,
                ),
            )
            return failed

    def cancel(self, owner_id: str, task_id: str, *, now: str | None = None) -> TaskRecord:
        owner = self._owner(owner_id)
        timestamp = self._timestamp(now)
        with self.metadata_store.transaction(immediate=self._is_sqlite) as connection:
            current = self._locked_owned(connection, owner, task_id)
            if current.state in {TaskState.SUCCEEDED, TaskState.FAILED, TaskState.CANCELLED}:
                return current
            cancelled = replace(
                current,
                state=TaskState.CANCELLED,
                leased_until=None,
                lease_owner=None,
                retryable=False,
                updated_at=timestamp,
            )
            connection.execute(
                """
                UPDATE task_queue
                SET state = ?, leased_until = NULL, lease_owner = NULL,
                    retryable = 0, updated_at = ?
                WHERE id = ? AND owner_id = ?
                """,
                (cancelled.state.value, timestamp, task_id, owner),
            )
            return cancelled

    @property
    def _is_sqlite(self) -> bool:
        return self.metadata_store.backend_name == "sqlite"

    def _locked_owned(self, connection: Any, owner_id: str, task_id: str) -> TaskRecord:
        row = connection.execute(
            """
            SELECT id, owner_id, task_type, state, attempts, max_attempts,
                   available_at, leased_until, lease_owner, failure_code,
                   retryable, created_at, updated_at, encrypted_payload
            FROM task_queue WHERE id = ? AND owner_id = ?
            """,
            (task_id, owner_id),
        ).fetchone()
        if row is None:
            raise TaskQueueError("task_not_found", "task was not found")
        return self._decode(row)

    @staticmethod
    def _require_lease(task: TaskRecord, worker_id: str, now: str) -> None:
        if task.state in {TaskState.SUCCEEDED, TaskState.FAILED, TaskState.CANCELLED}:
            raise TaskQueueError("task_closed", "task is already closed")
        if task.state is not TaskState.LEASED:
            raise TaskQueueError("task_not_leased", "task is not leased")
        if task.lease_owner != worker_id:
            raise TaskQueueError("task_lease_owner_mismatch", "worker does not own the task lease")
        if task.leased_until is None or parse_timestamp(task.leased_until) <= parse_timestamp(now):
            raise TaskQueueError("task_lease_expired", "task lease has expired")

    def _decode(self, row: Any) -> TaskRecord:
        if not isinstance(row[13], (bytes, bytearray)):
            raise TaskQueueError("task_record_corrupt", "task record is invalid")
        try:
            payload, result = self._decode_envelope(str(row[1]), str(row[0]), bytes(row[13]))
            return TaskRecord(
                id=str(row[0]),
                owner_id=str(row[1]),
                task_type=str(row[2]),
                state=TaskState(str(row[3])),
                attempts=int(row[4]),
                max_attempts=int(row[5]),
                available_at=str(row[6]),
                leased_until=None if row[7] is None else str(row[7]),
                lease_owner=None if row[8] is None else str(row[8]),
                failure_code=None if row[9] is None else str(row[9]),
                retryable=bool(row[10]),
                created_at=str(row[11]),
                updated_at=str(row[12]),
                payload=payload,
                result=result,
            )
        except (TaskValidationError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            raise TaskQueueError("task_record_corrupt", "task record is invalid") from exc

    def _encode(
        self,
        owner_id: str,
        task_id: str,
        payload: Mapping[str, Any],
        result: Mapping[str, Any] | None,
    ) -> bytes:
        try:
            serialized = json.dumps(
                {"version": self._RECORD_VERSION, "payload": payload, "result": result},
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise TaskQueueError("task_invalid", "task payload is not JSON serializable") from exc
        if len(serialized) > MAX_TASK_PAYLOAD_BYTES:
            raise TaskQueueError("task_payload_too_large", "task payload exceeds the limit")
        return self.encryption.encrypt(serialized, self._aad(owner_id, task_id))

    def _decode_envelope(
        self,
        owner_id: str,
        task_id: str,
        envelope: bytes,
    ) -> tuple[Mapping[str, Any], Mapping[str, Any] | None]:
        try:
            plaintext = self.encryption.decrypt(envelope, self._aad(owner_id, task_id))
            value = json.loads(plaintext.decode("utf-8"))
        except (AuthenticationError, InvalidEncryptedPayloadError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TaskQueueError("task_record_authentication_failed", "task record authentication failed") from exc
        if (
            not isinstance(value, dict)
            or value.get("version") != self._RECORD_VERSION
            or not isinstance(value.get("payload"), Mapping)
            or (value.get("result") is not None and not isinstance(value.get("result"), Mapping))
        ):
            raise TaskQueueError("task_record_corrupt", "task record is invalid")
        return value["payload"], value.get("result")

    def _aad(self, owner_id: str, task_id: str) -> bytes:
        return f"{self._AAD_PREFIX}{owner_id}/{task_id}".encode("utf-8")

    @staticmethod
    def _owner(owner_id: str) -> str:
        if not isinstance(owner_id, str) or not TaskQueue._IDENTIFIER.fullmatch(owner_id):
            raise TaskQueueError("task_invalid", "owner ID is invalid")
        return owner_id

    @staticmethod
    def _worker(worker_id: str) -> str:
        if not isinstance(worker_id, str) or not TaskQueue._IDENTIFIER.fullmatch(worker_id):
            raise TaskQueueError("task_invalid", "worker ID is invalid")
        return worker_id

    @staticmethod
    def _timestamp(value: str | None) -> str:
        timestamp = utc_now() if value is None else value
        try:
            parse_timestamp(timestamp)
        except (TypeError, ValueError) as exc:
            raise TaskQueueError("task_invalid", "timestamp is invalid") from exc
        return timestamp

    @classmethod
    def _plus_seconds(cls, timestamp: str, seconds: int) -> str:
        if isinstance(seconds, bool) or not isinstance(seconds, int) or not 1 <= seconds <= cls._MAX_LEASE_SECONDS:
            raise TaskQueueError("task_invalid", "lease duration is invalid")
        return (parse_timestamp(timestamp) + timedelta(seconds=seconds)).isoformat()
