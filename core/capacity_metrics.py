"""Low-cardinality capacity signals on the existing Prometheus /metrics endpoint."""
from prometheus_client import Counter, Histogram

decisions = Counter("backend_capacity_decisions_total", "Capacity decisions",
                    ("boundary", "decision"))
wait_seconds = Histogram("backend_wait_seconds", "Admission/connection waiting time",
                         ("boundary",), buckets=(.001, .01, .1, 1, 5, 15, 60, 300))
