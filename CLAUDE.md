# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
pip install -r requirements.txt      # requests, beautifulsoup4, lxml, PyQt6, mutagen, Pillow

python khinsider_downloader.py                       # launches GUI (no args)
python khinsider_downloader.py <album_url_or_slug>   # CLI mode
python khinsider_gui.py                              # GUI directly
```

CLI flags: `--formats flac,mp3` (explicit list, overrides `--format`), `-f/--format {all,lossless,mp3,art_only}`,
`-o/--output`, `-w/--workers` (default 6), `--no-artwork`, `--no-extras`, `--no-organize`, `--no-tag`,
`--no-playlist`, `--gui`.

Rebuild the standalone exe (output lands in `dist/`):

```bash
python -m PyInstaller KHInsiderDownloader.spec --clean
```

There is no test suite, linter, or CI. Verify against a real album — `mafia-ii-official-orchestral-score`
is a good target (48 tracks, MP3 + FLAC, 1 artwork, 1 extra). Because completed files are skipped with no
network request, a second run of the same command is a fast way to check queue/path logic in isolation.

## Architecture

Three modules, strictly layered — `khinsider_core.py` must stay free of PyQt imports so the
CLI path works headless.

### `khinsider_core.py` — scraping, download, and tagging

**Scraping.** `KHInsiderScraper.parse_album(url)` → `AlbumInfo` from one page fetch: metadata (h2 title,
Platforms/Year `<p>` text), artwork from `div.albumImage` plus `vgmtreasurechest`-hosted image links,
extra files, and the `table#songlist` tracklist. Available formats come from the `tr#songlist_header`
column headers (MP3/FLAC/M4A/OGG/WAV/OPUS).

> **Do not map header column indices onto data rows.** The header omits a label for the duration column,
> so data cells sit one index further right than their header. Per-track sizes are instead matched by
> cell *shape* (`_SIZE_CELL_RE`) and zipped onto `formats_available` in order. Track/disc numbers are
> still inferred positionally from digit cells preceding the first `M:SS` duration cell.

An unknown slug returns a 200 "Ooops!" page rather than a 404; `parse_album` detects this and raises
`AlbumNotFoundError` instead of yielding a plausible-looking empty album.

**Two-fetch model.** Track rows only carry a *page* URL. The actual CDN URL needs a second fetch per
track: `resolve_track_downloads()`, parallelised by `resolve_all_tracks_concurrently()` with a
`should_stop` callback. Both entry points must run this before building a queue.

**Format selection.** `DownloadOptions.resolve_formats(available)` is the single source of truth.
`selected_formats` (an explicit list; `[]` means artwork/extras only) overrides the legacy
`format_preference` preset when not None. The GUI always passes an explicit list; the CLI passes one
only for `--formats`.

**Queue and paths.** `build_download_queue(album, selected_tracks=None)` flattens artwork + extras +
per-format track URLs into `DownloadTask`s with final target paths already decided:
`<output_dir>/<sanitized title>/`, with `Artwork/` and a per-format subfolder (`FLAC/`, `MP3/`) when
`organize_by_format` is on. Every selected format always gets its own subfolder — there is deliberately
no "only split when >1 format" condition. Audio tasks carry `expected_bytes` (parsed from the advertised
size) so a session ETA exists before any Content-Length arrives.

**Downloading.** `download_queue()` is the normal entry point: it runs the queue through a
`ThreadPoolExecutor`, reports through `on_task_start/progress/done` plus a throttled `on_stats`, and
returns `(completed_tasks, DownloadStats)`. `download_file()` handles one task and is where the
performance-sensitive invariants live:

- A file at its **final path is complete by construction** (transfers stream to `<target>.part` and
  atomically `os.replace` on success), so an existing target is skipped with *no request at all*. This
  is why the skip check cannot compare sizes: embedding cover art grows the file past the remote size.
- A leftover `.part` is **resumed** with a `Range` request; cancellation deliberately keeps it.
- Only one request per file — no HEAD probe.
- `build_session()` supplies pooling, keep-alive and retry on 429/5xx; the pool is sized from the
  worker count.

**Tagging.** Cover embedding is deferred to a single `embed_covers()` pass after all transfers finish —
it guarantees the artwork has landed before any audio is tagged, and `_get_cover()` caches the cover
bytes (and the "none found" answer) per directory rather than re-reading per track.

### `khinsider_gui.py` — PyQt6 interface

`KHInsiderMainWindow` plus `FetchAlbumWorker` and `DownloadWorker` (both `QThread`). `DownloadWorker`
resolves links, then delegates to `downloader.download_queue`. Its callbacks fire on **pool threads** and
must only emit Qt signals — Qt queues those to the UI thread. Never touch widgets from them.

- `ContentCard` is the per-format section widget; `_build_format_cards()` creates one per entry in
  `album.formats_available`, plus the fixed Artwork and Extras cards. `selected_formats()` feeds
  `DownloadOptions.selected_formats` directly.
- `TransferRow` instances form a bounded pool of monitor slots, one per parallel connection, handed out
  in `on_start` and returned in `on_done`.
- Design tokens live in the `C` dict and are interpolated into `MODERN_STYLE` once at import time (Qt
  stylesheets have no variables, so the sheet uses doubled braces for its literal CSS braces).
- Styling `QCheckBox::indicator` removes the native tick, so `make_checkmark_icon()` draws one at
  runtime and its path is appended to the sheet in `__init__`.

### `khinsider_downloader.py` — entry point

No args → GUI; args → CLI. On Windows it calls `AttachConsole(-1)` so the `--noconsole` exe can still
print when invoked from a terminal, and reconfigures stdout/stderr to UTF-8 (output contains emoji and
`·`). This is the file PyInstaller builds.

## Notes

- Scraping is heuristic and tied to khinsider's current HTML. When a site change breaks parsing, fix
  `parse_album` / `resolve_track_downloads` and keep the graceful degradation (an album with no
  `table#songlist` but real artwork still returns a track-less `AlbumInfo`).
- `icon.ico` is resolved relative to `__file__` in `khinsider_gui.py` and shipped via the spec's
  `datas`; keep both in sync if the icon moves.
- `lxml` is optional at import (`HTML_PARSER` falls back to `html.parser`); it is in requirements and
  the spec's `hiddenimports` because track resolution parses one page per track.
- `downloads/`, `build/`, `dist/`, `__pycache__/` are generated output, not source.
