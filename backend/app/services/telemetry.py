from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from threading import Lock
from typing import Any


LATENCY_BUCKET_MS = (50, 100, 250, 500, 1000, 2500, 5000)


@dataclass(frozen=True)
class RequestSample:
    method: str
    endpoint: str
    status: int
    latency_ms: int
    actor_type: str
    mission_id: str | None


class InMemoryTelemetry:
    def __init__(self) -> None:
        self._lock = Lock()
        self._requests_total = 0
        self._in_flight = 0
        self._by_endpoint_status: dict[str, int] = defaultdict(int)
        self._latency_histogram: dict[str, int] = defaultdict(int)
        self._actor_counts: dict[str, int] = defaultdict(int)
        self._mission_counts: dict[str, int] = defaultdict(int)

    def begin(self) -> None:
        with self._lock:
            self._in_flight += 1

    def end(self, sample: RequestSample) -> None:
        endpoint_status_key = f"{sample.method} {sample.endpoint} {sample.status}"
        latency_bucket = self._latency_bucket(sample.latency_ms)
        with self._lock:
            self._in_flight = max(0, self._in_flight - 1)
            self._requests_total += 1
            self._by_endpoint_status[endpoint_status_key] += 1
            self._latency_histogram[latency_bucket] += 1
            self._actor_counts[sample.actor_type] += 1
            if sample.mission_id:
                self._mission_counts[sample.mission_id] += 1

    def end_sample(
        self,
        *,
        method: str,
        endpoint: str,
        status: int,
        latency_ms: int,
        actor_type: str,
        mission_id: str | None,
    ) -> None:
        self.end(
            RequestSample(
                method=method,
                endpoint=endpoint,
                status=status,
                latency_ms=latency_ms,
                actor_type=actor_type,
                mission_id=mission_id,
            )
        )

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "requests_total": self._requests_total,
                "in_flight": self._in_flight,
                "latency_buckets_ms": list(LATENCY_BUCKET_MS),
                "by_endpoint_status": dict(sorted(self._by_endpoint_status.items())),
                "latency_histogram": dict(sorted(self._latency_histogram.items())),
                "actor_types": dict(sorted(self._actor_counts.items())),
                "mission_id_counts": dict(sorted(self._mission_counts.items())),
            }

    def _latency_bucket(self, latency_ms: int) -> str:
        for bucket in LATENCY_BUCKET_MS:
            if latency_ms <= bucket:
                return f"<= {bucket}"
        return "> 5000"


telemetry = InMemoryTelemetry()
