#!/usr/bin/env bash
set -euo pipefail

BOOTSTRAP="${KAFKA_BOOTSTRAP_SERVERS:-kafka:9092}"

echo "kafka-init: targeting ${BOOTSTRAP}"

/opt/kafka/bin/kafka-topics.sh --bootstrap-server "${BOOTSTRAP}"   --create --if-not-exists   --topic vision.anomalies.v1   --partitions 6 --replication-factor 1   --config cleanup.policy=delete   --config retention.ms=86400000

/opt/kafka/bin/kafka-topics.sh --bootstrap-server "${BOOTSTRAP}"   --create --if-not-exists   --topic vision.anomalies.dlq   --partitions 1 --replication-factor 1   --config cleanup.policy=delete   --config retention.ms=604800000

echo "kafka-init: topics now present:"
/opt/kafka/bin/kafka-topics.sh --bootstrap-server "${BOOTSTRAP}" --list
