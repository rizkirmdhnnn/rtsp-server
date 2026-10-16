#!/usr/bin/env bash
set -e

# Jika argumen pertama adalah perintah shell atau tool bawaan sistem, jalankan langsung
if [ "$1" = "sh" ] || [ "$1" = "bash" ] || [ "$1" = "ffmpeg" ] || [ "$1" = "ffprobe" ] || [ "$1" = "mediamtx" ]; then
    exec "$@"
fi

# Bangun opsi dari Environment Variables jika ada
EXTRA_ARGS=()

if [ -n "$RTSP_PORT" ]; then
    EXTRA_ARGS+=("-p" "$RTSP_PORT")
fi

if [ -n "$RTSP_AUTH" ]; then
    EXTRA_ARGS+=("--auth" "$RTSP_AUTH")
fi

if [ -n "$RTSP_STREAM" ]; then
    EXTRA_ARGS+=("-s" "$RTSP_STREAM")
fi

if [ "$RTSP_PLAYLIST" = "true" ] || [ "$RTSP_PLAYLIST" = "1" ]; then
    EXTRA_ARGS+=("--playlist")
fi

if [ "$RTSP_NO_AUDIO" = "true" ] || [ "$RTSP_NO_AUDIO" = "1" ]; then
    EXTRA_ARGS+=("--no-audio")
fi

if [ -n "$RTSP_RESOLUTION" ]; then
    EXTRA_ARGS+=("--resolution" "$RTSP_RESOLUTION")
fi

if [ -n "$RTSP_FPS" ]; then
    EXTRA_ARGS+=("--fps" "$RTSP_FPS")
fi

if [ -n "$RTSP_BITRATE" ]; then
    EXTRA_ARGS+=("--bitrate" "$RTSP_BITRATE")
fi

# Jika argumen diberikan lewat docker run / CMD
if [ $# -gt 0 ]; then
    exec python3 /app/rtsp_server.py "${EXTRA_ARGS[@]}" "$@"
fi

# Jika tidak ada argumen sama sekali, cek konfigurasi dan folder /videos
if [ "$RTSP_SAMPLE" = "true" ] || [ "$RTSP_SAMPLE" = "1" ]; then
    echo "ℹ Menjalankan mode --sample berdasarkan konfigurasi RTSP_SAMPLE=true"
    exec python3 /app/rtsp_server.py "${EXTRA_ARGS[@]}" --sample
fi

# Cek apakah folder /videos ada dan memiliki file video
VIDEO_COUNT=0
if [ -d "/videos" ]; then
    VIDEO_COUNT=$(find /videos -maxdepth 2 -type f \( -iname "*.mp4" -o -iname "*.mkv" -o -iname "*.avi" -o -iname "*.mov" -o -iname "*.flv" -o -iname "*.ts" -o -iname "*.webm" -o -iname "*.m4v" -o -iname "*.wmv" \) 2>/dev/null | wc -l | tr -d ' ')
fi

if [ "$VIDEO_COUNT" -gt 0 ]; then
    echo "📁 Ditemukan $VIDEO_COUNT video di volume /videos"
    exec python3 /app/rtsp_server.py "${EXTRA_ARGS[@]}" /videos
else
    echo "⚠️  Volume /videos kosong atau tidak ada video yang ditemukan."
    echo "💡 Beralih otomatis ke mode test pattern (--sample) agar server RTSP dapat langsung diuji."
    echo "💡 Untuk memutar video Anda, mount folder lokal berisi video ke /videos (-v \$(pwd)/videos:/videos)"
    exec python3 /app/rtsp_server.py "${EXTRA_ARGS[@]}" --sample
fi
