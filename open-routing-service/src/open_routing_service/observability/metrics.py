"""Prometheus metric registry per task 3.8.

Catalog (from `closest-facility` spec):
  * ``routing_request_duration_seconds``  histogram(endpoint, result)
  * ``routing_request_total``             counter(endpoint, status_code)
  * ``routing_cache_hit_total``           counter
  * ``routing_cache_miss_total``          counter
  * ``routing_cache_error_total``         counter
  * ``closest_facility_results_count``    histogram(no labels — bounded values)
  * ``routing_replica_lag_seconds``       gauge (set by /readyz)

A dedicated ``CollectorRegistry`` keeps test isolation easy — tests can
inspect ``METRICS_REGISTRY`` directly without depending on global state.
"""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

METRICS_REGISTRY = CollectorRegistry()

# Buckets tuned for sub-second routing API targets (cached <50 ms,
# uncached <800 ms).
_LATENCY_BUCKETS = (
    0.005, 0.01, 0.025, 0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0, 10.0,
)

request_duration_seconds = Histogram(
    "routing_request_duration_seconds",
    "Latency of routing API requests in seconds.",
    labelnames=("endpoint", "result"),
    buckets=_LATENCY_BUCKETS,
    registry=METRICS_REGISTRY,
)

request_total = Counter(
    "routing_request_total",
    "Total routing API requests.",
    labelnames=("endpoint", "status_code"),
    registry=METRICS_REGISTRY,
)

cache_hit_total = Counter(
    "routing_cache_hit_total",
    "Closest-facility cache hits.",
    registry=METRICS_REGISTRY,
)

cache_miss_total = Counter(
    "routing_cache_miss_total",
    "Closest-facility cache misses (computed from DB).",
    registry=METRICS_REGISTRY,
)

cache_error_total = Counter(
    "routing_cache_error_total",
    "Closest-facility cache errors (Redis unreachable, timeouts, etc.).",
    registry=METRICS_REGISTRY,
)

# K is bounded [1, 10] but result count can be 0..K — buckets cover that.
closest_facility_results_count = Histogram(
    "closest_facility_results_count",
    "Number of facilities returned per request.",
    buckets=(0, 1, 2, 3, 5, 7, 10),
    registry=METRICS_REGISTRY,
)

replica_lag_seconds = Gauge(
    "routing_replica_lag_seconds",
    "Streaming-replication lag of the read-replica in seconds.",
    registry=METRICS_REGISTRY,
)
