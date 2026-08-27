# 须在 linux/arm64（树莓派 64-bit）上构建：python3-picamera2 来自 Raspberry Pi 软件源。
FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TIMELAPSE_APP_ROOT=/opt/pi-timelapse \
    TIMELAPSE_STATE_DIR=/var/lib/pi-timelapse \
    TIMELAPSE_HOST=0.0.0.0 \
    TIMELAPSE_PORT=8080 \
    TZ=Asia/Shanghai

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl gnupg \
    && mkdir -p /etc/apt/keyrings \
    && curl -fsSL https://archive.raspberrypi.com/debian/raspberrypi.gpg.key \
        | gpg --dearmor -o /etc/apt/keyrings/raspberrypi-archive-keyring.gpg \
    && echo "deb [signed-by=/etc/apt/keyrings/raspberrypi-archive-keyring.gpg] http://archive.raspberrypi.com/debian bookworm main" \
        > /etc/apt/sources.list.d/raspi.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        python3 \
        python3-pip \
        python3-venv \
        python3-picamera2 \
        python3-libcamera \
        tzdata \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo "$TZ" > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/pi-timelapse

COPY pyproject.toml README.md ./
COPY src ./src
COPY config ./config
COPY docker/entrypoint.sh /usr/local/bin/timelapse-entrypoint

RUN chmod +x /usr/local/bin/timelapse-entrypoint \
    && python3 -m venv --system-site-packages /opt/pi-timelapse/.venv \
    && /opt/pi-timelapse/.venv/bin/pip install --no-cache-dir -e . \
    && ln -sf /opt/pi-timelapse/.venv/bin/timelapse /usr/local/bin/timelapse \
    && mkdir -p /var/lib/pi-timelapse

EXPOSE 8080

VOLUME ["/var/lib/pi-timelapse"]

HEALTHCHECK --interval=30s --timeout=8s --start-period=25s --retries=3 \
    CMD /opt/pi-timelapse/.venv/bin/python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/status', timeout=5)"

ENTRYPOINT ["timelapse-entrypoint"]
