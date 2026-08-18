from __future__ import annotations

import unittest

from src.services.metadata_store import (
    MetadataIntegrityError,
    MetadataOperationalError,
    MetadataStore,
    MetadataStoreError,
)
from src.services.postgresql_metadata_store import PostgreSQLMetadataStore


class _FakeIntegrityError(Exception):
    sqlstate = "23505"


class _FakeOperationalError(Exception):
    sqlstate = "08006"


class _FakeRawConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []
        self.in_transaction = False
        self.execute_error: Exception | None = None
        self.commit_count = 0
        self.rollback_count = 0

    def execute(self, sql: str, parameters: object = ()) -> object:
        self.calls.append((sql, parameters))
        if self.execute_error is not None:
            raise self.execute_error
        if sql.strip().upper().startswith("BEGIN"):
            self.in_transaction = True
        return self

    def commit(self) -> None:
        self.commit_count += 1
        self.in_transaction = False

    def rollback(self) -> None:
        self.rollback_count += 1
        self.in_transaction = False


class _FakeCheckout:
    def __init__(self, raw: _FakeRawConnection, pool: "_FakePool") -> None:
        self.raw = raw
        self.pool = pool

    def __enter__(self) -> _FakeRawConnection:
        self.pool.checked_out += 1
        return self.raw

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> bool:
        self.pool.returned += 1
        return False


class _FakePool:
    def __init__(self, raw: _FakeRawConnection, **kwargs: object) -> None:
        self.raw = raw
        self.kwargs = kwargs
        self.open_count = 0
        self.close_count = 0
        self.checked_out = 0
        self.returned = 0

    def open(self, *, wait: bool = False) -> None:
        self.open_count += 1

    def connection(self) -> _FakeCheckout:
        return _FakeCheckout(self.raw, self)

    def close(self) -> None:
        self.close_count += 1


class PostgreSQLMetadataStoreTests(unittest.TestCase):
    def _factory(self) -> tuple[list[_FakePool], object]:
        pools: list[_FakePool] = []

        def factory(**kwargs: object) -> _FakePool:
            pool = _FakePool(_FakeRawConnection(), **kwargs)
            pools.append(pool)
            return pool

        return pools, factory

    def test_adapter_normalizes_qmark_binary_and_begin_immediate(self) -> None:
        pools, factory = self._factory()
        store = PostgreSQLMetadataStore(
            "postgresql://user:secret@example.invalid/past_partner",
            pool_factory=factory,
        )

        connection = store.connect()
        connection.execute("SELECT ? AS value", (memoryview(b"payload"),))
        connection.execute("SELECT '?' AS literal, ? -- ?\n", (1,))
        connection.execute("BEGIN IMMEDIATE")
        connection.close()

        self.assertEqual(1, len(pools))
        self.assertEqual(
            [
                ("SELECT %s AS value", (b"payload",)),
                ("SELECT '?' AS literal, %s -- ?\n", (1,)),
                ("BEGIN", ()),
            ],
            pools[0].raw.calls,
        )
        self.assertEqual(1, pools[0].checked_out)
        self.assertEqual(1, pools[0].returned)

    def test_transaction_balances_pool_and_rolls_back_on_failure(self) -> None:
        pools, factory = self._factory()
        store = PostgreSQLMetadataStore("postgresql://redacted", pool_factory=factory)

        with store.transaction(immediate=True) as connection:
            connection.execute("SELECT 1")

        self.assertEqual(1, pools[0].raw.commit_count)
        self.assertEqual(1, pools[0].returned)

        with self.assertRaises(RuntimeError):
            with store.transaction() as connection:
                connection.execute("SELECT 2")
                raise RuntimeError("abort")

        self.assertEqual(1, pools[0].raw.rollback_count)
        self.assertEqual(2, pools[0].returned)

    def test_integrity_and_operational_errors_are_stable_and_redacted(self) -> None:
        pools, factory = self._factory()
        store = PostgreSQLMetadataStore(
            "postgresql://user:password@example.invalid/past_partner",
            pool_factory=factory,
        )
        connection = store.connect()
        pools[0].raw.execute_error = _FakeIntegrityError("password=secret")

        with self.assertRaises(MetadataIntegrityError) as integrity:
            connection.execute("INSERT ?", ("secret",))
        self.assertEqual("metadata_integrity_error", integrity.exception.code)
        self.assertNotIn("password", str(integrity.exception))
        connection.close()

        pools[0].raw.execute_error = _FakeOperationalError("host=secret")
        connection = store.connect()
        with self.assertRaises(MetadataOperationalError) as operational:
            connection.execute("SELECT 1")
        self.assertEqual("metadata_operational_error", operational.exception.code)
        self.assertNotIn("host", str(operational.exception))
        connection.close()

    def test_missing_driver_fails_closed_without_dsn(self) -> None:
        def missing_driver() -> object:
            raise ModuleNotFoundError("psycopg_pool")

        store = PostgreSQLMetadataStore(
            "postgresql://user:password@example.invalid/past_partner",
            driver_loader=missing_driver,
        )

        with self.assertRaises(MetadataStoreError) as captured:
            store.connect()

        self.assertEqual("metadata_driver_unavailable", captured.exception.code)
        self.assertNotIn("password", str(captured.exception))
        self.assertNotIn("example.invalid", str(captured.exception))

    def test_close_is_idempotent_and_closes_pool_once(self) -> None:
        pools, factory = self._factory()
        store = PostgreSQLMetadataStore("postgresql://redacted", pool_factory=factory)
        self.assertIsInstance(store, MetadataStore)
        store.connect().close()

        store.close()
        store.close()

        self.assertEqual(1, pools[0].close_count)


if __name__ == "__main__":
    unittest.main()
