# Stage 1: Ekstraksi MediaMTX binary dari image resmi
FROM bluenviron/mediamtx:latest AS mediamtx-bin

# Stage 2: Runtime image utama berbasis Python Slim
FROM python:3.11-slim-bookworm

LABEL maintainer="RTSP Server Team"
LABEL description="RTSP Server & Streamer CLI didukung MediaMTX dan FFmpeg"

ENV PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive

# Install FFmpeg, ffprobe, dan dependensi sistem yang dibutuhkan
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    procps \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Salin binary MediaMTX dari image resmi
COPY --from=mediamtx-bin /mediamtx /usr/local/bin/mediamtx
RUN chmod +x /usr/local/bin/mediamtx

# Siapkan direktori kerja dan volume video
WORKDIR /app
RUN mkdir -p /videos

# Salin source code dan entrypoint script
COPY rtsp_server.py /app/rtsp_server.py
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

RUN chmod +x /app/rtsp_server.py /usr/local/bin/docker-entrypoint.sh && \
    ln -sf /app/rtsp_server.py /usr/local/bin/rtsp-server

# Port yang diekspos:
# 8554 : RTSP server (TCP & UDP)
# 8888 : HLS browser preview (TCP)
# 8889 : WebRTC browser preview WHEP (TCP)
# 8189 : WebRTC ICE media data (UDP)
EXPOSE 8554/tcp 8554/udp
EXPOSE 8888/tcp
EXPOSE 8889/tcp
EXPOSE 8189/udp

# Mount volume untuk folder video
VOLUME ["/videos"]

# Healthcheck untuk memastikan RTSP port 8554 aktif menerima koneksi
HEALTHCHECK --interval=15s --timeout=3s --start-period=5s --retries=3 \
    CMD python3 -c 'import socket; s = socket.socket(); s.settimeout(2); s.connect(("127.0.0.1", 8554)); s.close()' || exit 1

ENTRYPOINT ["docker-entrypoint.sh"]
CMD []
