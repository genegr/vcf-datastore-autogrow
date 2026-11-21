# dsgrow-bridge: the Slack bridge (Bolt for Python, Socket Mode: outbound only).
#   docker build --network host -f docker/bridge.Dockerfile -t dsgrow-bridge:1 .
FROM python:3.12-slim-bookworm

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY bridge/requirements.txt /tmp/bridge-requirements.txt
RUN pip install --no-cache-dir -r /tmp/bridge-requirements.txt \
 && useradd -u 1000 -m dsgrow
COPY bridge/*.py /app/
USER dsgrow
WORKDIR /app
CMD ["python", "app.py"]
