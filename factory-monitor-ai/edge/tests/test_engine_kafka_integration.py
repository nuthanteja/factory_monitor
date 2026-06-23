from __future__ import annotations

import asyncio
import json

import numpy as np
import pytest
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
# Use confluentinc/cp-kafka:7.6.0 (same image as Tasks 8 & 11):
# apache/kafka:3.7.0 lacks /etc/confluent/docker/configure and is incompatible
# with testcontainers' KafkaContainer startup script.
from testcontainers.kafka import KafkaContainer

from cloud.common.schemas.anomaly import AnomalyEvent
from edge.vision.debounce import DebounceConfig, TrackDebouncer
from edge.vision.detector import Detection
from edge.vision.engine import StubFrameSource, VisionEngine
from edge.vision.zone_config import CameraConfig, ZoneConfig

TOPIC = "vision.anomalies.v1"

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


@pytest.mark.integration
@pytest.mark.asyncio
async def test_engine_produces_event_to_real_kafka():
    with KafkaContainer("confluentinc/cp-kafka:7.6.0") as kafka:
        bootstrap = kafka.get_bootstrap_server()

        producer = AIOKafkaProducer(
            bootstrap_servers=bootstrap,
        )
        await producer.start()

        async def publish(key: str, ev: AnomalyEvent) -> None:
            await producer.send_and_wait(
                TOPIC,
                key=key.encode("utf-8"),
                value=ev.model_dump_json().encode("utf-8"),
            )

        consumer = AIOKafkaConsumer(
            TOPIC,
            bootstrap_servers=bootstrap,
            auto_offset_reset="earliest",
            enable_auto_commit=False,
            group_id="edge-it-test",
        )
        await consumer.start()

        try:
            viol = Detection("person", (600, 300, 100, 300), 0.91, no_hardhat=True)
            engine = VisionEngine(
                CFG,
                detector=_FixedDetector([viol]),
                tracker=_FakeTracker(),
                debouncer=TrackDebouncer(
                    DebounceConfig(window=12, m_of_n=8, clear_consecutive=6)
                ),
                publish=publish,
                frame_source=StubFrameSource(
                    [np.zeros((720, 1280, 3), dtype=np.uint8)] * 12
                ),
            )
            count = await engine.run()
            assert count == 1

            msg = await asyncio.wait_for(consumer.getone(), timeout=20)
            assert msg.key.decode() == "cam_01"
            payload = json.loads(msg.value.decode())
            ev = AnomalyEvent.model_validate(payload)
            assert ev.anomaly_type.value == "ppe_no_hardhat"
            assert ev.rule_id == "PPE_NO_HARDHAT"
            assert ev.camera_id == "cam_01"
            assert ev.source == "edge"
        finally:
            await consumer.stop()
            await producer.stop()
