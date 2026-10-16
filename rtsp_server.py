#!/usr/bin/env python3
"""
RTSP Server CLI
Tool CLI modern untuk streaming satu atau banyak video lokal ke RTSP Server.
Mendukung multi-channel concurrent streaming (simulasi multi-CCTV) atau playlist berurutan.
Didukung oleh MediaMTX (RTSP/WebRTC server) dan FFmpeg (streamer & hardware transcoder).
"""

import argparse
import atexit
from dataclasses import dataclass
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from typing import Dict, List, Optional, Tuple
import urllib.parse

# ANSI Color Codes
COLOR_RESET = "\033[0m"
COLOR_BOLD = "\033[1m"
COLOR_DIM = "\033[2m"
COLOR_RED = "\033[31m"
COLOR_GREEN = "\033[32m"
COLOR_YELLOW = "\033[33m"
COLOR_BLUE = "\033[34m"
COLOR_MAGENTA = "\033[35m"
COLOR_CYAN = "\033[36m"
COLOR_WHITE = "\033[37m"

SUPPORTED_EXTENSIONS = {".mp4", ".mkv", ".avi", ".mov", ".flv", ".ts", ".webm", ".m4v", ".wmv"}


def print_banner():
    banner = f"""
{COLOR_CYAN}{COLOR_BOLD}╔══════════════════════════════════════════════════════════════╗
║                   📹 RTSP STREAMER CLI                      ║
║      Stream Single / Multi-Video Lokal ke RTSP Server       ║
╚══════════════════════════════════════════════════════════════╝{COLOR_RESET}
"""
    print(banner)


def get_lan_ip() -> str:
    """Mendapatkan alamat IP LAN lokal mesin ini."""
    env_ip = os.environ.get("RTSP_HOST_IP") or os.environ.get("HOST_IP") or os.environ.get("ADVERTISED_IP")
    if env_ip:
        return env_ip.strip()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return "127.0.0.1"


def is_port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    """Mengecek apakah port tertentu sedang digunakan."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        try:
            s.bind((host, port))
            return False
        except OSError:
            return True


def check_dependencies() -> Tuple[bool, List[str]]:
    """Mengecek keberadaan binary ffmpeg, ffprobe, dan mediamtx."""
    missing = []
    if not shutil.which("ffmpeg"):
        missing.append("ffmpeg (Install via: brew install ffmpeg)")
    if not shutil.which("ffprobe"):
        missing.append("ffprobe (Install via: brew install ffmpeg)")
    if not shutil.which("mediamtx"):
        missing.append("mediamtx (Install via: brew install mediamtx)")
    return len(missing) == 0, missing


def sanitize_stream_name(name: str) -> str:
    """Membersihkan nama stream agar aman untuk URL RTSP."""
    # Izinkan huruf, angka, underscore, dash, dan slash
    cleaned = re.sub(r"[^a-zA-Z0-9_\-\/]", "_", name)
    cleaned = re.sub(r"_+", "_", cleaned)
    cleaned = cleaned.strip("_/")
    return cleaned or "stream"


def probe_video(video_path: str) -> Optional[Dict]:
    """Mengambil metadata video menggunakan ffprobe."""
    if not os.path.exists(video_path):
        return None

    cmd = [
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        video_path
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(result.stdout)

        info = {
            "format_name": data.get("format", {}).get("format_name", "unknown"),
            "duration": float(data.get("format", {}).get("duration", 0.0)),
            "size_bytes": int(data.get("format", {}).get("size", 0)),
            "bit_rate": int(data.get("format", {}).get("bit_rate", 0)),
            "has_video": False,
            "video_codec": None,
            "width": 0,
            "height": 0,
            "fps": 30.0,
            "has_audio": False,
            "audio_codec": None,
            "audio_sample_rate": 0,
            "audio_channels": 0,
        }

        for stream in data.get("streams", []):
            codec_type = stream.get("codec_type")
            if codec_type == "video" and not info["has_video"]:
                info["has_video"] = True
                info["video_codec"] = stream.get("codec_name")
                info["width"] = int(stream.get("width", 0))
                info["height"] = int(stream.get("height", 0))

                fps_str = stream.get("avg_frame_rate", "30/1")
                if "/" in fps_str:
                    num, den = fps_str.split("/")
                    if den != "0":
                        info["fps"] = round(float(num) / float(den), 2)
                else:
                    try:
                        info["fps"] = round(float(fps_str), 2)
                    except ValueError:
                        info["fps"] = 30.0
            elif codec_type == "audio" and not info["has_audio"]:
                info["has_audio"] = True
                info["audio_codec"] = stream.get("codec_name")
                info["audio_sample_rate"] = int(stream.get("sample_rate", 0))
                info["audio_channels"] = int(stream.get("channels", 0))

        return info
    except Exception:
        return None


def generate_sample_file(output_path: str, duration: int = 10, resolution: str = "1280x720", fps: int = 30) -> bool:
    """Menghasilkan file video MP4 sample lengkap dengan timer visual dan audio test."""
    print(f"{COLOR_YELLOW}⏳ Membuat video sample ke {output_path} ({duration}s, {resolution}, {fps}fps)...{COLOR_RESET}")
    vf = (
        f"drawtext=text='RTSP TEST PATTERN':fontcolor=yellow:fontsize=42:box=1:boxcolor=black@0.7:x=(w-text_w)/2:y=40,"
        f"drawtext=text='%{{localtime}}':fontcolor=white:fontsize=36:box=1:boxcolor=black@0.7:x=(w-text_w)/2:y=(h-text_h)/2,"
        f"drawtext=text='PTS %{{pts}}':fontcolor=cyan:fontsize=30:box=1:boxcolor=black@0.7:x=(w-text_w)/2:y=h-80"
    )
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"testsrc=duration={duration}:size={resolution}:rate={fps}",
        "-f", "lavfi", "-i", f"sine=frequency=880:duration={duration}:sample_rate=44100",
        "-vf", vf,
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast",
        "-c:a", "aac", "-b:a", "128k",
        output_path
    ]
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True)
        print(f"{COLOR_GREEN}✓ File video sample berhasil dibuat: {output_path}{COLOR_RESET}")
        return True
    except subprocess.CalledProcessError as e:
        print(f"{COLOR_RED}✗ Gagal membuat sample video: {e.stderr.decode()}{COLOR_RESET}")
        return False


def create_mediamtx_config(
    rtsp_port: int,
    webrtc_port: int = 8889,
    hls_port: int = 8888,
    enable_webrtc: bool = True,
    enable_hls: bool = True,
    auth_user: Optional[str] = None,
    auth_pass: Optional[str] = None,
) -> str:
    """Menghasilkan file konfigurasi YAML temporary untuk MediaMTX."""
    lines = [
        "logLevel: info",
        "logDestinations: [stdout]",
        "",
        "# RTSP Settings",
        "rtsp: yes",
        f"rtspAddress: :{rtsp_port}",
        "rtspTransports: [tcp, udp]",
        "",
        "# WebRTC Browser Preview",
        f"webrtc: {'yes' if enable_webrtc else 'no'}",
        f"webrtcAddress: :{webrtc_port}",
    ]

    env_ip = os.environ.get("RTSP_HOST_IP") or os.environ.get("HOST_IP") or os.environ.get("ADVERTISED_IP")
    if env_ip and enable_webrtc:
        lines.append(f"webrtcAdditionalHosts: [{env_ip.strip()}]")

    lines.extend([
        "",
        "# HLS Browser Preview",
        f"hls: {'yes' if enable_hls else 'no'}",
        f"hlsAddress: :{hls_port}",
        "",
        "# Nonaktifkan protokol lain yang tidak diperlukan",
        "rtmp: no",
        "srt: no",
        "",
        "paths:",
        "  all_others:",
    ])

    if auth_user and auth_pass:
        lines.extend([
            f"    publishUser: {auth_user}",
            f"    publishPass: {auth_pass}",
            f"    readUser: {auth_user}",
            f"    readPass: {auth_pass}",
        ])

    cfg_content = "\n".join(lines) + "\n"
    tmp = tempfile.NamedTemporaryFile("w", suffix=".yml", delete=False)
    tmp.write(cfg_content)
    tmp.flush()
    tmp.close()
    return tmp.name


def build_dashboard_html(server: "RTSPServerCLI") -> str:
    """Menghasilkan halaman web HTML dashboard modern yang menampilkan semua channel dan link streaming."""
    lan_ip = get_lan_ip()
    auth_prefix = f"{server.auth_user}:{server.auth_pass}@" if (server.auth_user and server.auth_pass) else ""
    rtsp_port = server.args.port
    webrtc_port = server.args.webrtc_port
    hls_port = server.args.hls_port

    cards_html = []
    all_rtsp_urls = []

    for idx, target in enumerate(server.targets, 1):
        rtsp_url = f"rtsp://{auth_prefix}{lan_ip}:{rtsp_port}/{target.name}"
        webrtc_url = f"http://{lan_ip}:{webrtc_port}/{target.name}"
        hls_url = f"http://{lan_ip}:{hls_port}/{target.name}"
        all_rtsp_urls.append(rtsp_url)

        if target.source_type == "sample":
            src_desc = "Synthetic Test Pattern"
            res_desc = f"{server.args.resolution or '1280x720'} @ {server.args.fps or 30} FPS"
            codec_desc = "H.264 / AAC"
        elif target.source_type == "playlist":
            src_desc = f"Playlist ({len(target.video_files or [])} Video)"
            res_desc = f"{target.info['width']}x{target.info['height']} @ {target.info['fps']}fps" if target.info else "Auto"
            codec_desc = target.info.get("video_codec", "H.264").upper() if target.info else "H.264"
        else:
            src_desc = Path(target.video_path or "").name
            res_desc = f"{target.info['width']}x{target.info['height']} @ {target.info['fps']}fps" if target.info else "Auto"
            codec_desc = target.info.get("video_codec", "H.264").upper() if target.info else "H.264"

        vlc_cmd = f"vlc \"{rtsp_url}\""
        ffplay_cmd = f"ffplay -rtsp_transport tcp \"{rtsp_url}\""
        py_snippet = f"import cv2; cap = cv2.VideoCapture('{rtsp_url}')"

        card = f"""
        <div class="channel-card" data-name="{target.name.lower()}">
            <div class="card-header">
                <div class="channel-title">
                    <span class="live-indicator"></span>
                    <h3 title="{target.name}">{target.name}</h3>
                </div>
                <span class="badge badge-codec">{codec_desc}</span>
            </div>
            <div class="card-meta">
                <span title="Resolusi & Framerate">📐 {res_desc}</span>
                <span title="File Sumber">📁 {src_desc}</span>
            </div>

            <div class="url-group">
                <label>RTSP Stream URL:</label>
                <div class="copy-box">
                    <input type="text" readonly value="{rtsp_url}" id="rtsp-{idx}">
                    <button class="btn btn-copy" onclick="copyText('{rtsp_url}', this)" title="Salin URL RTSP">
                        <span>📋 Salin</span>
                    </button>
                </div>
            </div>

            <div class="btn-group">
                <a href="{webrtc_url}" target="_blank" class="btn btn-webrtc" title="Buka WebRTC Player di Tab Baru">
                    ⚡ WebRTC Live
                </a>
                <a href="{hls_url}" target="_blank" class="btn btn-hls" title="Buka HLS Player di Tab Baru">
                    📺 HLS Player
                </a>
                <button class="btn btn-preview" onclick="openPreview('{webrtc_url}', '{target.name}')" title="Preview video langsung di halaman ini">
                    👁️ Preview
                </button>
            </div>

            <details class="cli-details">
                <summary>💻 Perintah Cepat (VLC / FFplay / Python)</summary>
                <div class="cli-content">
                    <div class="cli-item">
                        <span>VLC Player:</span>
                        <code>{vlc_cmd}</code>
                    </div>
                    <div class="cli-item">
                        <span>FFplay:</span>
                        <code>{ffplay_cmd}</code>
                    </div>
                    <div class="cli-item">
                        <span>Python OpenCV:</span>
                        <code>{py_snippet}</code>
                    </div>
                </div>
            </details>
        </div>
        """
        cards_html.append(card)

    cards_joined = "\n".join(cards_html)
    all_urls_txt = "\\n".join(all_rtsp_urls)

    return f"""<!DOCTYPE html>
<html lang="id">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>📹 RTSP Server Stream Dashboard</title>
    <style>
        :root {{
            --bg-base: #0B0F19;
            --bg-surface: #111827;
            --bg-card: #1F2937;
            --border: #374151;
            --text-main: #F9FAFB;
            --text-muted: #9CA3AF;
            --primary: #06B6D4;
            --primary-hover: #0891B2;
            --success: #10B981;
            --purple: #8B5CF6;
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            background-color: var(--bg-base);
            color: var(--text-main);
            min-height: 100vh;
            padding: 24px;
        }}
        .container {{
            max-width: 1400px;
            margin: 0 auto;
        }}
        header {{
            background: var(--bg-surface);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 24px;
            margin-bottom: 24px;
            display: flex;
            flex-wrap: wrap;
            justify-content: space-between;
            align-items: center;
            gap: 16px;
        }}
        .header-title {{
            display: flex;
            align-items: center;
            gap: 14px;
        }}
        .header-title h1 {{
            font-size: 24px;
            font-weight: 700;
            color: #FFF;
        }}
        .header-title p {{
            font-size: 14px;
            color: var(--text-muted);
            margin-top: 4px;
        }}
        .stats-grid {{
            display: flex;
            flex-wrap: wrap;
            gap: 12px;
        }}
        .stat-badge {{
            background: var(--bg-card);
            border: 1px solid var(--border);
            padding: 8px 16px;
            border-radius: 8px;
            font-size: 13px;
            display: flex;
            align-items: center;
            gap: 8px;
        }}
        .live-dot {{
            width: 10px;
            height: 10px;
            background-color: var(--success);
            border-radius: 50%;
            box-shadow: 0 0 10px var(--success);
            animation: pulse 1.8s infinite;
        }}
        @keyframes pulse {{
            0% {{ transform: scale(0.95); box-shadow: 0 0 0 0 rgba(16, 185, 129, 0.7); }}
            70% {{ transform: scale(1); box-shadow: 0 0 0 8px rgba(16, 185, 129, 0); }}
            100% {{ transform: scale(0.95); box-shadow: 0 0 0 0 rgba(16, 185, 129, 0); }}
        }}
        .toolbar {{
            display: flex;
            flex-wrap: wrap;
            justify-content: space-between;
            align-items: center;
            gap: 16px;
            margin-bottom: 24px;
        }}
        .search-box {{
            flex: 1;
            min-width: 280px;
            position: relative;
        }}
        .search-box input {{
            width: 100%;
            padding: 12px 16px 12px 42px;
            background: var(--bg-surface);
            border: 1px solid var(--border);
            border-radius: 8px;
            color: #FFF;
            font-size: 15px;
            outline: none;
            transition: border-color 0.2s;
        }}
        .search-box input:focus {{
            border-color: var(--primary);
        }}
        .search-icon {{
            position: absolute;
            left: 14px;
            top: 50%;
            transform: translateY(-50%);
            color: var(--text-muted);
        }}
        .action-buttons {{
            display: flex;
            gap: 10px;
            flex-wrap: wrap;
        }}
        .btn {{
            display: inline-flex;
            align-items: center;
            justify-content: center;
            padding: 9px 16px;
            font-size: 13px;
            font-weight: 600;
            border-radius: 6px;
            cursor: pointer;
            text-decoration: none;
            border: none;
            transition: all 0.2s;
            gap: 6px;
        }}
        .btn-primary {{
            background: var(--primary);
            color: #000;
        }}
        .btn-primary:hover {{ background: var(--primary-hover); }}
        .btn-secondary {{
            background: var(--bg-card);
            color: var(--text-main);
            border: 1px solid var(--border);
        }}
        .btn-secondary:hover {{ background: #283548; }}
        .btn-webrtc {{
            background: rgba(6, 182, 212, 0.15);
            color: var(--primary);
            border: 1px solid rgba(6, 182, 212, 0.4);
            flex: 1;
        }}
        .btn-webrtc:hover {{ background: rgba(6, 182, 212, 0.3); }}
        .btn-hls {{
            background: rgba(139, 92, 246, 0.15);
            color: #A78BFA;
            border: 1px solid rgba(139, 92, 246, 0.4);
            flex: 1;
        }}
        .btn-hls:hover {{ background: rgba(139, 92, 246, 0.3); }}
        .btn-preview {{
            background: rgba(16, 185, 129, 0.15);
            color: #34D399;
            border: 1px solid rgba(16, 185, 129, 0.4);
            flex: 1;
        }}
        .btn-preview:hover {{ background: rgba(16, 185, 129, 0.3); }}
        .channels-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(360px, 1fr));
            gap: 20px;
        }}
        .channel-card {{
            background: var(--bg-surface);
            border: 1px solid var(--border);
            border-radius: 10px;
            padding: 20px;
            display: flex;
            flex-direction: column;
            gap: 14px;
            transition: transform 0.15s, border-color 0.15s;
        }}
        .channel-card:hover {{
            border-color: #4B5563;
            transform: translateY(-2px);
        }}
        .card-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 10px;
        }}
        .channel-title {{
            display: flex;
            align-items: center;
            gap: 8px;
            min-width: 0;
        }}
        .channel-title h3 {{
            font-size: 16px;
            font-weight: 600;
            color: #FFF;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }}
        .live-indicator {{
            width: 8px;
            height: 8px;
            background: var(--success);
            border-radius: 50%;
            flex-shrink: 0;
        }}
        .badge {{
            font-size: 11px;
            padding: 3px 8px;
            border-radius: 4px;
            font-weight: 700;
            text-transform: uppercase;
        }}
        .badge-codec {{
            background: #1E293B;
            color: var(--primary);
            border: 1px solid rgba(6, 182, 212, 0.3);
        }}
        .card-meta {{
            font-size: 13px;
            color: var(--text-muted);
            display: flex;
            flex-direction: column;
            gap: 4px;
        }}
        .card-meta span {{
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }}
        .url-group {{
            display: flex;
            flex-direction: column;
            gap: 6px;
        }}
        .url-group label {{
            font-size: 12px;
            font-weight: 600;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}
        .copy-box {{
            display: flex;
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 6px;
            overflow: hidden;
        }}
        .copy-box input {{
            background: transparent;
            border: none;
            padding: 8px 12px;
            color: var(--success);
            font-family: monospace;
            font-size: 13px;
            flex: 1;
            outline: none;
            min-width: 0;
        }}
        .btn-copy {{
            background: #374151;
            color: #FFF;
            border: none;
            padding: 0 12px;
            font-size: 12px;
            cursor: pointer;
            transition: background 0.2s;
            white-space: nowrap;
        }}
        .btn-copy:hover {{ background: #4B5563; }}
        .btn-copy.copied {{ background: var(--success); color: #000; }}
        .btn-group {{
            display: flex;
            gap: 8px;
            flex-wrap: wrap;
        }}
        details.cli-details {{
            border-top: 1px solid var(--border);
            padding-top: 10px;
            font-size: 12px;
        }}
        details.cli-details summary {{
            cursor: pointer;
            color: var(--text-muted);
            font-weight: 500;
            user-select: none;
        }}
        details.cli-details summary:hover {{ color: #FFF; }}
        .cli-content {{
            margin-top: 10px;
            display: flex;
            flex-direction: column;
            gap: 8px;
        }}
        .cli-item span {{
            color: var(--text-muted);
            display: block;
            margin-bottom: 2px;
            font-size: 11px;
        }}
        .cli-item code {{
            background: #0D1117;
            padding: 6px 8px;
            border-radius: 4px;
            display: block;
            color: #E2E8F0;
            font-family: monospace;
            word-break: break-all;
            user-select: all;
        }}
        /* Modal Preview */
        .modal-overlay {{
            display: none;
            position: fixed;
            inset: 0;
            background: rgba(0, 0, 0, 0.85);
            z-index: 1000;
            justify-content: center;
            align-items: center;
            padding: 20px;
        }}
        .modal-content {{
            background: var(--bg-surface);
            border: 1px solid var(--border);
            border-radius: 12px;
            width: 100%;
            max-width: 900px;
            overflow: hidden;
            display: flex;
            flex-direction: column;
            max-height: 90vh;
        }}
        .modal-header {{
            padding: 16px 20px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 1px solid var(--border);
        }}
        .modal-header h3 {{ font-size: 18px; color: #FFF; }}
        .modal-close {{
            background: transparent;
            border: none;
            color: var(--text-muted);
            font-size: 24px;
            cursor: pointer;
            line-height: 1;
        }}
        .modal-close:hover {{ color: #FFF; }}
        .modal-body {{
            position: relative;
            padding-bottom: 56.25%; /* 16:9 ratio */
            height: 0;
            overflow: hidden;
            background: #000;
        }}
        .modal-body iframe {{
            position: absolute;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            border: none;
        }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div class="header-title">
                <span class="live-dot"></span>
                <div>
                    <h1>RTSP Streamer Dashboard</h1>
                    <p>Monitoring & Katalog Stream Server (MediaMTX & FFmpeg)</p>
                </div>
            </div>
            <div class="stats-grid">
                <div class="stat-badge">
                    <span>📡 Status:</span>
                    <strong style="color: var(--success);">ONLINE</strong>
                </div>
                <div class="stat-badge">
                    <span>📹 Channel:</span>
                    <strong id="channel-count">{len(server.targets)} Aktif</strong>
                </div>
                <div class="stat-badge">
                    <span>🌐 IP Server:</span>
                    <strong>{lan_ip}</strong>
                </div>
            </div>
        </header>

        <div class="toolbar">
            <div class="search-box">
                <span class="search-icon">🔍</span>
                <input type="text" id="searchInput" placeholder="Cari nama channel CCTV..." oninput="filterChannels()">
            </div>
            <div class="action-buttons">
                <button class="btn btn-primary" onclick="copyAllUrls()">
                    📋 Salin Semua URL RTSP
                </button>
                <a href="/playlist.m3u" download="cctv_streams.m3u" class="btn btn-secondary">
                    📥 Download Playlist (.M3U)
                </a>
                <a href="/api/channels" target="_blank" class="btn btn-secondary">
                    JSON API
                </a>
            </div>
        </div>

        <div class="channels-grid" id="channelsGrid">
            {cards_joined}
        </div>
    </div>

    <!-- Modal Video Preview -->
    <div class="modal-overlay" id="previewModal" onclick="closePreview(event)">
        <div class="modal-content" onclick="event.stopPropagation()">
            <div class="modal-header">
                <h3 id="modalTitle">Live Preview</h3>
                <button class="modal-close" onclick="closePreview()">&times;</button>
            </div>
            <div class="modal-body">
                <iframe id="previewIframe" src="" allowfullscreen></iframe>
            </div>
        </div>
    </div>

    <script>
        function copyText(text, btn) {{
            navigator.clipboard.writeText(text).then(() => {{
                const original = btn.innerHTML;
                btn.innerHTML = '✓ Tersalin!';
                btn.classList.add('copied');
                setTimeout(() => {{
                    btn.innerHTML = original;
                    btn.classList.remove('copied');
                }}, 2000);
            }});
        }}

        function copyAllUrls() {{
            const allUrls = "{all_urls_txt}".replace(/\\n/g, '\\n');
            navigator.clipboard.writeText(allUrls).then(() => {{
                alert('Berhasil menyalin semua {len(server.targets)} URL RTSP ke clipboard!');
            }});
        }}

        function filterChannels() {{
            const query = document.getElementById('searchInput').value.toLowerCase().trim();
            const cards = document.querySelectorAll('.channel-card');
            let visibleCount = 0;
            cards.forEach(card => {{
                const name = card.getAttribute('data-name');
                if (name.includes(query)) {{
                    card.style.display = 'flex';
                    visibleCount++;
                }} else {{
                    card.style.display = 'none';
                }}
            }});
            document.getElementById('channel-count').innerText = visibleCount + ' / {len(server.targets)} Aktif';
        }}

        function openPreview(url, title) {{
            document.getElementById('modalTitle').innerText = 'Live Preview: ' + title;
            document.getElementById('previewIframe').src = url;
            document.getElementById('previewModal').style.display = 'flex';
        }}

        function closePreview() {{
            document.getElementById('previewIframe').src = '';
            document.getElementById('previewModal').style.display = 'none';
        }}

        document.addEventListener('keydown', (e) => {{
            if (e.key === 'Escape') closePreview();
        }});
    </script>
</body>
</html>
"""


def create_dashboard_handler(server: "RTSPServerCLI"):
    """Factory untuk membuat HTTP request handler yang terikat ke instance RTSPServerCLI."""
    class DashboardHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args):
            # Matikan access logging di stdout agar tidak mengacaukan tampilan CLI
            _ = (format, args)
            return

        def do_HEAD(self):
            parsed_path = urllib.parse.urlparse(self.path)
            path = parsed_path.path
            if path in ("/", "/index.html"):
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
            elif path == "/api/channels":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
            elif path == "/playlist.m3u":
                self.send_response(200)
                self.send_header("Content-Type", "audio/x-mpegurl")
                self.end_headers()
            else:
                self.send_response(404)
                self.end_headers()

        def do_GET(self):
            parsed_path = urllib.parse.urlparse(self.path)
            path = parsed_path.path

            if path == "/" or path == "/index.html":
                html = build_dashboard_html(server)
                content = html.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)

            elif path == "/api/channels":
                lan_ip = get_lan_ip()
                auth_prefix = f"{server.auth_user}:{server.auth_pass}@" if (server.auth_user and server.auth_pass) else ""
                channels = []
                for target in server.targets:
                    channels.append({
                        "name": target.name,
                        "source_type": target.source_type,
                        "rtsp_url": f"rtsp://{auth_prefix}{lan_ip}:{server.args.port}/{target.name}",
                        "webrtc_url": f"http://{lan_ip}:{server.args.webrtc_port}/{target.name}",
                        "hls_url": f"http://{lan_ip}:{server.args.hls_port}/{target.name}",
                        "width": target.info.get("width") if target.info else 0,
                        "height": target.info.get("height") if target.info else 0,
                        "fps": target.info.get("fps") if target.info else 30.0,
                        "video_codec": target.info.get("video_codec") if target.info else "h264",
                    })

                data = {
                    "status": "online",
                    "uptime_seconds": int(time.time() - server.start_time) if server.start_time else 0,
                    "host_ip": lan_ip,
                    "total_channels": len(channels),
                    "channels": channels
                }
                content = json.dumps(data, indent=2).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)

            elif path == "/playlist.m3u":
                lan_ip = get_lan_ip()
                auth_prefix = f"{server.auth_user}:{server.auth_pass}@" if (server.auth_user and server.auth_pass) else ""
                lines = ["#EXTM3U"]
                for target in server.targets:
                    rtsp_url = f"rtsp://{auth_prefix}{lan_ip}:{server.args.port}/{target.name}"
                    lines.append(f"#EXTINF:-1 tvg-id=\"{target.name}\" tvg-name=\"{target.name}\",{target.name}")
                    lines.append(rtsp_url)

                content = "\n".join(lines).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "audio/x-mpegurl")
                self.send_header("Content-Disposition", "attachment; filename=\"cctv_streams.m3u\"")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)

            else:
                self.send_response(404)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(b"404 Not Found")

    return DashboardHandler


@dataclass
class StreamTarget:
    name: str                           # Path RTSP, misal: "live/stream", "cam1", "kamera_depan"
    source_type: str                    # "file", "sample", atau "playlist"
    video_path: Optional[str] = None    # Path ke file video (jika single file)
    video_files: Optional[List[str]] = None  # Daftar file video (jika playlist)
    playlist_file: Optional[str] = None # File sementara concat ffmpeg
    info: Optional[Dict] = None         # Metadata video (ffprobe)
    proc: Optional[subprocess.Popen] = None  # Proses FFmpeg yang sedang berjalan


class RTSPServerCLI:
    def __init__(self, args):
        self.args = args
        self.mediamtx_proc: Optional[subprocess.Popen] = None
        self.config_path: Optional[str] = None
        self.is_running = False
        self.start_time = 0.0
        self.targets: List[StreamTarget] = []
        self.sessions: Dict[str, Dict] = {}
        self.sessions_lock = threading.Lock()
        self.monitor_thread: Optional[threading.Thread] = None
        self.http_server: Optional[HTTPServer] = None
        self.web_thread: Optional[threading.Thread] = None

        # Parse authentication
        self.auth_user = None
        self.auth_pass = None
        if self.args.auth:
            if ":" in self.args.auth:
                self.auth_user, self.auth_pass = self.args.auth.split(":", 1)
            else:
                self.auth_user = self.args.auth
                self.auth_pass = ""

    def _monitor_mediamtx(self):
        """Membaca log dari MediaMTX secara real-time untuk mendeteksi event koneksi klien."""
        if not self.mediamtx_proc or not self.mediamtx_proc.stdout:
            return

        for raw_line in iter(self.mediamtx_proc.stdout.readline, ""):
            line = raw_line.strip()
            if not line:
                continue

            # 1. Session created
            m_create = re.search(r"\[(RTSP|WebRTC|HLS|RTMP)\] \[session ([a-f0-9]+)\] created by ([^ ]+)", line)
            if m_create:
                proto, sid, ip = m_create.groups()
                with self.sessions_lock:
                    self.sessions[sid] = {
                        "proto": proto,
                        "ip": ip,
                        "is_publisher": False,
                        "path": None,
                        "time": time.time()
                    }
                continue

            # 2. Publisher started
            m_pub = re.search(r"\[session ([a-f0-9]+)\] is publishing to path '([^']+)'", line)
            if m_pub:
                sid, path = m_pub.groups()
                with self.sessions_lock:
                    if sid in self.sessions:
                        self.sessions[sid]["is_publisher"] = True
                        self.sessions[sid]["path"] = path
                continue

            # 3. Client started reading
            m_read = re.search(r"\[(RTSP|WebRTC|HLS|RTMP)\] \[session ([a-f0-9]+)\] is reading from path '([^']+)'(?:, with ([^,]+))?", line)
            if m_read:
                proto, sid, path, transport = m_read.groups()
                with self.sessions_lock:
                    s = self.sessions.get(sid, {
                        "proto": proto,
                        "ip": "unknown",
                        "is_publisher": False,
                        "time": time.time()
                    })
                    s["path"] = path
                    s["transport"] = (transport or "TCP").strip()
                    s["is_publisher"] = False
                    self.sessions[sid] = s
                    client_ip = s["ip"]
                    proto_info = f"{proto}/{s['transport']}"

                ts = datetime.now().strftime("%H:%M:%S")
                sys.stdout.write("\r\033[K")
                print(f"  {COLOR_DIM}[{ts}]{COLOR_RESET} {COLOR_GREEN}🟢 CLIENT CONNECTED   {COLOR_RESET}: {COLOR_BOLD}{client_ip:<21}{COLOR_RESET} ──▶ Channel {COLOR_CYAN}'{path}'{COLOR_RESET} ({proto_info})")
                sys.stdout.flush()
                continue

            # 4. Session destroyed
            m_des = re.search(r"\[session ([a-f0-9]+)\] destroyed(?:.*?: (.+))?", line)
            if m_des:
                sid, reason = m_des.groups()
                s = None
                with self.sessions_lock:
                    if sid in self.sessions:
                        s = self.sessions.pop(sid)

                if s and not s.get("is_publisher") and s.get("path"):
                    ts = datetime.now().strftime("%H:%M:%S")
                    dur = int(time.time() - s.get("time", time.time()))
                    client_ip = s["ip"]
                    path = s["path"]
                    reason_clean = (reason or "Client closed connection").strip()
                    sys.stdout.write("\r\033[K")
                    print(f"  {COLOR_DIM}[{ts}]{COLOR_RESET} {COLOR_RED}🔴 CLIENT DISCONNECTED{COLOR_RESET}: {COLOR_BOLD}{client_ip:<21}{COLOR_RESET} ◀── Channel {COLOR_CYAN}'{path}'{COLOR_RESET} (Durasi: {dur}s | {reason_clean})")
                    sys.stdout.flush()
                continue

    def parse_targets(self) -> List[StreamTarget]:
        """Mem-parse input video/folder dari argumen CLI menjadi daftar StreamTarget."""
        targets: List[StreamTarget] = []

        # 1. Mode Sample (Synthetic test pattern)
        if self.args.sample:
            stream_name = sanitize_stream_name(self.args.stream)
            targets.append(StreamTarget(
                name=stream_name,
                source_type="sample",
            ))
            return targets

        raw_inputs: List[str] = self.args.videos or []
        if not raw_inputs:
            return []

        # Kumpulkan semua file video dari input (termasuk scan direktori)
        # Format input bisa berupa:
        # - "video.mp4"
        # - "cam1=video1.mp4" atau "cam1:video1.mp4"
        # - "/path/to/folder_video/"
        collected_items: List[Tuple[Optional[str], str]] = []

        for item in raw_inputs:
            # Cek apakah format custom alias "nama=path" atau "nama:path" (hindari path windows/relatif seperti C:\)
            custom_alias = None
            path_str = item

            if "=" in item:
                parts = item.split("=", 1)
                custom_alias = parts[0].strip()
                path_str = parts[1].strip()
            elif ":" in item and not item.startswith("http") and not os.path.exists(item):
                parts = item.split(":", 1)
                if len(parts[0]) > 1:  # hindari drive letter 'C:'
                    custom_alias = parts[0].strip()
                    path_str = parts[1].strip()

            path_obj = Path(path_str).expanduser().resolve()

            if not path_obj.exists():
                print(f"{COLOR_RED}✗ File atau folder tidak ditemukan: {path_str}{COLOR_RESET}")
                sys.exit(1)

            if path_obj.is_dir():
                # Scan folder untuk semua file video yang didukung
                found = sorted([
                    f for f in path_obj.iterdir()
                    if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS
                ])
                if not found:
                    print(f"{COLOR_RED}✗ Tidak ditemukan file video di folder: {path_obj}{COLOR_RESET}")
                    print(f"  Format yang didukung: {', '.join(sorted(SUPPORTED_EXTENSIONS))}")
                    sys.exit(1)

                print(f"{COLOR_CYAN}📁 Ditemukan {len(found)} video di folder '{path_obj.name}/'{COLOR_RESET}")
                for vf in found:
                    collected_items.append((None, str(vf)))
            else:
                collected_items.append((custom_alias, str(path_obj)))

        if not collected_items:
            return []

        # 2. Mode Playlist: Menggabungkan banyak video menjadi SATU stream berurutan
        if self.args.playlist and len(collected_items) > 1:
            all_files = [path for _, path in collected_items]
            stream_name = sanitize_stream_name(self.args.stream)

            # Buat file concat playlist temporary untuk FFmpeg
            tmp_pl = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False)
            for f in all_files:
                # Escape tanda petik satu untuk syntax concat demuxer ffmpeg
                escaped_f = f.replace("'", "'\\''")
                tmp_pl.write(f"file '{escaped_f}'\n")
            tmp_pl.flush()
            tmp_pl.close()

            # Ambil probe dari file pertama sebagai representasi
            info = probe_video(all_files[0])

            targets.append(StreamTarget(
                name=stream_name,
                source_type="playlist",
                video_files=all_files,
                playlist_file=tmp_pl.name,
                info=info
            ))
            return targets

        # 3. Mode Multi-Channel: Setiap video menjadi channel stream terpisah
        used_names = set()
        for alias, video_path in collected_items:
            info = probe_video(video_path)
            if not info or not info.get("has_video"):
                print(f"{COLOR_YELLOW}⚠ Lewati file non-video / rusak: {video_path}{COLOR_RESET}")
                continue

            if alias:
                s_name = sanitize_stream_name(alias)
            elif len(collected_items) == 1 and self.args.stream != "live/stream":
                # Jika user hanya menyetel 1 video dan memberikan flag -s, pakai itu
                s_name = sanitize_stream_name(self.args.stream)
            else:
                # Default nama stream adalah nama file tanpa ekstensi
                base_stem = Path(video_path).stem
                s_name = sanitize_stream_name(base_stem)

            # Cegah duplikasi nama stream
            final_name = s_name
            counter = 1
            while final_name in used_names:
                final_name = f"{s_name}_{counter}"
                counter += 1
            used_names.add(final_name)

            targets.append(StreamTarget(
                name=final_name,
                source_type="file",
                video_path=video_path,
                info=info
            ))

        return targets

    def cleanup(self):
        """Mematikan semua sub-proses dan menghapus file temporary."""
        if getattr(self, "_cleaned_up", False):
            return
        self._cleaned_up = True
        self.is_running = False

        print(f"\n{COLOR_YELLOW}🛑 Menghentikan semua stream dan server RTSP...{COLOR_RESET}")

        # Hentikan semua proses FFmpeg
        for target in self.targets:
            if target.proc:
                try:
                    target.proc.terminate()
                    target.proc.wait(timeout=1.5)
                except Exception:
                    try:
                        target.proc.kill()
                    except Exception:
                        pass
                target.proc = None

            # Hapus file temporary playlist jika ada
            if target.playlist_file and os.path.exists(target.playlist_file):
                try:
                    os.remove(target.playlist_file)
                except Exception:
                    pass
                target.playlist_file = None

        # Hentikan server MediaMTX
        if self.mediamtx_proc:
            try:
                self.mediamtx_proc.terminate()
                self.mediamtx_proc.wait(timeout=2)
            except Exception:
                try:
                    self.mediamtx_proc.kill()
                except Exception:
                    pass
            self.mediamtx_proc = None

        # Hapus file temporary konfigurasi MediaMTX
        if self.config_path and os.path.exists(self.config_path):
            try:
                os.remove(self.config_path)
            except Exception:
                pass
            self.config_path = None

        # Hentikan Web Dashboard Server
        if self.http_server:
            try:
                self.http_server.shutdown()
                self.http_server.server_close()
            except Exception:
                pass
            self.http_server = None

        print(f"{COLOR_GREEN}✓ Semua stream & RTSP Server berhasil dihentikan.{COLOR_RESET}")

    def build_ffmpeg_cmd(self, target: StreamTarget, rtsp_publish_url: str) -> List[str]:
        """Menyusun argumen command line FFmpeg untuk target stream tertentu."""
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning"]

        if target.source_type == "sample":
            # Mode sample: generate video on-the-fly dengan clock real-time
            resolution = self.args.resolution or "1280x720"
            fps = self.args.fps or 30
            vf = (
                f"drawtext=text='RTSP TEST PATTERN':fontcolor=yellow:fontsize=48:box=1:boxcolor=black@0.7:x=(w-text_w)/2:y=50,"
                f"drawtext=text='%{{localtime}}':fontcolor=white:fontsize=42:box=1:boxcolor=black@0.7:x=(w-text_w)/2:y=(h-text_h)/2,"
                f"drawtext=text='PTS %{{pts}} | FPS {fps}':fontcolor=cyan:fontsize=32:box=1:boxcolor=black@0.7:x=(w-text_w)/2:y=h-90"
            )

            cmd.extend([
                "-re",
                "-f", "lavfi", "-i", f"testsrc=size={resolution}:rate={fps}",
            ])

            if not self.args.no_audio:
                cmd.extend([
                    "-f", "lavfi", "-i", "sine=frequency=880:beep_factor=4:sample_rate=44100",
                ])

            cmd.extend(["-vf", vf])

            if sys.platform == "darwin" and self.args.hw:
                cmd.extend(["-c:v", "h264_videotoolbox", "-b:v", self.args.bitrate or "2000k"])
            else:
                cmd.extend(["-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency", "-pix_fmt", "yuv420p"])

            if not self.args.no_audio:
                cmd.extend(["-c:a", "aac", "-b:a", "128k"])
            else:
                cmd.append("-an")

        elif target.source_type == "playlist" and target.playlist_file:
            # Mode playlist (concat demuxer)
            cmd.extend([
                "-re",
            ])
            if self.args.loop:
                cmd.extend(["-stream_loop", "-1"])

            cmd.extend([
                "-f", "concat",
                "-safe", "0",
                "-i", target.playlist_file,
            ])

            # Transcode ke H.264 seragam agar sambungan antar file mulus
            if self.args.copy:
                cmd.extend(["-c", "copy"])
            else:
                if sys.platform == "darwin" and self.args.hw:
                    cmd.extend(["-c:v", "h264_videotoolbox"])
                    if self.args.bitrate:
                        cmd.extend(["-b:v", self.args.bitrate])
                else:
                    cmd.extend(["-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency", "-pix_fmt", "yuv420p"])
                    if self.args.bitrate:
                        cmd.extend(["-b:v", self.args.bitrate])

                if not self.args.no_audio:
                    cmd.extend(["-c:a", "aac", "-b:a", "128k"])
                else:
                    cmd.append("-an")

        else:
            # Mode file video tunggal
            video_file = target.video_path
            assert video_file is not None

            cmd.append("-re")
            if self.args.loop:
                cmd.extend(["-stream_loop", "-1"])

            cmd.extend(["-i", video_file])

            probe = target.info
            force_transcode = self.args.transcode or bool(self.args.resolution) or bool(self.args.fps) or bool(self.args.bitrate)
            is_h264 = probe and probe.get("video_codec") in ("h264", "avc1")

            if is_h264 and not force_transcode and not self.args.copy:
                cmd.extend(["-c:v", "copy"])
            elif self.args.copy:
                cmd.extend(["-c:v", "copy"])
            else:
                if sys.platform == "darwin" and self.args.hw:
                    cmd.extend(["-c:v", "h264_videotoolbox"])
                    if self.args.bitrate:
                        cmd.extend(["-b:v", self.args.bitrate])
                else:
                    cmd.extend([
                        "-c:v", "libx264",
                        "-preset", "ultrafast",
                        "-tune", "zerolatency",
                        "-pix_fmt", "yuv420p"
                    ])
                    if self.args.bitrate:
                        cmd.extend(["-b:v", self.args.bitrate])

                vf_filters = []
                if self.args.resolution:
                    w, h = self.args.resolution.split("x")
                    vf_filters.append(f"scale={w}:{h}")
                if self.args.fps:
                    vf_filters.append(f"fps={self.args.fps}")
                if vf_filters:
                    cmd.extend(["-vf", ",".join(vf_filters)])

            if self.args.no_audio:
                cmd.append("-an")
            else:
                is_aac = probe and probe.get("audio_codec") in ("aac", "mp4a")
                if is_aac and not force_transcode:
                    cmd.extend(["-c:a", "copy"])
                elif probe and probe.get("has_audio"):
                    cmd.extend(["-c:a", "aac", "-b:a", "128k"])

        # Output RTSP via TCP
        cmd.extend([
            "-f", "rtsp",
            "-rtsp_transport", "tcp",
            rtsp_publish_url
        ])

        return cmd

    def run(self):
        # 1. Validasi Dependensi
        ok, missing = check_dependencies()
        if not ok:
            print(f"{COLOR_RED}✗ Dependensi belum lengkap:{COLOR_RESET}")
            for m in missing:
                print(f"  - {m}")
            sys.exit(1)

        # 2. Parse Target Video
        self.targets = self.parse_targets()
        if not self.targets:
            print(f"{COLOR_RED}✗ Error: Anda harus memasukkan path file/folder video, atau gunakan opsi --sample.{COLOR_RESET}")
            print(f"\n{COLOR_YELLOW}Contoh Penggunaan Single / Multi Video:{COLOR_RESET}")
            print(f"  rtsp-server video.mp4")
            print(f"  rtsp-server video1.mp4 video2.mp4 video3.mp4")
            print(f"  rtsp-server cam1=depan.mp4 cam2=belakang.mp4")
            print(f"  rtsp-server ./folder_cctv/")
            print(f"  rtsp-server ./folder_cctv/ --playlist")
            print(f"  rtsp-server --sample")
            sys.exit(1)

        # 3. Validasi Port
        rtsp_port = self.args.port
        webrtc_port = self.args.webrtc_port
        hls_port = self.args.hls_port

        if is_port_in_use(rtsp_port):
            print(f"{COLOR_RED}✗ Port RTSP {rtsp_port} sedang digunakan aplikasi lain!{COLOR_RESET}")
            print(f"{COLOR_YELLOW}Gunakan opsi -p <port> untuk memilih port lain (misal: -p 8555){COLOR_RESET}")
            sys.exit(1)

        # 4. Generate MediaMTX Config
        self.config_path = create_mediamtx_config(
            rtsp_port=rtsp_port,
            webrtc_port=webrtc_port,
            hls_port=hls_port,
            enable_webrtc=not self.args.no_webrtc,
            enable_hls=not self.args.no_hls,
            auth_user=self.auth_user,
            auth_pass=self.auth_pass,
        )

        # 5. Jalankan MediaMTX Process
        mediamtx_cmd = ["mediamtx", self.config_path]
        try:
            self.mediamtx_proc = subprocess.Popen(
                mediamtx_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )
            self.monitor_thread = threading.Thread(target=self._monitor_mediamtx, daemon=True)
            self.monitor_thread.start()
        except Exception as e:
            print(f"{COLOR_RED}✗ Gagal menjalankan MediaMTX: {e}{COLOR_RESET}")
            sys.exit(1)

        # Tunggu port RTSP siap (maksimal 3 detik)
        ready = False
        for _ in range(30):
            if is_port_in_use(rtsp_port):
                ready = True
                break
            time.sleep(0.1)

        if not ready:
            print(f"{COLOR_RED}✗ MediaMTX gagal membuka port {rtsp_port}.{COLOR_RESET}")
            self.cleanup()
            sys.exit(1)

        # 6. Jalankan Semua FFmpeg Publisher
        active_count = 0
        for target in self.targets:
            if self.auth_user and self.auth_pass:
                publish_url = f"rtsp://{self.auth_user}:{self.auth_pass}@127.0.0.1:{rtsp_port}/{target.name}"
            else:
                publish_url = f"rtsp://127.0.0.1:{rtsp_port}/{target.name}"

            ffmpeg_cmd = self.build_ffmpeg_cmd(target, publish_url)
            try:
                proc = subprocess.Popen(
                    ffmpeg_cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE
                )
                target.proc = proc
                active_count += 1
            except Exception as e:
                print(f"{COLOR_RED}✗ Gagal memulai stream '{target.name}': {e}{COLOR_RESET}")

        if active_count == 0:
            print(f"{COLOR_RED}✗ Tidak ada stream FFmpeg yang berhasil dimulai.{COLOR_RESET}")
            self.cleanup()
            sys.exit(1)

        # Tunggu sebentar untuk memastikan stream tidak langsung crash
        time.sleep(1.2)
        failed_targets = [t for t in self.targets if t.proc and t.proc.poll() is not None]
        if failed_targets:
            for ft in failed_targets:
                assert ft.proc is not None
                _, err = ft.proc.communicate()
                print(f"{COLOR_RED}✗ Stream '{ft.name}' berhenti mendadak:{COLOR_RESET}\n{err.decode(errors='replace')}")
            if len(failed_targets) == len(self.targets):
                self.cleanup()
                sys.exit(1)

        self.is_running = True
        self.start_time = time.time()

        # 6.5 Jalankan Web Dashboard
        if not self.args.no_web:
            try:
                handler_cls = create_dashboard_handler(self)
                self.http_server = HTTPServer(("0.0.0.0", self.args.web_port), handler_cls)
                self.web_thread = threading.Thread(target=self.http_server.serve_forever, daemon=True)
                self.web_thread.start()
            except Exception as e:
                print(f"{COLOR_YELLOW}⚠ Gagal menjalankan Web Dashboard di port {self.args.web_port}: {e}{COLOR_RESET}")

        # 7. Tampilkan Dashboard URL & Status
        lan_ip = get_lan_ip()
        auth_prefix = f"{self.auth_user}:{self.auth_pass}@" if (self.auth_user and self.auth_pass) else ""

        print(f"\n{COLOR_GREEN}{COLOR_BOLD}✅ RTSP SERVER BERHASIL BERJALAN! ({len(self.targets)} Channel Aktif){COLOR_RESET}\n")

        name_width = max(16, min(38, max(len(t.name) for t in self.targets) + 2))
        src_width = 32
        res_width = 22
        total_width = 4 + name_width + src_width + res_width

        print(f"{COLOR_BOLD}{'NO':<4} {'CHANNEL':<{name_width}} {'SUMBER VIDEO':<{src_width}} {'RESOLUSI / INFO':<{res_width}}{COLOR_RESET}")
        print("─" * total_width)

        for i, target in enumerate(self.targets, 1):
            if target.source_type == "sample":
                src = "Synthetic Test Pattern"
                res = f"{self.args.resolution or '1280x720'} @ {self.args.fps or 30} FPS"
            elif target.source_type == "playlist":
                src = f"Playlist ({len(target.video_files or [])} Video berurutan)"
                res = f"{target.info['width']}x{target.info['height']}" if target.info else "Auto"
            else:
                src = Path(target.video_path or "").name
                if len(src) > (src_width - 2):
                    src = src[:(src_width - 5)] + "..."
                res = f"{target.info['width']}x{target.info['height']} @ {target.info['fps']}fps" if target.info else "Auto"

            disp_name = target.name
            if len(disp_name) > (name_width - 2):
                disp_name = disp_name[:(name_width - 5)] + "..."

            print(f"{i:<4} {COLOR_CYAN}{disp_name:<{name_width}}{COLOR_RESET} {src:<{src_width}} {res:<{res_width}}")

        print("─" * total_width)

        print(f"\n{COLOR_BOLD}🔗 RTSP STREAM URL (Dapat diakses bersamaan):{COLOR_RESET}")
        for target in self.targets:
            rtsp_loc = f"rtsp://{auth_prefix}127.0.0.1:{rtsp_port}/{target.name}"
            rtsp_lan = f"rtsp://{auth_prefix}{lan_ip}:{rtsp_port}/{target.name}"
            print(f"  {COLOR_BOLD}Channel [{target.name}]:{COLOR_RESET}")
            print(f"    • Localhost : {COLOR_GREEN}{rtsp_loc}{COLOR_RESET}")
            print(f"    • LAN       : {COLOR_GREEN}{rtsp_lan}{COLOR_RESET}")

        if not self.args.no_webrtc or not self.args.no_hls:
            print(f"\n{COLOR_BOLD}🌐 PREVIEW VIA BROWSER (Contoh Channel Pertama):{COLOR_RESET}")
            first_target = self.targets[0]
            if not self.args.no_webrtc:
                print(f"  {COLOR_CYAN}► WebRTC (Live): http://{lan_ip}:{webrtc_port}/{first_target.name}{COLOR_RESET} {COLOR_DIM}(Near-zero latency di Chrome/Safari){COLOR_RESET}")
            if not self.args.no_hls:
                print(f"  {COLOR_CYAN}► HLS (Player) : http://{lan_ip}:{hls_port}/{first_target.name}{COLOR_RESET}")

        if not self.args.no_web:
            web_url = f"http://{lan_ip}:{self.args.web_port}/"
            print(f"\n{COLOR_BOLD}🌐 HALAMAN WEB DASHBOARD (Katalog Semua Link Stream):{COLOR_RESET}")
            print(f"  {COLOR_GREEN}{COLOR_BOLD}► {web_url}{COLOR_RESET}  {COLOR_DIM}(Buka di browser untuk melihat semua channel, salin URL, & preview){COLOR_RESET}")

        first_stream_url = f"rtsp://{auth_prefix}127.0.0.1:{rtsp_port}/{self.targets[0].name}"
        print(f"\n{COLOR_BOLD}💡 CONTOH TESTING CEPAT:{COLOR_RESET}")
        print(f"  • {COLOR_YELLOW}FFplay CLI{COLOR_RESET}   : ffplay -rtsp_transport tcp \"{first_stream_url}\"")
        print(f"  • {COLOR_YELLOW}VLC Player{COLOR_RESET}   : Buka VLC -> File -> Open Network -> Masukkan URL di atas")
        print(f"  • {COLOR_YELLOW}OpenCV Python{COLOR_RESET}: cap = cv2.VideoCapture('{first_stream_url}')")

        print(f"\n{COLOR_MAGENTA}Tekan {COLOR_BOLD}Ctrl+C{COLOR_RESET}{COLOR_MAGENTA} untuk menghentikan semua server & stream.{COLOR_RESET}\n")

        # 8. Loop Monitoring
        try:
            while True:
                time.sleep(1.0)
                alive_count = sum(1 for t in self.targets if t.proc and t.proc.poll() is None)

                if alive_count == 0:
                    print(f"\n{COLOR_YELLOW}Semua stream telah selesai.{COLOR_RESET}")
                    break
                if self.mediamtx_proc and self.mediamtx_proc.poll() is not None:
                    print(f"\n{COLOR_RED}✗ Server MediaMTX berhenti.{COLOR_RESET}")
                    break

                with self.sessions_lock:
                    active_viewers = sum(1 for s in self.sessions.values() if not s.get("is_publisher") and s.get("path"))

                uptime = int(time.time() - self.start_time)
                mins, secs = divmod(uptime, 60)
                hrs, mins = divmod(mins, 60)
                viewer_str = f"| {COLOR_GREEN}👥 {active_viewers} Client Online{COLOR_RESET}" if active_viewers > 0 else f"| {COLOR_DIM}👥 0 Client{COLOR_RESET}"
                sys.stdout.write(f"\r  {COLOR_CYAN}● LIVE{COLOR_RESET} Uptime: {hrs:02d}:{mins:02d}:{secs:02d} | {alive_count}/{len(self.targets)} Channel Aktif {viewer_str}   ")
                sys.stdout.flush()

        except KeyboardInterrupt:
            pass
        finally:
            self.cleanup()


def show_video_info(video_path: str):
    """Menampilkan detail dan kompatibilitas RTSP dari file video."""
    print(f"\n{COLOR_BOLD}🔍 Memeriksa file video: {video_path}{COLOR_RESET}\n")
    info = probe_video(video_path)
    if not info or not info.get("has_video"):
        print(f"{COLOR_RED}✗ Gagal membaca informasi video atau file tidak valid.{COLOR_RESET}")
        return

    size_mb = round(info["size_bytes"] / (1024 * 1024), 2)
    dur_m, dur_s = divmod(int(info["duration"]), 60)

    print(f"  • Ukuran File   : {size_mb} MB")
    print(f"  • Durasi        : {dur_m}m {dur_s}s ({info['duration']:.2f} detik)")
    print(f"  • Format        : {info['format_name']}")
    print(f"  • Resolusi      : {info['width']}x{info['height']}")
    print(f"  • Framerate     : {info['fps']} FPS")
    print(f"  • Video Codec   : {COLOR_GREEN if info['video_codec'] == 'h264' else COLOR_YELLOW}{info['video_codec']}{COLOR_RESET}")
    print(f"  • Audio Codec   : {info['audio_codec'] or 'Tidak ada audio'}")

    print(f"\n{COLOR_BOLD}📊 Analisis Kompatibilitas RTSP:{COLOR_RESET}")
    if info["video_codec"] == "h264":
        print(f"  {COLOR_GREEN}✓ Video sudah menggunakan codec H.264 (Standard RTSP).{COLOR_RESET}")
        print(f"  {COLOR_GREEN}✓ Dapat di-stream dengan mode 'stream-copy' (0% beban CPU, performa maksimal!){COLOR_RESET}")
    else:
        print(f"  {COLOR_YELLOW}⚠ Video menggunakan codec '{info['video_codec']}' (Bukan H.264).{COLOR_RESET}")
        print(f"  {COLOR_CYAN}ℹ Tool ini akan otomatis mentranscode ke H.264 secara on-the-fly agar kompatibel dengan RTSP client.{COLOR_RESET}")
    print()


def main():
    parser = argparse.ArgumentParser(
        description="Tool CLI RTSP Server untuk streaming satu atau banyak video lokal / test pattern.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Contoh Penggunaan:
  1. Stream 1 video lokal (default loop):
     rtsp-server video.mp4

  2. Stream BANYAK video sekaligus (Multi-Channel / Multi-CCTV):
     rtsp-server video1.mp4 video2.mp4 video3.mp4
     rtsp-server cam1=depan.mp4 cam2=belakang.mp4

  3. Stream SEMUA video dalam satu folder (otomatis buat channel per video):
     rtsp-server ./folder_cctv/

  4. Putar BANYAK video secara berurutan dalam 1 stream (Playlist loop):
     rtsp-server video1.mp4 video2.mp4 video3.mp4 --playlist
     rtsp-server ./folder_cctv/ --playlist

  5. Stream test pattern langsung (tanpa file video):
     rtsp-server --sample

  6. Stream dengan autentikasi (user:pass):
     rtsp-server video.mp4 --auth admin:rahasia123
        """
    )

    parser.add_argument("videos", nargs="*", help="Satu atau lebih path video / folder video (misal: vid1.mp4 vid2.mp4 atau cam1=vid1.mp4)")
    parser.add_argument("-p", "--port", type=int, default=8554, help="Port RTSP server (default: 8554)")
    parser.add_argument("-s", "--stream", default="live/stream", help="Nama stream path (default: live/stream, atau nama file)")
    parser.add_argument("--playlist", action="store_true", help="Putar banyak video secara berurutan dalam satu stream yang sama")
    parser.add_argument("--loop", action=argparse.BooleanOptionalAction, default=True, help="Looping video terus menerus (default: true)")
    parser.add_argument("-c", "--copy", action="store_true", help="Paksa gunakan stream copy (-c:v copy) tanpa re-encoding")
    parser.add_argument("-t", "--transcode", action="store_true", help="Paksa transcode ulang ke H.264")
    parser.add_argument("--hw", action=argparse.BooleanOptionalAction, default=True, help="Gunakan hardware acceleration Apple Silicon (default: true)")
    parser.add_argument("--resolution", help="Ubah resolusi video, misal: 1920x1080, 1280x720, 640x360")
    parser.add_argument("--bitrate", help="Bitrate video target jika transcode, misal: 2000k")
    parser.add_argument("--fps", type=int, help="Framerate target jika transcode (misal: 30 atau 60)")
    parser.add_argument("--no-audio", action="store_true", help="Hapus/matikan stream audio")
    parser.add_argument("--auth", help="Aktifkan autentikasi username:password (misal: admin:pass123)")

    # WebRTC & HLS preview options
    parser.add_argument("--webrtc-port", type=int, default=8889, help="Port WebRTC browser preview (default: 8889)")
    parser.add_argument("--hls-port", type=int, default=8888, help="Port HLS browser preview (default: 8888)")
    parser.add_argument("--no-webrtc", action="store_true", help="Nonaktifkan WebRTC preview")
    parser.add_argument("--no-hls", action="store_true", help="Nonaktifkan HLS preview")

    # Web Dashboard options
    default_web_port = int(os.environ.get("RTSP_WEB_PORT") or os.environ.get("WEB_PORT") or 8080)
    parser.add_argument("-w", "--web-port", type=int, default=default_web_port, help=f"Port Web Dashboard (default: {default_web_port})")
    parser.add_argument("--no-web", action="store_true", help="Nonaktifkan Web Dashboard")

    # Mode khusus
    parser.add_argument("--sample", action="store_true", help="Stream synthetic test card real-time (tanpa perlu file video)")
    parser.add_argument("--info", action="store_true", help="Tampilkan info teknis video & kompatibilitas RTSP lalu keluar")
    parser.add_argument("--generate-sample", help="Buat file video sample MP4 ke path yang ditentukan lalu keluar")

    args = parser.parse_args()

    print_banner()

    # Opsi membuat sample file
    if args.generate_sample:
        ok = generate_sample_file(args.generate_sample, duration=15, resolution=args.resolution or "1280x720")
        sys.exit(0 if ok else 1)

    # Opsi info video
    if args.info:
        if not args.videos:
            print(f"{COLOR_RED}✗ Harap masukkan path file video untuk dicek.{COLOR_RESET}")
            sys.exit(1)
        show_video_info(args.videos[0])
        sys.exit(0)

    # Menjalankan RTSP Server
    server = RTSPServerCLI(args)

    # Register exit handler
    def handle_signal(*_):
        server.cleanup()
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)
    atexit.register(server.cleanup)

    server.run()


if __name__ == "__main__":
    main()
