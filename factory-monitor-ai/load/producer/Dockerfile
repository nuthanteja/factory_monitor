# Load generator image — contains only the Python producer + cloud.common deps.
# Built from the repo root:
#   docker build -f load/producer/Dockerfile -t factory-monitor/loadgen .

FROM python:3.11-slim

WORKDIR /app

# Install only the dependencies the producer needs:
# aiokafka (for make_producer / serialize_event) + pydantic + pydantic-settings
# (for cloud.common.schemas and config).
RUN pip install --no-cache-dir \
    aiokafka==0.11.0 \
    opentelemetry-api==1.27.0 \
    opentelemetry-sdk==1.27.0 \
    pydantic==2.9.2 \
    pydantic-settings==2.5.2

# Copy only the modules the producer imports.
COPY cloud/common/schemas/ /app/cloud/common/schemas/
COPY cloud/common/config.py /app/cloud/common/config.py
COPY cloud/common/kafka.py  /app/cloud/common/kafka.py
COPY cloud/common/telemetry.py /app/cloud/common/telemetry.py
COPY cloud/common/__init__.py  /app/cloud/common/__init__.py
COPY cloud/__init__.py         /app/cloud/__init__.py
COPY load/producer/anomaly_load.py /app/anomaly_load.py

ENV PYTHONPATH=/app

ENTRYPOINT ["python", "/app/anomaly_load.py"]
