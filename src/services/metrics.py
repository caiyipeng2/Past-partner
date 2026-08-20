"""Bounded in-process metrics for the local HTTP runtime.

The registry deliberately accepts only canonical labels supplied by the HTTP
adapter and never persists or exports owner/provider data.  It is a process
local diagnostic aid, so restarting the server resets all values.
"""

from __future__ import annotations

from collections.abc import Hashable
import threading


class MetricsRegistry:
    """Thread-safe request counters with a hard cap on label series."""

    _OVERFLOW_LABELS = ("other", "/other", "other")
    _MAX_LABEL_LENGTH = 128

    def __init__(self, *, max_series: int = 256) -> None:
        if not isinstance(max_series, int) or isinstance(max_series, bool) or max_series < 1:
            raise ValueError("max_series must be a positive integer")
        self._max_series = max_series
        self._lock = threading.RLock()
        self._request_counts: dict[tuple[str, str, str], int] = {}
        self._in_flight = 0

    def begin_request(self) -> None:
        with self._lock:
            self._in_flight += 1

    def end_request(self) -> None:
        with self._lock:
            # Parser failures and defensive cleanup can call this after the
            # corresponding begin was skipped; never publish a negative gauge.
            self._in_flight = max(0, self._in_flight - 1)

    def observe_request(self, method: Hashable, route: Hashable, status: Hashable) -> None:
        labels = (
            self._bounded_label(method),
            self._bounded_label(route),
            self._bounded_label(status),
        )
        with self._lock:
            if labels not in self._request_counts:
                detail_count = len(self._request_counts) - int(
                    self._OVERFLOW_LABELS in self._request_counts
                )
                # Reserve one slot for overflow before the detailed series
                # reach the hard cap. This preserves already-exported counter
                # series instead of resetting them when a new route appears.
                if labels != self._OVERFLOW_LABELS and detail_count >= self._max_series - 1:
                    labels = self._OVERFLOW_LABELS
            self._request_counts[labels] = self._request_counts.get(labels, 0) + 1

    def render_prometheus(self) -> str:
        with self._lock:
            counts = sorted(self._request_counts.items())
            in_flight = self._in_flight

        lines = [
            "# HELP past_partner_http_requests_total Completed HTTP requests by canonical route.",
            "# TYPE past_partner_http_requests_total counter",
        ]
        for (method, route, status), count in counts:
            labels = (
                f'method="{self._escape_label(method)}",'
                f'route="{self._escape_label(route)}",'
                f'status="{self._escape_label(status)}"'
            )
            lines.append(f"past_partner_http_requests_total{{{labels}}} {count}")
        lines.extend(
            [
                "# HELP past_partner_http_requests_in_flight Active HTTP requests in this process.",
                "# TYPE past_partner_http_requests_in_flight gauge",
                f"past_partner_http_requests_in_flight {in_flight}",
                "",
            ]
        )
        return "\n".join(lines)

    @classmethod
    def _bounded_label(cls, value: Hashable) -> str:
        text = str(value)[: cls._MAX_LABEL_LENGTH]
        return text.encode("ascii", "backslashreplace").decode("ascii")

    @staticmethod
    def _escape_label(value: str) -> str:
        return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')
