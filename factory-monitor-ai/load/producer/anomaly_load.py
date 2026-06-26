"""Async Kafka load producer — drives 50→500→1000 anomaly events/s into
vision.anomalies.v1 for the k6 SLO load test.

Usage (standalone):
    python anomaly_load.py          # runs the default ramp
    python anomaly_load.py --dry-run  # build_event() only, no Kafka

The producer reuses make_producer / serialize_event from cloud.common.kafka
so it uses the identical wire format as the edge publisher.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import random

# Allow running from the repo root:  python load/producer/anomaly_load.py
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))

from cloud.common.kafka import make_producer, serialize_event  # noqa: E402
from cloud.common.schemas.anomaly import (  # noqa: E402
    AnomalyEvent,
    AnomalyType,
    Evidence,
    Severity,
)

# ── Constants ──────────────────────────────────────────────────────────────────

_CAMERAS = [f"cam_{i:02d}" for i in range(1, 7)]
_ZONES = ["zone_weld_bay", "zone_assembly", "zone_loading_dock", "zone_paint_shop"]
_ANOMALY_TYPES = list(AnomalyType)
_SEVERITIES = [Severity.LOW, Severity.MEDIUM, Severity.HIGH, Severity.CRITICAL]
_OBJECT_CLASSES = ["person", "forklift"]

# Default ramp: (rate_per_second, duration_seconds)
DEFAULT_STAGES: list[tuple[int, int]] = [
    (50, 30),    # warm-up
    (500, 60),   # ramp
    (1000, 60),  # peak
    (500, 30),   # ramp-down
    (50, 20),    # cool-down
]

# Number of concurrent coroutine pacers (each runs at target/K)
_K = 10

_TOPIC = os.environ.get("KAFKA_ANOMALIES_TOPIC", "vision.anomalies.v1")
_BOOTSTRAP = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")


# ── Event factory ──────────────────────────────────────────────────────────────


def build_event() -> AnomalyEvent:
    """Build one valid AnomalyEvent with occurred_at=now and unique IDs.

    Rules:
    - occurred_at = datetime.now(UTC)  — the latency histogram anchor
    - event_id    = uuid4             — unique per call (prevents ingest dedup)
    - dedup_key   = uuid4 fragment    — unique per call (prevents open-incident
                                         dedup silently swallowing the load)
    - source      = "replay"          — honest; allowed by the schema
    """
    camera_id = random.choice(_CAMERAS)
    track_id = f"{camera_id}:{random.randint(1000, 9999)}"
    anomaly_type = random.choice(_ANOMALY_TYPES)

    return AnomalyEvent(
        schema_version="1.0",
        event_id=str(uuid.uuid4()),
        anomaly_type=anomaly_type,
        rule_id=anomaly_type.value.upper(),
        occurred_at=datetime.now(tz=UTC),
        site_id="plant-01",
        camera_id=camera_id,
        zone_id=random.choice(_ZONES),
        track_id=track_id,
        object_class=random.choice(_OBJECT_CLASSES),
        severity=random.choice(_SEVERITIES),
        confidence=round(random.uniform(0.70, 0.99), 2),
        dedup_key=str(uuid.uuid4()),
        evidence=Evidence(
            bbox=[
                random.randint(0, 1280),
                random.randint(0, 720),
                random.randint(50, 300),
                random.randint(50, 300),
            ],
            snapshot_url="",
            footage_source="load_gen",
        ),
        source="replay",
    )


# ── Pacing coroutine ──────────────────────────────────────────────────────────


async def _pacer(
    producer: object,
    topic: str,
    rate_per_second: float,
    duration: float,
) -> int:
    """Fire events at *rate_per_second* for *duration* seconds.

    Uses producer.send (fire-and-forget) for throughput; the final flush() in
    run() drains in-flight sends.  Returns count of events sent.
    """
    sent = 0
    interval = 1.0 / rate_per_second if rate_per_second > 0 else 1.0
    deadline = asyncio.get_running_loop().time() + duration
    while asyncio.get_running_loop().time() < deadline:
        event = build_event()
        producer.send(  # type: ignore[attr-defined]
            topic,
            key=event.camera_id.encode("utf-8"),
            value=serialize_event(event),
        )
        sent += 1
        await asyncio.sleep(interval)
    return sent


# ── Main ramp runner ──────────────────────────────────────────────────────────


async def run(rate_stages: list[tuple[int, int]] | None = None) -> None:
    """Drive the load ramp.

    rate_stages: list of (events_per_second, duration_seconds).
    Each stage spawns K concurrent pacing coroutines, each at target/K rate.
    After all stages, flush() drains any in-flight produce requests.
    """
    if rate_stages is None:
        rate_stages = DEFAULT_STAGES

    producer = await make_producer(_BOOTSTRAP)
    try:
        total = 0
        for rate, duration in rate_stages:
            per_coroutine = max(1.0, rate / _K)
            print(
                f"[loadgen] stage rate={rate}/s  duration={duration}s"
                f"  coroutines={_K}  each={per_coroutine:.1f}/s"
            )
            tasks = [
                asyncio.create_task(
                    _pacer(producer, _TOPIC, per_coroutine, float(duration))
                )
                for _ in range(_K)
            ]
            counts = await asyncio.gather(*tasks)
            stage_total = sum(counts)
            total += stage_total
            print(f"[loadgen] stage done  sent={stage_total}")

        await producer.flush()
        print(f"[loadgen] complete  total_sent={total}")
    finally:
        await producer.stop()


# ── CLI entry point ───────────────────────────────────────────────────────────


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Anomaly load producer")
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Call build_event() and print one event; do not connect to Kafka.",
    )
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    if args.dry_run:
        import json

        event = build_event()
        print(json.dumps(event.model_dump(mode="json"), indent=2))
    else:
        asyncio.run(run())
