"""Phase 1 end-to-end checkpoint: PPE violation → Kafka → ingest → Postgres → API.

Synthesises a no-hardhat detection via the edge VisionEngine (stub detector),
publishes it to a real Kafka broker (testcontainers cp-kafka), consumes with
handle_message, then asserts exactly one ppe_no_hardhat incident in Postgres
with status=AWAITING_OPERATOR and current_tier=0.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest

from cloud.common.kafka import make_producer, publish_event
from cloud.common.schemas.anomaly import AnomalyEvent
from cloud.ingest_worker.consumer import handle_message
from cloud.common.db.models import Incident, IncidentEvent
from edge.vision.debounce import DebounceConfig, TrackDebouncer
from edge.vision.detector import Detection
from edge.vision.engine import StubFrameSource, VisionEngine
from edge.vision.zone_config import CameraConfig, ZoneConfig

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.kafka import KafkaContainer
from testcontainers.postgres import PostgresContainer
from aiokafka import AIOKafkaConsumer

from alembic import command
from alembic.config import Config

MIGRATIONS = str(Path(__file__).resolve().parents[3] / "cloud" / "migrations")
TOPIC = "vision.anomalies.v1"
DLQ = "vision.anomalies.dlq"

CFG = CameraConfig(
    camera_id="cam_01",
    site_id="plant-01",
    rtsp_url="rtsp://mediamtx:8554/cam_01",
    zones=[
        ZoneConfig(
            zone_id="zone_weld_bay",
            kind="required_ppe",
            polygon=[(0, 0), (1280, 0), (1280, 720), (0, 720)],
        )
    ],
)


class _FakeTracker:
    def update(self, detections):
        return [(i + 1, d) for i, d in enumerate(detections)]


class _FixedDetector:
    def __init__(self, dets):
        self._dets = dets

    def detect(self, frame):
        return list(self._dets)


def _async_url(sync_url: str) -> str:
    return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://").replace(
        "postgresql://", "postgresql+asyncpg://"
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_ppe_violation_becomes_incident_end_to_end():
    with PostgresContainer("postgres:16") as pg, KafkaContainer("confluentinc/cp-kafka:7.6.0") as kafka:
        sync_url = pg.get_connection_url()
        cfg = Config()
        cfg.set_main_option("script_location", MIGRATIONS)
        cfg.set_main_option("sqlalchemy.url", sync_url)
        command.upgrade(cfg, "head")

        engine = create_async_engine(_async_url(sync_url), future=True)
        session_maker = async_sessionmaker(engine, expire_on_commit=False)
        # make_producer accepts bootstrap as a keyword arg (str | None); passes
        # it straight to AIOKafkaProducer(bootstrap_servers=...).
        bootstrap = kafka.get_bootstrap_server()

        # 1) EDGE: run the vision engine over a stub stream; publish to Kafka.
        # publish_event(producer, topic, event) → None  (from cloud/common/kafka.py)
        edge_producer = await make_producer(bootstrap=bootstrap)
        try:
            async def publish(key: str, ev: AnomalyEvent) -> None:
                await publish_event(edge_producer, TOPIC, ev)

            viol = Detection("person", (600, 300, 100, 300), 0.91, no_hardhat=True)
            vision = VisionEngine(
                CFG,
                detector=_FixedDetector([viol]),
                tracker=_FakeTracker(),
                debouncer=TrackDebouncer(DebounceConfig(window=12, m_of_n=8, clear_consecutive=6)),
                publish=publish,
                frame_source=StubFrameSource([np.zeros((720, 1280, 3), dtype=np.uint8)] * 12),
                clock=lambda: datetime.now(timezone.utc),
            )
            produced = await vision.run()
            assert produced == 1
            await edge_producer.flush()
        finally:
            await edge_producer.stop()

        # 2) INGEST: consume the one message and run handle_message (DB commit).
        # handle_message(session_maker, producer, raw_value, raw_key, *, dlq_topic, grace_seconds)
        # returns: "created" | "duplicate_event_id" | "duplicate_open_dedup" | "dlq"
        consumer = AIOKafkaConsumer(
            TOPIC,
            bootstrap_servers=bootstrap,
            group_id=f"e2e-{uuid.uuid4()}",
            auto_offset_reset="earliest",
            enable_auto_commit=False,
        )
        await consumer.start()
        dlq_producer = await make_producer(bootstrap=bootstrap)
        try:
            msg = await asyncio.wait_for(consumer.getone(), timeout=30)
            status = await handle_message(
                session_maker, dlq_producer, msg.value, msg.key,
                dlq_topic=DLQ, grace_seconds=120,
            )
            assert status == "created"
        finally:
            await consumer.stop()
            await dlq_producer.stop()

        # 3) API/READ MODEL: the incident is now queryable (what GET /incidents returns).
        async with session_maker() as s:
            rows = (await s.execute(select(Incident))).scalars().all()
        assert len(rows) == 1
        inc = rows[0]
        assert inc.camera_id == "cam_01"
        assert inc.anomaly_type == "ppe_no_hardhat"
        assert inc.status.value == "AWAITING_OPERATOR"
        assert inc.current_tier == 0

        # 4) AUDIT: the incident has exactly one CREATED event.
        async with session_maker() as s:
            evts = (await s.execute(select(IncidentEvent).where(IncidentEvent.incident_id == inc.id))).scalars().all()
        assert len(evts) == 1
        assert evts[0].type == "CREATED"

        await engine.dispose()
