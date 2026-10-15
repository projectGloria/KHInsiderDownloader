# 🎵 KHInsider Downloader Pro

A modern, high-speed desktop downloader and CLI tool for [KHInsider](https://downloads.khinsider.com) (Video Game Music).

Download complete video game soundtracks, **lossless FLAC** audio, **full-resolution & lossless artwork (covers, booklet scans, disc art)**, and album metadata with ease.

---

## ✨ Features

### 🎛️ Pick exactly what you want, per format
Every audio format the album offers gets its own selectable section — FLAC, MP3, OGG, M4A and so on — each showing its track count and total download size before you commit.

- Select **FLAC only**, **MP3 only**, or any combination.
- One-click presets: *All*, *Lossless only*, *MP3 only*, *Artwork only*.
- **Artwork & Scans** and **Extra Files** are their own sections too, so you can grab covers without any audio.

### 📁 Every format in its own subfolder
Each selected format is written to a dedicated subfolder, so mixed-format albums never interleave:

```
Mafia II Official Orchestral Score/
├── FLAC/                 ← lossless audio
├── MP3/                  ← lossy audio
├── Artwork/              ← covers, booklet scans, disc art
├── khinsider.info.txt    ← extras stay at the album root
└── Mafia II ... [FLAC].m3u
```

Turn this off with `--no-organize` (GUI: *Separate subfolder per format*) to write everything flat.

### ⚡ Performance first
- **Parallel downloads** — files transfer concurrently (default 6, tunable 1–16). Measured ~4.5 MB/s vs ~2.8 MB/s sequential on the same album.
- **Parallel link resolution** — every track's CDN URL is resolved concurrently before the queue starts.
- **Connection pooling & keep-alive** — one TLS handshake is reused across the whole session, with automatic retry on 429/5xx.
- **One request per file** — the old HEAD-then-GET round trip is gone.
- **Instant re-runs** — files already downloaded are skipped with *zero* network requests.
- **Resumable** — an interrupted transfer picks up where it left off via HTTP Range instead of restarting.
- Uses `lxml` for faster HTML parsing when installed (falls back to the stdlib parser automatically).

### 🖼️ Lossless artwork & complete scans
Fetches all available artwork — front covers, back covers, booklet scans (all pages), disc art, obi strips, digipacks, tray art — always at full original resolution, bypassing low-res thumbnails.

### 🏷️ Tagging & playlists
Embeds the front cover into audio tags (ID3 for MP3, picture block for FLAC) and generates a `.m3u` playlist per format.

### 🖥️ Modern interface
Dark, three-zone workspace: command bar, a left inspector (album card, content sections, output options) and a work area with the tracklist plus a **live monitor showing every parallel transfer**, aggregate speed, and ETA.

---

## 🚀 Getting Started

### 1. Using the Pre-Built Executable

Double-click `dist/KHInsiderDownloader.exe`. The GUI opens immediately — no Python needed.

You can also drive it from a terminal:
```powershell
.\dist\KHInsiderDownloader.exe "https://downloads.khinsider.com/game-soundtracks/album/mafia-ii-official-orchestral-score"
```

### 2. Running from Source

```bash
pip install -r requirements.txt
```

Launch the GUI:
```bash
python khinsider_downloader.py
```

---

## 💻 Command Line Usage

```bash
# Everything the album offers (all formats + artwork + extras)
python khinsider_downloader.py mafia-ii-official-orchestral-score

# Only lossless FLAC (+ artwork), into FLAC/
python khinsider_downloader.py <album_url> --formats flac

# FLAC and MP3 side by side, each in its own subfolder
python khinsider_downloader.py <album_url> --formats flac,mp3

# Presets instead of an explicit list
python khinsider_downloader.py <album_url> --format lossless
python khinsider_downloader.py <album_url> --format art_only

# 12 parallel connections into a custom folder
python khinsider_downloader.py <album_url> --formats flac -w 12 -o "C:/Music/OSTs"
```

A bare album slug works anywhere a full URL does.

| Option | Description |
| --- | --- |
| `--formats LIST` | Explicit comma-separated formats, e.g. `flac` or `flac,mp3`. Overrides `--format`. |
| `-f, --format` | Preset: `all`, `lossless`, `mp3`, `art_only` (default: `all`). |
| `-o, --output` | Output directory (default: `./downloads`). |
| `-w, --workers` | Parallel download connections (default: 6). |
| `--no-artwork` | Skip artwork and scans. |
| `--no-extras` | Skip extra files (info txt, cue sheets). |
| `--no-organize` | Write everything flat instead of per-format subfolders. |
| `--no-tag` | Do not embed cover art into audio tags. |
| `--no-playlist` | Do not generate `.m3u` playlists. |
| `--gui` | Force the graphical interface. |

---

## 🛠️ Building the Executable

```bash
python -m PyInstaller KHInsiderDownloader.spec --clean
```

The output is written to `dist/KHInsiderDownloader.exe`.
