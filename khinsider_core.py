"""
KHInsider Downloader Core Engine
Handles scraping album metadata, tracks, all audio formats (MP3, FLAC, etc.),
lossless artwork and scans, concurrent downloading, and tagging.
"""

import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter

try:
    from urllib3.util.retry import Retry
except ImportError:  # pragma: no cover - very old urllib3
    Retry = None

# Try importing mutagen for optional tagging / artwork embedding
try:
    import mutagen
    from mutagen.flac import FLAC, Picture
    from mutagen.id3 import APIC, ID3, ID3NoHeaderError
    from mutagen.mp3 import MP3
    MUTAGEN_AVAILABLE = True
except ImportError:
    MUTAGEN_AVAILABLE = False

# lxml parses the large songlist tables several times faster than the stdlib
# parser; fall back transparently when it is not installed.
try:
    import lxml  # noqa: F401
    HTML_PARSER = "lxml"
except ImportError:
    HTML_PARSER = "html.parser"


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

BASE_URL = "https://downloads.khinsider.com"

# Formats considered lossless, in descending order of preference.
LOSSLESS_FORMATS = ["flac", "wav", "aiff", "ape"]

# Streaming chunk size. 1 MiB keeps syscall/loop overhead negligible on fast links
# while still giving responsive progress and cancellation.
CHUNK_SIZE = 1024 * 1024

_SIZE_UNITS = {"b": 1, "kb": 1024, "mb": 1024 ** 2, "gb": 1024 ** 3}

# Matches a tracklist size cell such as "7.56 MB" (but not a "4:02" duration).
_SIZE_CELL_RE = re.compile(r'^\s*[\d.]+\s*[KMG]?B\s*$', re.IGNORECASE)


def build_session(pool_size: int = 16) -> requests.Session:
    """
    Creates a requests Session tuned for many parallel transfers against one host:
    a connection pool large enough that workers never contend for a socket, plus
    keep-alive so the TLS handshake is paid once rather than per file.
    """
    session = requests.Session()
    session.headers.update({
        "User-Agent": USER_AGENT,
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
    })

    adapter_kwargs = dict(pool_connections=pool_size, pool_maxsize=pool_size)
    if Retry is not None:
        adapter_kwargs["max_retries"] = Retry(
            total=3,
            backoff_factor=0.4,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset(["GET", "HEAD"]),
        )

    adapter = HTTPAdapter(**adapter_kwargs)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def parse_size_to_bytes(size_str: str) -> int:
    """Converts a KHInsider size label such as '5.2 MB' into bytes (0 if unparseable)."""
    if not size_str:
        return 0
    m = re.match(r'^\s*([\d.]+)\s*([KMG]?B)\s*$', size_str.strip(), re.IGNORECASE)
    if not m:
        return 0
    try:
        return int(float(m.group(1)) * _SIZE_UNITS[m.group(2).lower()])
    except (ValueError, KeyError):
        return 0


def format_bytes(num: float) -> str:
    """Human readable byte count."""
    if num >= 1024 ** 3:
        return f"{num / 1024 ** 3:.2f} GB"
    if num >= 1024 ** 2:
        return f"{num / 1024 ** 2:.1f} MB"
    if num >= 1024:
        return f"{num / 1024:.0f} KB"
    return f"{int(num)} B"


def format_speed(bytes_per_sec: float) -> str:
    if bytes_per_sec >= 1024 ** 2:
        return f"{bytes_per_sec / 1024 ** 2:.2f} MB/s"
    if bytes_per_sec >= 1024:
        return f"{bytes_per_sec / 1024:.0f} KB/s"
    return f"{int(bytes_per_sec)} B/s"


def format_duration(seconds: float) -> str:
    if seconds <= 0 or seconds != seconds or seconds == float("inf"):
        return "--:--"
    seconds = int(seconds)
    if seconds >= 3600:
        return f"{seconds // 3600}h {(seconds % 3600) // 60:02d}m"
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


class AlbumNotFoundError(Exception):
    """Raised when a URL or slug does not resolve to a real KHInsider album."""


def _silent_remove(path: str) -> None:
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


class DownloadStats:
    """
    Thread-safe aggregate counters for one download session.

    `add_bytes` is called from every worker thread on every chunk, so it stays
    as cheap as possible - a lock and an integer add, no formatting or callbacks.
    """

    def __init__(self, total_files: int = 0, expected_bytes: int = 0):
        self.total_files = total_files
        self.expected_bytes = expected_bytes
        self.completed_files = 0
        self.failed_files = 0
        self.bytes_downloaded = 0
        self.start_time = time.time()
        self._lock = threading.Lock()

    def add_bytes(self, count: int) -> None:
        with self._lock:
            self.bytes_downloaded += count

    @property
    def elapsed(self) -> float:
        return max(1e-6, time.time() - self.start_time)

    @property
    def speed(self) -> float:
        """Session-wide throughput in bytes/sec, summed across all workers."""
        return self.bytes_downloaded / self.elapsed

    @property
    def eta(self) -> float:
        """Seconds remaining, estimated from advertised sizes where known."""
        speed = self.speed
        if speed <= 0:
            return 0.0
        if self.expected_bytes > self.bytes_downloaded:
            return (self.expected_bytes - self.bytes_downloaded) / speed
        if self.completed_files and self.completed_files < self.total_files:
            per_file = self.elapsed / self.completed_files
            return per_file * (self.total_files - self.completed_files)
        return 0.0

    @property
    def percent(self) -> int:
        if not self.total_files:
            return 0
        done = self.completed_files + self.failed_files
        return min(100, int(done / self.total_files * 100))


@dataclass
class ArtworkInfo:
    url: str
    filename: str
    is_lossless: bool = False
    description: str = ""
    thumbnail_url: str = ""


@dataclass
class TrackInfo:
    disc_number: str
    track_number: str
    title: str
    duration: str
    page_url: str
    sizes: Dict[str, str] = field(default_factory=dict)         # e.g. {'mp3': '5.2 MB', 'flac': '24 MB'}
    download_urls: Dict[str, str] = field(default_factory=dict) # e.g. {'mp3': 'https://...', 'flac': 'https://...'}


@dataclass
class AlbumInfo:
    url: str
    album_id: str
    title: str
    platforms: List[str] = field(default_factory=list)
    year: str = ""
    formats_available: List[str] = field(default_factory=list)
    tracks: List[TrackInfo] = field(default_factory=list)
    artworks: List[ArtworkInfo] = field(default_factory=list)
    extra_files: List[Tuple[str, str]] = field(default_factory=list)  # (url, filename)
    thumbnail_url: str = ""


def sanitize_filename(name: str) -> str:
    """Sanitizes a string to be a safe directory or file name on Windows/Linux."""
    cleaned = re.sub(r'[<>:"/\\|?*]', '-', name)
    cleaned = re.sub(r'[\r\n\t]', ' ', cleaned)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned or "unnamed"


def normalize_album_url(input_str: str) -> str:
    """
    Normalizes an input string to a valid KHInsider album URL.
    Supports full album URLs, track URLs, and album slugs.
    """
    input_str = input_str.strip()
    if not input_str:
        return ""
    
    if input_str.startswith("http://") or input_str.startswith("https://"):
        parsed = urlparse(input_str)
        path = parsed.path.rstrip('/')
        parts = path.split('/')
        if "album" in parts:
            idx = parts.index("album")
            if idx + 1 < len(parts):
                album_slug = parts[idx + 1]
                return f"{BASE_URL}/game-soundtracks/album/{album_slug}"
        return input_str
    
    slug = input_str.strip('/').split('/')[-1]
    return f"{BASE_URL}/game-soundtracks/album/{slug}"


class KHInsiderScraper:
    def __init__(self, session: Optional[requests.Session] = None, pool_size: int = 16):
        self.session = session or build_session(pool_size)
        self.session.headers.setdefault("User-Agent", USER_AGENT)

    def fetch_soup(self, url: str) -> BeautifulSoup:
        response = self.session.get(url, timeout=20)
        response.raise_for_status()
        return BeautifulSoup(response.text, HTML_PARSER)

    def parse_album(self, album_url: str) -> AlbumInfo:
        album_url = normalize_album_url(album_url)
        soup = self.fetch_soup(album_url)

        parsed = urlparse(album_url)
        album_id = parsed.path.rstrip('/').split('/')[-1]

        h2_title = soup.find('h2')
        if h2_title and h2_title.text:
            raw_title = h2_title.text.strip()
        else:
            raw_title = unquote(album_id).replace('-', ' ').title()
        title = sanitize_filename(raw_title)

        platforms = []
        year = ""
        for p in soup.find_all('p'):
            text = p.get_text()
            if "Platforms:" in text:
                for a in p.find_all('a'):
                    platforms.append(a.get_text(strip=True))
            if "Year:" in text or "Released:" in text:
                m = re.search(r'\b(19\d\d|20\d\d)\b', text)
                if m:
                    year = m.group(1)

        artworks: List[ArtworkInfo] = []
        seen_art_urls = set()

        for div in soup.find_all('div', class_='albumImage'):
            a_tag = div.find('a')
            img_tag = div.find('img')
            
            orig_url = ""
            thumb_url = ""
            if a_tag and a_tag.get('href'):
                orig_url = a_tag['href']
            if img_tag and img_tag.get('src'):
                thumb_url = img_tag['src']
                if not orig_url:
                    orig_url = thumb_url.replace('/thumbs/', '/')

            if orig_url:
                orig_url = urljoin(BASE_URL, orig_url)
                if orig_url not in seen_art_urls:
                    seen_art_urls.add(orig_url)
                    filename = sanitize_filename(unquote(orig_url.split('/')[-1]))
                    is_lossless = filename.lower().endswith(('.png', '.tiff', '.tif', '.flac'))
                    desc = "Artwork"
                    if "front" in filename.lower():
                        desc = "Front Cover"
                    elif "back" in filename.lower():
                        desc = "Back Cover"
                    elif "disc" in filename.lower():
                        desc = "Disc Artwork"
                    elif "booklet" in filename.lower():
                        desc = "Booklet"

                    artworks.append(ArtworkInfo(
                        url=orig_url,
                        filename=filename,
                        is_lossless=is_lossless,
                        description=desc,
                        thumbnail_url=thumb_url or orig_url
                    ))

        for a in soup.find_all('a'):
            href = a.get('href', '')
            lower_href = href.lower()
            if any(lower_href.endswith(ext) for ext in ['.jpg', '.jpeg', '.png', '.webp', '.tiff']):
                full_link = urljoin(BASE_URL, href)
                if 'vgmtreasurechest' in full_link and full_link not in seen_art_urls:
                    seen_art_urls.add(full_link)
                    filename = sanitize_filename(unquote(full_link.split('/')[-1]))
                    is_lossless = filename.lower().endswith(('.png', '.tiff', '.tif'))
                    artworks.append(ArtworkInfo(
                        url=full_link,
                        filename=filename,
                        is_lossless=is_lossless,
                        description="Scan / Artwork",
                        thumbnail_url=full_link
                    ))

        thumbnail_url = artworks[0].thumbnail_url if artworks else ""

        extra_files: List[Tuple[str, str]] = []
        for a in soup.find_all('a'):
            href = a.get('href', '')
            if not href:
                continue
            lower = href.lower()
            if 'khinsider.info.txt' in lower or any(lower.endswith(ext) for ext in ['.pdf', '.cue', '.log', '.zip', '.txt']):
                if 'vgmtreasurechest' in href or 'downloads.khinsider' in href:
                    full_extra_url = urljoin(BASE_URL, href)
                    extra_filename = sanitize_filename(unquote(full_extra_url.split('/')[-1]))
                    if not any(x[0] == full_extra_url for x in extra_files):
                        extra_files.append((full_extra_url, extra_filename))

        formats_available: List[str] = []
        tracks: List[TrackInfo] = []

        songlist_table = soup.find('table', id='songlist')

        # KHInsider answers an unknown slug with a 200 "Ooops!" page rather than
        # a 404, which would otherwise look like a real album holding nothing.
        if not songlist_table and not artworks and raw_title.lower().startswith("ooops"):
            raise AlbumNotFoundError(
                f"No album found at {album_url}. Check the album URL or slug."
            )

        if not songlist_table:
            return AlbumInfo(
                url=album_url,
                album_id=album_id,
                title=title,
                platforms=platforms,
                year=year,
                formats_available=[],
                tracks=[],
                artworks=artworks,
                extra_files=extra_files,
                thumbnail_url=thumbnail_url
            )

        header_tr = songlist_table.find('tr', id='songlist_header')
        format_cols: Dict[int, str] = {}
        if header_tr:
            cells = header_tr.find_all(['th', 'td'])
            for idx, cell in enumerate(cells):
                txt = cell.get_text(strip=True).upper()
                if txt in ['MP3', 'FLAC', 'M4A', 'OGG', 'WAV', 'OPUS']:
                    format_cols[idx] = txt.lower()
                    if txt.lower() not in formats_available:
                        formats_available.append(txt.lower())

        for tr in songlist_table.find_all('tr'):
            tr_id = tr.get('id', '')
            if tr_id in ['songlist_header', 'songlist_footer']:
                continue

            tds = tr.find_all('td')
            if not tds:
                continue

            track_a = None
            for a in tr.find_all('a'):
                href = a.get('href', '')
                if href.startswith('/game-soundtracks/album/'):
                    track_a = a
                    break

            if not track_a:
                continue

            page_url = urljoin(BASE_URL, track_a['href'])
            title_text = track_a.get_text(strip=True)

            disc_num = "1"
            track_num = ""
            duration = ""
            sizes: Dict[str, str] = {}

            text_cells = [td.get_text(strip=True) for td in tds]

            dur_idx = -1
            for idx, cell_val in enumerate(text_cells):
                if re.match(r'^\d+:\d{2}$', cell_val) or re.match(r'^\d+:\d{2}:\d{2}$', cell_val):
                    duration = cell_val
                    dur_idx = idx
                    break

            nums = []
            for i in range(dur_idx if dur_idx > 0 else min(3, len(text_cells))):
                val = text_cells[i].rstrip('.')
                if val.isdigit():
                    nums.append(val)

            if len(nums) >= 2:
                disc_num = nums[0]
                track_num = nums[1]
            elif len(nums) == 1:
                track_num = nums[0]
            else:
                track_num = str(len(tracks) + 1)

            # The header labels the format columns but omits a label for the
            # duration column, so header indices do not line up with data cells.
            # The size cells do appear in header order though, so match them by
            # shape and zip them onto the format names.
            size_cells = [c for c in text_cells if _SIZE_CELL_RE.match(c)]
            for fmt, size_text in zip(formats_available, size_cells):
                sizes[fmt] = size_text

            tracks.append(TrackInfo(
                disc_number=disc_num,
                track_number=track_num,
                title=title_text or f"Track {len(tracks) + 1}",
                duration=duration,
                page_url=page_url,
                sizes=sizes
            ))

        return AlbumInfo(
            url=album_url,
            album_id=album_id,
            title=title,
            platforms=platforms,
            year=year,
            formats_available=formats_available,
            tracks=tracks,
            artworks=artworks,
            extra_files=extra_files,
            thumbnail_url=thumbnail_url
        )

    def resolve_track_downloads(self, track_page_url: str) -> Dict[str, str]:
        download_urls = {}
        soup = self.fetch_soup(track_page_url)

        for a in soup.find_all('a'):
            href = a.get('href', '')
            if not href:
                continue
            text = a.get_text(strip=True).lower()
            if 'click here to download' in text or 'download' in text:
                full_href = urljoin(BASE_URL, href)
                for fmt in ['flac', 'mp3', 'm4a', 'ogg', 'wav', 'opus']:
                    if fmt in text or full_href.lower().endswith(f'.{fmt}'):
                        download_urls[fmt] = full_href
                        break

        if not download_urls:
            audio = soup.find('audio')
            if audio and audio.get('src'):
                src = urljoin(BASE_URL, audio['src'])
                ext = src.split('.')[-1].split('?')[0].lower()
                download_urls[ext] = src

        return download_urls

    def resolve_all_tracks_concurrently(
        self,
        tracks: List[TrackInfo],
        max_workers: int = 6,
        progress_callback: Optional[Callable[[int, int], None]] = None,
        should_stop: Optional[Callable[[], bool]] = None
    ):
        total = len(tracks)
        completed = 0

        def resolve_single(track: TrackInfo):
            if should_stop and should_stop():
                return
            urls = self.resolve_track_downloads(track.page_url)
            track.download_urls = urls

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_track = {executor.submit(resolve_single, t): t for t in tracks}
            for future in as_completed(future_to_track):
                if should_stop and should_stop():
                    executor.shutdown(wait=False, cancel_futures=True)
                    break
                completed += 1
                if progress_callback:
                    progress_callback(completed, total)


@dataclass
class DownloadOptions:
    output_dir: str = "downloads"
    format_preference: str = "all"  # 'all', 'lossless', 'mp3', 'art_only'
    # Explicit per-format selection, e.g. ['flac'] or ['flac', 'mp3'].
    # When set it overrides `format_preference` entirely; an empty list means
    # "no audio at all" (artwork/extras only). None = derive from format_preference.
    selected_formats: Optional[List[str]] = None
    download_artwork: bool = True
    download_extras: bool = True
    organize_by_format: bool = True
    embed_cover_art: bool = True
    generate_m3u: bool = True
    # Files already present at their final path are left alone. Turn this off to
    # force a re-download of everything.
    skip_existing: bool = True
    max_concurrent_downloads: int = 6
    max_resolve_workers: int = 16

    def resolve_formats(self, available: List[str]) -> List[str]:
        """
        Returns the concrete list of audio formats to download for an album,
        preserving the album's own format ordering.
        """
        avail = [f.lower() for f in available]

        if self.selected_formats is not None:
            wanted = {f.lower() for f in self.selected_formats}
            return [f for f in avail if f in wanted]

        pref = (self.format_preference or "all").lower()
        if pref == "art_only":
            return []
        if pref == "all":
            return list(avail)
        if pref == "lossless":
            for fmt in LOSSLESS_FORMATS:
                if fmt in avail:
                    return [fmt]
            return ["mp3"] if "mp3" in avail else avail[:1]
        if pref in avail:
            return [pref]
        return avail[:1]


class DownloadTask:
    def __init__(self, url: str, target_path: str, file_type: str, metadata: Optional[dict] = None):
        self.url = url
        self.target_path = target_path
        self.file_type = file_type  # 'audio', 'artwork', 'extra'
        self.metadata = metadata or {}


class KHInsiderDownloader:
    def __init__(
        self,
        options: DownloadOptions,
        scraper: Optional[KHInsiderScraper] = None
    ):
        self.options = options
        pool = max(8, options.max_concurrent_downloads * 2, options.max_resolve_workers)
        self.scraper = scraper or KHInsiderScraper(pool_size=pool)
        self.session = self.scraper.session
        self._is_cancelled = False
        # Album dir -> (cover bytes, mime), so the cover is read from disk once
        # per album instead of once per tagged track.
        self._cover_cache: Dict[str, Optional[Tuple[bytes, str]]] = {}
        self._cover_lock = threading.Lock()

    def cancel(self):
        self._is_cancelled = True

    @property
    def is_cancelled(self) -> bool:
        return self._is_cancelled

    def build_download_queue(
        self,
        album: AlbumInfo,
        selected_tracks: Optional[List[TrackInfo]] = None
    ) -> List[DownloadTask]:
        queue: List[DownloadTask] = []
        album_dir = os.path.join(self.options.output_dir, sanitize_filename(album.title))

        tracks_to_process = selected_tracks if selected_tracks is not None else album.tracks

        if self.options.download_artwork and album.artworks:
            art_dir = os.path.join(album_dir, "Artwork") if self.options.organize_by_format else album_dir
            for art in album.artworks:
                target_file = os.path.join(art_dir, art.filename)
                queue.append(DownloadTask(
                    url=art.url,
                    target_path=target_file,
                    file_type="artwork",
                    metadata={"description": art.description, "is_lossless": art.is_lossless}
                ))

        if self.options.download_extras and album.extra_files:
            for extra_url, extra_fn in album.extra_files:
                target_file = os.path.join(album_dir, extra_fn)
                queue.append(DownloadTask(
                    url=extra_url,
                    target_path=target_file,
                    file_type="extra",
                    metadata={"filename": extra_fn}
                ))

        for track in tracks_to_process:
            avail = list(track.download_urls.keys())
            if not avail:
                continue

            # Resolve against what this album advertises, then intersect with what
            # this particular track actually offers (some tracks lack a format).
            wanted = self.options.resolve_formats(album.formats_available or avail)
            formats_to_get = [f for f in wanted if f in avail]

            for fmt in formats_to_get:
                dl_url = track.download_urls.get(fmt)
                if not dl_url:
                    continue
                filename = sanitize_filename(unquote(dl_url.split('/')[-1]))

                # Each audio format always gets its own subfolder (FLAC/, MP3/, ...)
                # so mixed-format albums never interleave in one directory.
                if self.options.organize_by_format:
                    target_dir = os.path.join(album_dir, fmt.upper())
                else:
                    target_dir = album_dir

                target_path = os.path.join(target_dir, filename)
                queue.append(DownloadTask(
                    url=dl_url,
                    target_path=target_path,
                    file_type="audio",
                    metadata={
                        "track_title": track.title,
                        "track_number": track.track_number,
                        "disc_number": track.disc_number,
                        "album_title": album.title,
                        "format": fmt,
                        "referer": track.page_url,
                        # Advertised size, used for a session-wide ETA before
                        # any Content-Length has come back.
                        "expected_bytes": parse_size_to_bytes(track.sizes.get(fmt, "")),
                    }
                    ))

        return queue

    def download_file(
        self,
        task: DownloadTask,
        progress_callback: Optional[Callable[[int, int, float, float], None]] = None,
        embed_cover: bool = True,
        on_bytes: Optional[Callable[[int], None]] = None
    ) -> bool:
        """
        Downloads one task to its target path.

        Streams into a sibling `.part` file and renames on completion, so an
        interrupted transfer never leaves a corrupt file in place. A leftover
        `.part` is resumed with a Range request rather than restarted.

        `on_bytes` receives raw chunk sizes for session-wide throughput accounting.
        """
        target_path = task.target_path
        parent_dir = os.path.dirname(target_path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)
        temp_path = target_path + ".part"

        headers: Dict[str, str] = {}
        referer = task.metadata.get("referer")
        if referer:
            headers["Referer"] = referer

        # A file only ever reaches its final path via an atomic rename once the
        # transfer completed, so its presence alone means "already downloaded".
        # Checking this before any request also makes re-runs cost zero network,
        # and stays correct after cover embedding has changed the file's size.
        if self.options.skip_existing and os.path.exists(target_path):
            _silent_remove(temp_path)
            task.metadata["skipped"] = True
            if progress_callback:
                size = os.path.getsize(target_path)
                progress_callback(size, size, 0.0, 0.0)
            return True

        existing_size = os.path.getsize(target_path) if os.path.exists(target_path) else 0
        resume_from = os.path.getsize(temp_path) if os.path.exists(temp_path) else 0
        if resume_from > 0:
            headers["Range"] = f"bytes={resume_from}-"

        # Connect timeout is kept short; the read timeout covers slow CDN stalls.
        with self.session.get(task.url, headers=headers, stream=True, timeout=(10, 40)) as response:
            if response.status_code == 416:
                # The .part is already at/past the full length - it is unusable, drop it.
                _silent_remove(temp_path)
                return False

            response.raise_for_status()

            resuming = response.status_code == 206 and resume_from > 0
            if not resuming:
                resume_from = 0

            remaining = int(response.headers.get("content-length", 0) or 0)
            total_bytes = remaining + resume_from

            # Skip files we already have in full. Checking the streamed response's
            # headers costs one round trip, where the old HEAD-then-GET cost two.
            if not resuming and total_bytes > 0 and existing_size == total_bytes:
                task.metadata["skipped"] = True
                if progress_callback:
                    progress_callback(total_bytes, total_bytes, 0.0, 0.0)
                return True

            bytes_downloaded = resume_from
            start_time = time.time()
            last_report_time = 0.0

            with open(temp_path, "ab" if resuming else "wb") as f:
                for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                    if self._is_cancelled:
                        # The .part file is deliberately kept so the transfer can
                        # resume instead of restarting from zero.
                        return False
                    if not chunk:
                        continue

                    f.write(chunk)
                    bytes_downloaded += len(chunk)
                    if on_bytes:
                        on_bytes(len(chunk))

                    now = time.time()
                    if progress_callback and now - last_report_time >= 0.2:
                        elapsed = now - start_time
                        speed = (bytes_downloaded - resume_from) / elapsed if elapsed > 0 else 0.0
                        eta = (total_bytes - bytes_downloaded) / speed if (speed > 0 and total_bytes > 0) else 0.0
                        progress_callback(bytes_downloaded, total_bytes, speed, eta)
                        last_report_time = now

        if os.path.exists(temp_path):
            _silent_remove(target_path)
            os.replace(temp_path, target_path)

        if progress_callback:
            progress_callback(bytes_downloaded, total_bytes or bytes_downloaded, 0.0, 0.0)

        if embed_cover and task.file_type == "audio" and self.options.embed_cover_art:
            self._try_embed_cover(task)

        return True

    def download_queue(
        self,
        queue: List[DownloadTask],
        max_workers: Optional[int] = None,
        on_task_start: Optional[Callable[[DownloadTask], None]] = None,
        on_task_progress: Optional[Callable[[DownloadTask, int, int, float], None]] = None,
        on_task_done: Optional[Callable[[DownloadTask, bool, str], None]] = None,
        on_stats: Optional[Callable[['DownloadStats'], None]] = None,
    ) -> Tuple[List[DownloadTask], 'DownloadStats']:
        """
        Runs the whole queue in parallel and returns (completed_tasks, stats).

        Cover-art embedding is deferred to a single pass once every transfer has
        finished, which both avoids re-reading the cover per track and guarantees
        the artwork has actually landed before any audio file is tagged.
        """
        workers = max(1, max_workers or self.options.max_concurrent_downloads)
        expected_total = sum(int(t.metadata.get("expected_bytes", 0) or 0) for t in queue)
        stats = DownloadStats(total_files=len(queue), expected_bytes=expected_total)
        completed: List[DownloadTask] = []
        lock = threading.Lock()
        last_emit = [0.0]

        def emit_stats(force: bool = False):
            if not on_stats:
                return
            now = time.time()
            if force or now - last_emit[0] >= 0.2:
                last_emit[0] = now
                on_stats(stats)

        def run_task(task: DownloadTask) -> None:
            if self._is_cancelled:
                return
            if on_task_start:
                on_task_start(task)

            def progress(done: int, total: int, speed: float, eta: float):
                if on_task_progress:
                    on_task_progress(task, done, total, speed)
                emit_stats()

            try:
                ok = self.download_file(
                    task,
                    progress_callback=progress,
                    embed_cover=False,
                    on_bytes=stats.add_bytes,
                )
                error = "" if ok else ("cancelled" if self._is_cancelled else "incomplete")
            except Exception as exc:
                ok, error = False, str(exc)

            with lock:
                if ok:
                    completed.append(task)
                    stats.completed_files += 1
                elif not self._is_cancelled:
                    stats.failed_files += 1

            if on_task_done:
                on_task_done(task, ok, error)
            emit_stats(force=True)

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(run_task, t) for t in queue]
            for future in as_completed(futures):
                future.result()

        if self.options.embed_cover_art and not self._is_cancelled:
            self.embed_covers(completed)

        emit_stats(force=True)
        return completed, stats

    def embed_covers(self, completed_tasks: List[DownloadTask]) -> None:
        """Tags every downloaded audio file with the album front cover, in one pass."""
        if not MUTAGEN_AVAILABLE:
            return
        for task in completed_tasks:
            if task.file_type == "audio" and not task.metadata.get("skipped"):
                self._try_embed_cover(task)

    def _get_cover(self, audio_dir: str) -> Optional[Tuple[bytes, str]]:
        """
        Finds and reads the album front cover once per directory, caching the
        result (including the 'no cover found' answer) for every later track.
        """
        with self._cover_lock:
            if audio_dir in self._cover_cache:
                return self._cover_cache[audio_dir]

        search_dirs = [
            os.path.join(os.path.dirname(audio_dir), "Artwork"),
            audio_dir,
            os.path.join(audio_dir, "Artwork"),
        ]

        candidate = None
        for s_dir in search_dirs:
            if not os.path.isdir(s_dir):
                continue
            for f in sorted(os.listdir(s_dir)):
                lf = f.lower()
                if lf.endswith((".jpg", ".jpeg", ".png")) and any(x in lf for x in ("front", "cover", "00")):
                    candidate = os.path.join(s_dir, f)
                    break
            if candidate:
                break

        result: Optional[Tuple[bytes, str]] = None
        if candidate:
            try:
                with open(candidate, "rb") as cf:
                    result = (cf.read(), "image/png" if candidate.lower().endswith(".png") else "image/jpeg")
            except OSError:
                result = None

        with self._cover_lock:
            self._cover_cache[audio_dir] = result
        return result

    def _try_embed_cover(self, task: DownloadTask):
        if not MUTAGEN_AVAILABLE:
            return

        audio_path = task.target_path
        cover = self._get_cover(os.path.dirname(audio_path))
        if not cover:
            return
        cover_data, mime = cover

        try:
            if audio_path.lower().endswith(".flac"):
                audio = FLAC(audio_path)
                pic = Picture()
                pic.data = cover_data
                pic.type = 3  # Cover (front)
                pic.mime = mime
                audio.clear_pictures()
                audio.add_picture(pic)
                audio.save()
            elif audio_path.lower().endswith(".mp3"):
                try:
                    audio = ID3(audio_path)
                except ID3NoHeaderError:
                    audio = ID3()
                audio.delall('APIC')
                audio.add(APIC(
                    encoding=3,
                    mime=mime,
                    type=3,
                    desc="Cover",
                    data=cover_data
                ))
                audio.save(audio_path)
        except Exception:
            pass

    def generate_playlists(self, album: AlbumInfo, completed_tasks: List[DownloadTask]):
        if not self.options.generate_m3u:
            return

        album_dir = os.path.join(self.options.output_dir, sanitize_filename(album.title))
        audio_tasks = [t for t in completed_tasks if t.file_type == "audio" and os.path.exists(t.target_path)]
        if not audio_tasks:
            return

        by_format: Dict[str, List[DownloadTask]] = {}
        for t in audio_tasks:
            fmt = t.metadata.get("format", "audio")
            by_format.setdefault(fmt, []).append(t)

        for fmt, tasks in by_format.items():
            playlist_name = f"{sanitize_filename(album.title)} [{fmt.upper()}].m3u"
            playlist_path = os.path.join(album_dir, playlist_name)
            try:
                with open(playlist_path, "w", encoding="utf-8") as f:
                    f.write("#EXTM3U\n")
                    for t in tasks:
                        rel_path = os.path.relpath(t.target_path, album_dir)
                        title = t.metadata.get("track_title", os.path.basename(t.target_path))
                        f.write(f"#EXTINF:-1,{title}\n")
                        f.write(f"{rel_path}\n")
            except Exception:
                pass
