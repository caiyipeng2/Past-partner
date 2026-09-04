"""R2-04 Task 5 operations summary error boundaries."""

from __future__ import annotations

import unittest

from src.services.metadata_store import MetadataStoreError
from src.services.operations_summary import OperationsSummaryError, OperationsSummaryService


class _BrokenMetadataStore:
    backend_name = "sqlite"

    def migrate(self) -> int:
        return 24

    def connect(self):
        raise MetadataStoreError("metadata_operational_error", "metadata operational error")

    def transaction(self, *, immediate: bool = False):
        raise MetadataStoreError("metadata_operational_error", "metadata operational error")

    def close(self) -> None:
        return None


class OperationsSummaryErrorTests(unittest.TestCase):
    def test_audit_store_failure_is_wrapped_as_operations_unavailable(self) -> None:
        service = OperationsSummaryService(_BrokenMetadataStore(), object())

        with self.assertRaises(OperationsSummaryError) as raised:
            service._audit_summary()

        self.assertEqual("operations_unavailable", raised.exception.code)

    def test_counts_include_zero_values_for_every_supported_state(self) -> None:
        class _Result:
            def fetchall(self):
                return [("queued", 2), ("failed", 1)]

        class _Connection:
            def execute(self, _query):
                return _Result()

        states = OperationsSummaryService._counts(_Connection(), "task_queue", "state")

        self.assertEqual(
            {
                "queued": 2,
                "leased": 0,
                "succeeded": 0,
                "failed": 1,
                "cancelled": 0,
            },
            states,
        )

    def test_counts_fail_closed_on_unknown_state(self) -> None:
        class _Result:
            def fetchall(self):
                return [("unexpected", 1)]

        class _Connection:
            def execute(self, _query):
                return _Result()

        with self.assertRaises(OperationsSummaryError) as raised:
            OperationsSummaryService._counts(_Connection(), "task_queue", "state")

        self.assertEqual("operations_record_corrupt", raised.exception.code)


if __name__ == "__main__":
    unittest.main()
