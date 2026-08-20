import unittest

from src.services.metrics import MetricsRegistry


class MetricsRegistryTests(unittest.TestCase):
    def test_renders_canonical_request_series_and_in_flight_gauge(self) -> None:
        registry = MetricsRegistry(max_series=4)

        registry.begin_request()
        registry.observe_request("GET", "/api/v1/health", 200)
        registry.end_request()

        rendered = registry.render_prometheus()

        self.assertIn("# TYPE past_partner_http_requests_total counter", rendered)
        self.assertIn(
            'past_partner_http_requests_total{method="GET",route="/api/v1/health",status="200"} 1',
            rendered,
        )
        self.assertIn("past_partner_http_requests_in_flight 0", rendered)

    def test_escapes_prometheus_label_values(self) -> None:
        registry = MetricsRegistry(max_series=4)

        registry.observe_request("M\\\"\n", '/route\\"\n', "5\\\"\n")

        rendered = registry.render_prometheus()

        self.assertIn('method="M\\\\\\\"\\n"', rendered)
        self.assertIn('route="/route\\\\\\\"\\n"', rendered)
        self.assertIn('status="5\\\\\\\"\\n"', rendered)

    def test_in_flight_counter_is_never_negative(self) -> None:
        registry = MetricsRegistry(max_series=4)

        registry.end_request()

        self.assertIn("past_partner_http_requests_in_flight 0", registry.render_prometheus())

    def test_series_limit_uses_bounded_overflow_series(self) -> None:
        registry = MetricsRegistry(max_series=2)

        registry.observe_request("GET", "/one", 200)
        registry.observe_request("POST", "/two", 500)
        registry.observe_request("PATCH", "/three", 204)

        rendered = registry.render_prometheus()

        self.assertEqual(2, rendered.count("past_partner_http_requests_total{"))
        self.assertIn(
            'past_partner_http_requests_total{method="GET",route="/one",status="200"} 1',
            rendered,
        )
        self.assertIn(
            'past_partner_http_requests_total{method="other",route="/other",status="other"} 2',
            rendered,
        )


if __name__ == "__main__":
    unittest.main()
