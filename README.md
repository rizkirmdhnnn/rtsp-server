# 📹 RTSP Server CLI

Tool CLI untuk mengubah satu atau banyak video lokal (MP4, MKV, AVI, MOV, dll) menjadi RTSP stream server secara lokal atau melalui jaringan LAN. Dibuat khusus untuk pengujian (testing) aplikasi video streaming, CCTV player, NVR, OpenCV, mobile app (Android/iOS), maupun web.

Didukung oleh:
- **MediaMTX** (industrial-grade RTSP/WebRTC server)
- **FFmpeg** (video streamer & hardware-accelerated transcoder)

---

## 🚀 Fitur Utama

- **Single & Multi-Video Support**:
  - **Multi-Channel / Multi-CCTV**: Stream banyak video secara bersamaan di port yang sama (misal `cam1`, `cam2`, `cam3`).
  - **Folder Scanning**: Cukup arahkan ke folder berisi video, semua video otomatis menjadi channel RTSP masing-masing!
  - **Playlist Mode (`--playlist`)**: Menggabungkan banyak video berurutan dalam satu channel stream yang berputar terus.
- **Loop Otomatis**: Video otomatis berulang terus menerus tanpa terputus (sangat ideal untuk testing aplikasi).
- **Direct Stream Copy**: Jika video sudah menggunakan H.264/AAC, stream langsung disalurkan tanpa beban CPU (0% CPU usage).
- **Hardware Acceleration**: Otomatis menggunakan Apple Silicon VideoToolbox (`h264_videotoolbox`) jika perlu transcode.
- **Deteksi Otomatis IP LAN**: Menampilkan alamat URL RTSP localhost dan IP jaringan Wi-Fi/LAN (misal `rtsp://10.10.x.x:8554/...`) agar mudah dites langsung dari smartphone atau perangkat lain di jaringan yang sama.
- **WebRTC & HLS Browser Preview**: Dilengkapi link player browser bawaan tanpa perlu install VLC.
- **Mode Test Pattern (`--sample`)**: Bisa langsung live streaming gambar test pattern dengan timer jam real-time tanpa perlu file video apa pun!
- **Dukungan Autentikasi**: Opsi username & password untuk menguji aplikasi dengan proteksi RTSP auth.
- **Clean Shutdown**: Menekan `Ctrl+C` langsung menghentikan semua proses tanpa meninggalkan port menggantung.

---

## 📦 Prasyarat & Pemasangan

Tool ini memerlukan `ffmpeg` dan `mediamtx` (keduanya sudah terpasang di sistem macOS Anda via Homebrew):

```bash
brew install ffmpeg mediamtx
```

Perintah `rtsp-server` sudah otomatis terpasang secara global di `~/.local/bin/rtsp-server`. Anda dapat menjalankannya langsung dari terminal mana pun.

---

## 💻 Cara Penggunaan

### 1. Multi-Channel: Stream Banyak Video Sekaligus (Simulasi Multi-Kamera)
Cocok untuk menguji aplikasi NVR, dashboard multi-kamera, atau computer vision:

```bash
# Otomatis menggunakan nama file sebagai nama stream:
rtsp-server video1.mp4 video2.mp4 video3.mp4

# Menggunakan alias custom (nama channel bebas):
rtsp-server cam1=depan.mp4 cam2=belakang.mp4 parkir=lobby.mp4
```

Hasilnya, setiap video akan memiliki URL RTSP independen yang berjalan bersamaan:
- `rtsp://127.0.0.1:8554/cam1`
- `rtsp://127.0.0.1:8554/cam2`
- `rtsp://127.0.0.1:8554/parkir`

### 2. Folder Scanning (Paling Praktis)
Cukup masukkan path folder tempat Anda menyimpan kumpulan video:

```bash
rtsp-server /path/ke/folder_cctv/
```
Tool akan otomatis memindai seluruh file video (`.mp4`, `.mkv`, `.avi`, `.mov`, dll) di dalam folder tersebut dan menyiarkan semuanya sekaligus sebagai channel terpisah.

### 3. Playlist Mode (Banyak Video Diputar Berurutan di 1 Channel)
Jika Anda ingin video diputar berurutan (video A -> video B -> video C -> loop lagi dari awal) dalam 1 stream RTSP:

```bash
rtsp-server video1.mp4 video2.mp4 video3.mp4 --playlist
# Atau gunakan folder:
rtsp-server /path/ke/folder/ --playlist
```
URL RTSP: `rtsp://127.0.0.1:8554/live/stream`

### 4. Single Video
```bash
rtsp-server video.mp4
# Dengan custom nama stream:
rtsp-server video.mp4 -s kamera/utama
```

### 5. Mode Test Pattern (Tanpa File Video)
Jika belum menyiapkan video dan ingin langsung menguji aplikasi Anda:

```bash
rtsp-server --sample
```

### 6. Proteksi Autentikasi (User & Password)
```bash
rtsp-server cam1=video1.mp4 cam2=video2.mp4 --auth admin:password123
```
URL RTSP:
- `rtsp://admin:password123@127.0.0.1:8554/cam1`
- `rtsp://admin:password123@127.0.0.1:8554/cam2`

---

## 🧪 Cara Menguji RTSP Stream ke Aplikasi Anda

### Opsi A: Browser Web (Paling Cepat)
Buka browser (Chrome, Safari) dan akses alamat WebRTC:
```
http://localhost:8889/cam1
```

### Opsi B: ffplay (Command Line)
```bash
ffplay -rtsp_transport tcp "rtsp://127.0.0.1:8554/cam1"
```

### Opsi C: VLC Media Player
1. Buka VLC Player.
2. Tekan `Cmd + N` (Open Network).
3. Masukkan URL: `rtsp://127.0.0.1:8554/cam1`.
4. Klik **Open**.

### Opsi D: Python (OpenCV)
```python
import cv2

rtsp_url = "rtsp://127.0.0.1:8554/cam1"
cap = cv2.VideoCapture(rtsp_url)

while cap.isOpened():
    ret, frame = cap.read()
    if not ret:
        break
    cv2.imshow("RTSP Camera 1", frame)
    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

cap.release()
cv2.destroyAllWindows()
```

### Opsi E: Aplikasi Mobile (Android / iOS)
Gunakan URL **Jaringan LAN**:
```text
rtsp://<IP_LAN_MAC_ANDA>:8554/cam1
```
*(Pastikan smartphone dan Mac Anda berada dalam jaringan Wi-Fi yang sama).*

---

## ⚙️ Ringkasan Parameter / Opsi

| Opsi | Default | Deskripsi |
|---|---|---|
| `videos` | - | Satu atau lebih path file video, custom alias (`cam1=vid.mp4`), atau folder |
| `--playlist` | `False` | Putar banyak video secara berurutan dalam 1 stream tunggal |
| `-p, --port` | `8554` | Port RTSP server |
| `-s, --stream` | `live/stream` | Path/nama stream default |
| `--loop / --no-loop` | `True` | Video di-loop berulang tanpa henti |
| `-c, --copy` | auto | Paksa passthrough codec tanpa transcode |
| `-t, --transcode` | auto | Paksa transcode ulang ke H.264 |
| `--hw / --no-hw` | `True` | Hardware acceleration Apple Silicon |
| `--resolution` | - | Ubah resolusi (misal `1920x1080`, `1280x720`) |
| `--bitrate` | - | Bitrate target saat transcode (misal `2000k`) |
| `--fps` | - | Framerate target (misal `30` atau `60`) |
| `--no-audio` | `False` | Matikan/buang track audio |
| `--auth` | - | Autentikasi `username:password` |
| `--sample` | `False` | Jalankan live test pattern tanpa video |
| `--info` | `False` | Tampilkan analisis teknis video |

---

## 🛑 Berhenti

Tekan `Ctrl + C` pada terminal tempat `rtsp-server` dijalankan. Semua proses stream FFmpeg dan server MediaMTX akan dihentikan secara bersih.

---

## 🐳 Deployment Menggunakan Docker

RTSP Server ini sudah sepenuhnya didukung untuk dijalankan dan dideploy menggunakan Docker dan Docker Compose.

### 1. Menjalankan Cepat dengan Docker Compose (Direkomendasikan)

Pastikan file video Anda sudah diletakkan di dalam folder `./videos/`, lalu jalankan:

```bash
docker compose up -d
```

Melihat status & log stream:
```bash
docker compose logs -f
```

Menghentikan server:
```bash
docker compose down
```

### 2. Menjalankan dengan `docker run`

**A. Jalankan dengan Folder Video Lokal:**
```bash
docker run -d --name rtsp-server \
  -p 8554:8554/tcp -p 8554:8554/udp \
  -p 8888:8888/tcp -p 8889:8889/tcp \
  -v $(pwd)/videos:/videos:ro \
  rtsp-server:latest
```

**B. Jalankan Mode Test Pattern (Tanpa File Video):**
```bash
docker run -d --name rtsp-server \
  -p 8554:8554/tcp -p 8554:8554/udp \
  -p 8888:8888/tcp -p 8889:8889/tcp \
  rtsp-server:latest --sample
```
*(Catatan: Jika folder `/videos` kosong, container otomatis fallback ke mode `--sample`)*

**C. Dengan Custom Port & Autentikasi:**
```bash
docker run -d --name rtsp-server \
  -p 8554:8554/tcp -p 8554:8554/udp \
  -v $(pwd)/videos:/videos:ro \
  -e RTSP_AUTH=admin:secret123 \
  -e RTSP_HOST_IP=192.168.1.100 \
  rtsp-server:latest
```

### 3. Variabel Lingkungan (Environment Variables)

| Variabel | Default | Deskripsi |
|---|---|---|
| `RTSP_HOST_IP` | - | IP Publik/LAN host server untuk WebRTC ICE & tampilan URL di log |
| `RTSP_PORT` | `8554` | Port RTSP server |
| `RTSP_AUTH` | - | Format `username:password` untuk proteksi RTSP |
| `RTSP_STREAM` | `live/stream` | Nama stream default |
| `RTSP_PLAYLIST` | `false` | Putar berurutan dalam 1 channel playlist (`true`/`false`) |
| `RTSP_SAMPLE` | `false` | Paksa jalankan mode test pattern tanpa video (`true`/`false`) |
| `RTSP_NO_AUDIO` | `false` | Matikan stream audio (`true`/`false`) |
| `RTSP_RESOLUTION`| - | Ubah resolusi video (misal `1280x720`) |
| `RTSP_FPS` | - | Ubah framerate target (misal `30`) |
| `RTSP_BITRATE` | - | Target bitrate video (misal `2000k`) |

---

## 🚀 Mempublish Docker Image ke Registry

### 1. Build Lokal Image Multi-Arsitektur

Untuk membuat image Docker yang kompatibel dengan server Linux x86_64 (Intel/AMD) maupun ARM64 (Apple Silicon / Raspberry Pi / AWS Graviton):

```bash
docker buildx build --platform linux/amd64,linux/arm64 -t username/rtsp-server:latest --push .
```

Atau build untuk arsitektur mesin lokal saja:
```bash
docker build -t username/rtsp-server:latest .
```

### 2. Publish ke Docker Hub

1. Login ke Docker Hub:
   ```bash
   docker login
   ```
2. Tag image:
   ```bash
   docker tag rtsp-server:latest username/rtsp-server:latest
   docker tag rtsp-server:latest username/rtsp-server:v1.0.0
   ```
3. Push ke Docker Hub:
   ```bash
   docker push username/rtsp-server:latest
   docker push username/rtsp-server:v1.0.0
   ```

### 3. Publish ke GitHub Container Registry (GHCR)

1. Login menggunakan GitHub Personal Access Token (PAT dengan izin `write:packages`):
   ```bash
   echo $CR_PAT | docker login ghcr.io -u GITHUB_USERNAME --password-stdin
   ```
2. Tag & Push:
   ```bash
   docker tag rtsp-server:latest ghcr.io/GITHUB_USERNAME/rtsp-server:latest
   docker push ghcr.io/GITHUB_USERNAME/rtsp-server:latest
   ```

### 4. Otomatisasi via GitHub Actions CI/CD

File workflow sudah tersedia di `.github/workflows/docker-publish.yml`. Setiap kali Anda melakukan `git push` ke branch `main`/`master` atau membuat git tag versi (misal `v1.0.0`), GitHub Actions akan secara otomatis:
- Membangun image multi-arch (`linux/amd64` dan `linux/arm64`).
- Mempublish image langsung ke GitHub Container Registry (`ghcr.io`).
- (Opsional) Mempublish ke Docker Hub jika secret `DOCKERHUB_USERNAME` dan `DOCKERHUB_TOKEN` ditambahkan di pengaturan repository GitHub.

