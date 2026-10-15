"""
KHInsider Downloader Pro
Complete Soundtrack & Lossless Artwork Downloader.

Can be run in two modes:
1. GUI Mode (Default when launched without arguments or by clicking executable)
2. CLI Mode:
   python khinsider_downloader.py <album_url_or_slug> [options]
"""

import argparse
import os
import sys
import time
from typing import Optional

# Ensure console output when run from terminal with arguments
if sys.platform == "win32":
    if len(sys.argv) > 1 and "--gui" not in sys.argv:
        try:
            import ctypes
            ctypes.windll.kernel32.AttachConsole(-1)
        except Exception:
            pass
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from khinsider_core import (
    AlbumInfo,
    DownloadOptions,
    DownloadStats,
    DownloadTask,
    KHInsiderDownloader,
    KHInsiderScraper,
    format_bytes,
    format_duration,
    format_speed,
    normalize_album_url,
    parse_size_to_bytes,
    sanitize_filename,
)


def run_cli(args):
    """Executes download in command-line mode."""
    url = normalize_album_url(args.url)
    print("=" * 60)
    print("🎵 KHInsider Downloader Pro (CLI)")
    print(f"Target: {url}")
    print("=" * 60)

    scraper = KHInsiderScraper()
    print("Fetching album metadata and tracklist...")
    try:
        album = scraper.parse_album(url)
    except Exception as e:
        print(f"Error fetching album: {e}")
        sys.exit(1)

    print(f"\nAlbum Title:      {album.title}")
    if album.platforms:
        print(f"Platforms:        {', '.join(album.platforms)}")
    if album.year:
        print(f"Release Year:     {album.year}")
    print(f"Total Tracks:     {len(album.tracks)}")
    print(f"Available Audio:  {', '.join(f.upper() for f in album.formats_available) or 'Unknown'}")
    print(f"Artworks & Scans: {len(album.artworks)} (Lossless: {sum(1 for a in album.artworks if a.is_lossless)})")
    if album.extra_files:
        print(f"Extra files:      {len(album.extra_files)} (e.g. info, booklets)")
    print("-" * 60)

    # An explicit --formats list wins over the --format preset.
    selected_formats = None
    if args.formats:
        selected_formats = [f.strip().lower() for f in args.formats.split(",") if f.strip()]
        unknown = [f for f in selected_formats if f not in album.formats_available]
        if unknown:
            print(f"\nWarning: not available for this album: {', '.join(unknown)}")

    options = DownloadOptions(
        output_dir=args.output,
        format_preference=args.format.lower(),
        selected_formats=selected_formats,
        download_artwork=not args.no_artwork,
        download_extras=not args.no_extras,
        organize_by_format=args.organize,
        embed_cover_art=not args.no_tag,
        generate_m3u=not args.no_playlist,
        max_concurrent_downloads=args.workers,
        max_resolve_workers=max(8, args.workers * 2),
    )

    wanted = options.resolve_formats(album.formats_available)
    print(f"Selected formats: {', '.join(f.upper() for f in wanted) or 'none (artwork/extras only)'}")
    print("-" * 60)

    if wanted and album.tracks:
        print(f"Resolving download links for {len(album.tracks)} tracks...")
        start_t = time.time()

        def on_resolve_progress(done, total):
            sys.stdout.write(f"\r  [{done}/{total}] {int(done / total * 100)}%")
            sys.stdout.flush()

        scraper.resolve_all_tracks_concurrently(
            album.tracks,
            max_workers=options.max_resolve_workers,
            progress_callback=on_resolve_progress,
        )
        print(f"\n  Resolved in {time.time() - start_t:.1f}s.")

    downloader = KHInsiderDownloader(options, scraper=scraper)
    queue = downloader.build_download_queue(album)

    if not queue:
        print("No files to download for current options.")
        return

    n_audio = sum(1 for t in queue if t.file_type == "audio")
    n_art = sum(1 for t in queue if t.file_type == "artwork")
    n_extra = sum(1 for t in queue if t.file_type == "extra")
    print(f"\nQueue: {len(queue)} files ({n_audio} audio, {n_art} artwork, {n_extra} extras)")
    print(f"Downloading with {options.max_concurrent_downloads} parallel connections...\n")

    # Both callbacks share one terminal line, so each wipes the previous status
    # line before writing rather than leaving its tail behind.
    CLEAR = "\r" + " " * 78 + "\r"

    def on_task_done(task: DownloadTask, ok: bool, error: str):
        name = os.path.basename(task.target_path)
        if ok:
            mark = "skip" if task.metadata.get("skipped") else " ok "
        else:
            mark = "FAIL"
        line = f"{CLEAR}[{mark}] {name}"
        if not ok and error:
            line += f"  ({error})"
        sys.stdout.write(line + "\n")
        sys.stdout.flush()

    def on_stats(stats: DownloadStats):
        done = stats.completed_files + stats.failed_files
        sys.stdout.write(
            f"\r  {done}/{stats.total_files} files · {format_bytes(stats.bytes_downloaded)}"
            f" · {format_speed(stats.speed)} · ETA {format_duration(stats.eta)}"
        )
        sys.stdout.flush()

    completed, stats = downloader.download_queue(
        queue,
        on_task_done=on_task_done,
        on_stats=on_stats,
    )

    downloader.generate_playlists(album, completed)

    dest = os.path.abspath(os.path.join(options.output_dir, sanitize_filename(album.title)))
    print("\n\n" + "=" * 60)
    print(f"Done: {stats.completed_files}/{stats.total_files} files, "
          f"{format_bytes(stats.bytes_downloaded)} in {format_duration(stats.elapsed)} "
          f"({format_speed(stats.bytes_downloaded / stats.elapsed)} avg)")
    if stats.failed_files:
        print(f"Failed: {stats.failed_files} file(s)")
    print(f"Saved to: {dest}")
    print("=" * 60)


def main():
    if len(sys.argv) == 1:
        # Launch GUI
        try:
            from khinsider_gui import main as gui_main
            gui_main()
            return
        except ImportError as e:
            print(f"Could not launch GUI: {e}")
            print("Falling back to CLI help:")

    parser = argparse.ArgumentParser(
        description="KHInsider Downloader Pro - Download sound tracks, lossless FLAC, booklets, and lossless artwork"
    )
    parser.add_argument("url", nargs="?", help="KHInsider album URL or slug")
    parser.add_argument("--gui", action="store_true", help="Force launch graphical interface")
    parser.add_argument("-o", "--output", default="downloads", help="Output directory (default: ./downloads)")
    parser.add_argument("-f", "--format", choices=["all", "lossless", "mp3", "art_only"], default="all",
                        help="Audio format preset: 'all' (every format), 'lossless' (FLAC/WAV), 'mp3', "
                             "or 'art_only' (default: all)")
    parser.add_argument("--formats", metavar="LIST",
                        help="Explicit comma-separated formats to download, e.g. --formats flac "
                             "or --formats flac,mp3. Overrides --format. Each format is saved to its "
                             "own subfolder.")
    parser.add_argument("--no-artwork", action="store_true", help="Do not download artwork and scans")
    parser.add_argument("--no-extras", action="store_true", help="Do not download extra files (info txt, etc.)")
    parser.add_argument("--no-organize", dest="organize", action="store_false", default=True,
                        help="Do not organize into format subfolders (FLAC, MP3, Artwork)")
    parser.add_argument("--no-tag", action="store_true", help="Do not embed artwork into audio file tags")
    parser.add_argument("--no-playlist", action="store_true", help="Do not generate .m3u playlist")
    parser.add_argument("-w", "--workers", type=int, default=6,
                        help="Number of parallel download connections (default: 6)")

    args = parser.parse_args()

    if args.gui or not args.url:
        from khinsider_gui import main as gui_main
        gui_main()
    else:
        run_cli(args)


if __name__ == "__main__":
    main()
