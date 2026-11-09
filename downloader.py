"""Shared YouTube inspection and download logic for the CLI and web server."""

from copy import deepcopy
from dataclasses import dataclass
from io import StringIO
import json
from math import isfinite
import os
from pathlib import Path
import re
from shlex import join as shell_join
import shutil
import subprocess
from tempfile import TemporaryDirectory, TemporaryFile
from time import monotonic, time
from urllib.parse import parse_qs, urlsplit

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError, ExtractorError, sanitize_filename

CONTAINERS = ("mp4", "mkv", "webm")
MODES = ("full", "audio", "video", "subtitle", "thumbnail")
COOKIE_PATH = Path(__file__).resolve().with_name("cookie.txt")
YOUTUBE_HOSTS = {
    "youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com",
    "www.youtube-nocookie.com", "youtube-nocookie.com",
}


class RequestError(ValueError):
    """The URL or requested selection is invalid."""


class DownloadFailure(RuntimeError):
    """A download failed after validation."""

    def __init__(self, message, folder=None, files=()):
        super().__init__(message)
        self.folder = folder
        self.files = list(files)


@dataclass(frozen=True)
class Selection:
    mode: str = "full"
    audio: str = ""
    video: str = ""
    container: str = "mp4"
    subtitle: str = ""
    thumbnail: bool = False


def youtube_url(value):
    if not isinstance(value, str):
        raise RequestError("Enter a YouTube video URL.")
    try:
        parsed = urlsplit(value.strip())
        host = parsed.hostname
        if (parsed.scheme not in ("http", "https") or parsed.username is not None
                or parsed.password is not None or parsed.port is not None):
            raise ValueError
        segments = parsed.path.strip("/").split("/")
        if host == "youtu.be" and len(segments) == 1:
            video_id = segments[0]
        elif host in YOUTUBE_HOSTS and parsed.path == "/watch":
            video_id = parse_qs(parsed.query).get("v", [""])[0]
        elif (host in YOUTUBE_HOSTS and len(segments) == 2
              and segments[0] in ("shorts", "live", "embed", "v")):
            video_id = segments[1]
        else:
            raise ValueError
        if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
            raise ValueError
    except ValueError as error:
        raise RequestError("Enter a URL for a single YouTube video.") from error

    return f"https://www.youtube.com/watch?v={video_id}"


def split_pair(value):
    if isinstance(value, str):
        codes = value.strip().split("+")
        if len(codes) == 2 and all(re.fullmatch(r"[^\s+]{1,100}", code) for code in codes):
            return tuple(codes)
    raise RequestError("Use an AUDIO+VIDEO format pair, for example 251+399.")


def download_root():
    configured = os.environ.get("YT_DOWNLOAD_DIR")
    return Path(configured).expanduser().resolve() if configured else Path.home() / "Downloads"


def local_cookie_options():
    try:
        if not COOKIE_PATH.is_file():
            return {}
        contents = COOKIE_PATH.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return {}

    lines = contents.split("\n")
    if not lines or lines[0] not in ("# Netscape HTTP Cookie File", "# HTTP Cookie File"):
        return {}

    now = time()
    has_current_cookie = False
    for line in lines[1:]:
        entry = line.removeprefix("#HttpOnly_")
        if not entry.strip() or entry.startswith("#"):
            continue
        fields = entry.split("\t")
        if len(fields) != 7 or re.search(r"[\x00-\x08\x0a-\x1f\x7f]", entry):
            return {}
        domain, subdomains, path, secure, expiry, name, value = fields
        if (not domain or subdomains != ("TRUE" if domain.startswith(".") else "FALSE")
                or not path.startswith("/") or secure not in ("TRUE", "FALSE")
                or not (name or value) or (expiry and not re.fullmatch(r"[0-9]{1,20}(?:\.[0-9]+)?", expiry))):
            return {}
        expires_at = int(expiry.partition(".")[0] or 0)
        if expires_at == 0 or expires_at > now:
            has_current_cookie = True

    return {"cookiefile": StringIO(contents)} if has_current_cookie else {}


def ydl_options(extra=None, **options):
    runtimes = {name: {} for name in ("deno", "node") if shutil.which(name)}
    settings = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        "socket_timeout": 30,
        "js_runtimes": runtimes,
        **(extra or {}),
        **options,
    }
    cookiefile = settings.get("cookiefile")
    if isinstance(cookiefile, StringIO):
        # yt-dlp writes cookies on close. Give each instance its own in-memory copy.
        settings["cookiefile"] = StringIO(cookiefile.getvalue())
    return settings


def inspect_video(url, extra=None):
    try:
        with YoutubeDL(ydl_options(extra, format="best*", ignore_no_formats_error=True)) as downloader:
            info = downloader.extract_info(youtube_url(url), download=False)
    except (DownloadError, ExtractorError) as error:
        raise DownloadFailure(str(error)) from error

    if not info or info.get("_type", "video") != "video":
        raise RequestError("This URL did not resolve to a single video.")
    if info.get("live_status") in ("is_live", "is_upcoming", "post_live"):
        raise RequestError("Wait until the livestream recording is available.")
    return info


def has_track(item, track):
    return item.get({"audio": "acodec", "video": "vcodec"}[track]) not in (None, "", "none")


def format_kind(item):
    if item.get("has_drm") and item["has_drm"] != "maybe":
        return "unavailable"
    audio, video = has_track(item, "audio"), has_track(item, "video")
    if audio and video:
        return "combined"
    if audio:
        return "audio"
    if video:
        return "video"
    return "unavailable"


def subtitles(info):
    languages = {}
    for field, source in (("subtitles", "Manual"), ("automatic_captions", "Automatic")):
        for code, tracks in (info.get(field) or {}).items():
            if code != "live_chat" and tracks and re.fullmatch(r"[\w.-]{1,100}", code):
                selected = next((track for track in reversed(tracks) if track.get("ext") == "vtt"), tracks[-1])
                size, estimated = media_size(selected)
                languages.setdefault(code, {
                    "id": code, "name": tracks[0].get("name") or code, "source": source,
                    "size": size, "size_estimated": estimated,
                })
    return sorted(languages.values(), key=lambda item: (item["source"] != "Manual", item["id"]))


def media_size(item, duration=None):
    if item.get("filesize"):
        return item["filesize"], False
    if item.get("filesize_approx"):
        return item["filesize_approx"], True

    kind = format_kind(item)
    bitrate = item.get("tbr")
    if not bitrate and kind in ("audio", "video"):
        bitrate = item.get("abr" if kind == "audio" else "vbr")
    if kind != "unavailable" and bitrate and duration and bitrate > 0 and duration > 0:
        return round(bitrate * duration * 125), True
    return None, False


def describe_video(info):
    formats = []
    for item in reversed(info.get("formats") or []):
        size, estimated = media_size(item, info.get("duration"))
        formats.append({
            "id": item.get("format_id"),
            "kind": format_kind(item),
            "ext": item.get("ext"),
            "resolution": item.get("resolution"),
            "fps": item.get("fps"),
            "audio_codec": item.get("acodec"),
            "video_codec": item.get("vcodec"),
            "audio_bitrate": item.get("abr"),
            "size": size,
            "size_estimated": estimated,
            "note": item.get("format_note"),
        })
    thumbnails = info.get("thumbnails") or []
    thumbnail_size, thumbnail_estimated = media_size(thumbnails[-1]) if thumbnails else (None, False)
    return {
        "title": info.get("title"),
        "uploader": info.get("uploader"),
        "thumbnail": info.get("thumbnail"),
        "has_thumbnail": bool(thumbnails),
        "thumbnail_size": thumbnail_size,
        "thumbnail_size_estimated": thumbnail_estimated,
        "formats": formats,
        "subtitles": subtitles(info),
    }


def select_format(info, track, code):
    formats = [item for item in (info.get("formats") or [])
               if format_kind(item) in (track, "combined")]
    if not code:
        separate = [item for item in formats if format_kind(item) == track]
        if formats:
            return (separate or formats)[-1]
        raise RequestError(f"No downloadable {track} formats are available.")
    for item in formats:
        if item.get("format_id") == code:
            return item
    raise RequestError(f"Format {code!r} is not an available {track} format.")


def output_title(info):
    title = sanitize_filename(info.get("title") or "YouTube video").strip(" .")
    title = title.encode("utf-8")[:200].decode("utf-8", errors="ignore").rstrip(" .")
    return title or "YouTube video"


def create_folder(title):
    root = download_root()
    root.mkdir(parents=True, exist_ok=True)
    attempt = 1
    while True:
        folder = root / (title if attempt == 1 else f"{title} ({attempt})")
        try:
            folder.mkdir()
        except FileExistsError:
            attempt += 1
        else:
            return folder


def run_tool(arguments, log=None):
    if log:
        log(shell_join(arguments))
    result = subprocess.run(arguments, capture_output=True, text=True, errors="replace")
    if result.returncode:
        raise DownloadFailure(result.stderr.strip() or f"{arguments[0]} failed.")
    return result.stdout


def run_ffmpeg_progress(command, report):
    with TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as errors:
        with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=errors, text=True) as process:
            fields = {}
            for line in process.stdout:
                key, _, value = line.strip().partition("=")
                fields[key] = value
                if key == "progress":
                    timestamp = fields.get("out_time_us", "")
                    size = fields.get("total_size", "")
                    report(max(0, int(timestamp)) / 1_000_000 if timestamp.lstrip("-").isdigit() else None,
                           int(size) if size.isdigit() else None)
                    fields.clear()
            if process.wait():
                errors.seek(0)
                raise DownloadFailure(errors.read().strip() or "FFmpeg failed.")


def ffmpeg_output(arguments, destination, progress=None, log=None):
    temporary = destination.with_name(f".{destination.stem}.partial{destination.suffix}")
    command = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y"]
    if progress:
        command += ["-progress", "pipe:1", "-stats_period", "0.25"]
    command += [*arguments, str(temporary)]
    try:
        if progress:
            if log:
                log(shell_join(command))
            run_ffmpeg_progress(command, progress)
        else:
            run_tool(command, log)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def probe_track(path, track, log=None):
    try:
        metadata = run_tool([
            "ffprobe", "-v", "error", "-show_entries", "stream=codec_type,codec_name:format=duration",
            "-of", "json", str(path),
        ], log)
    except DownloadFailure as error:
        raise DownloadFailure(f"The downloaded {track} file is not valid media.") from error

    data = json.loads(metadata)
    streams = [stream for stream in data.get("streams", [])
               if stream.get("codec_type") in ("audio", "video")]
    if len(streams) != 1 or streams[0].get("codec_type") != track or not streams[0].get("codec_name"):
        raise DownloadFailure(f"The downloaded file does not contain exactly one {track} stream.")
    try:
        duration = float(data.get("format", {}).get("duration", 0))
    except (TypeError, ValueError):
        duration = 0
    return streams[0]["codec_name"], duration if isfinite(duration) and duration > 0 else 0


def merge_tracks(video, audio, destination, container, codecs, progress=None, duration=0, log=None):
    options = ["-c", "copy"]
    if container == "mp4":
        if codecs["video"] not in ("h264", "hevc", "av1", "vp9", "mpeg4"):
            options += ["-c:v", "libx264", "-preset", "fast", "-crf", "20"]
        if codecs["audio"] != "aac":
            options += ["-c:a", "aac", "-b:a", "192k"]
        options += ["-movflags", "+faststart"]
    elif container == "webm":
        if codecs["video"] not in ("vp8", "vp9", "av1"):
            options += ["-c:v", "libvpx-vp9", "-crf", "30", "-b:v", "0"]
        if codecs["audio"] not in ("opus", "vorbis"):
            options += ["-c:a", "libopus", "-b:a", "128k"]

    started = monotonic()

    def report(seconds, size):
        fraction = seconds / duration if duration and seconds is not None else None
        eta = (1 - fraction) * (monotonic() - started) / fraction if fraction and fraction < 1 else None
        progress({"stage": "merge", "state": "running", "bytes": size, "eta": eta,
                  "percent": min(99, fraction * 100) if fraction is not None else None})

    if progress:
        progress({"stage": "merge", "state": "running", "percent": 0})
    result = ffmpeg_output([
        "-i", str(video), "-i", str(audio), "-map", "0:v:0", "-map", "1:a:0", *options,
    ], destination, progress=report if progress else None, log=log)
    if progress:
        progress({"stage": "merge", "state": "done", "percent": 100, "bytes": result.stat().st_size})
    return result


class TransferProgress:
    def __init__(self, stage, item, duration, report, reused):
        self.stage = stage
        self.report = report
        self.reused = reused
        self.total, self.estimated = (0, False) if reused else media_size(item, duration)
        self.downloaded = None
        self.last_update = 0
        self.emit("running", 0, 0)

    def emit(self, state, percent, size, speed=None, eta=None):
        self.report({"stage": self.stage, "state": state, "percent": percent, "bytes": size,
                     "total": self.total, "estimated": self.estimated, "speed": speed,
                     "eta": eta, "reused": self.reused})

    def update(self, event):
        if event.get("status") == "finished":
            self.downloaded = event.get("downloaded_bytes") or event.get("total_bytes")
            return
        if event.get("status") != "downloading":
            return
        now = monotonic()
        if now - self.last_update < 0.25:
            return
        self.last_update = now
        if event.get("total_bytes"):
            self.total, self.estimated = event["total_bytes"], False
        elif event.get("total_bytes_estimate") and (self.total is None or self.estimated):
            self.total, self.estimated = event["total_bytes_estimate"], True
        size = event.get("downloaded_bytes") or 0
        speed = event.get("speed")
        eta = event.get("eta")
        if (eta is None or eta <= 0) and self.total and speed and speed > 0:
            eta = max(0, self.total - size) / speed
        percent = min(99, 100 * size / self.total) if self.total else None
        self.emit("running", percent, size, speed, eta)

    def finish(self, source):
        self.total, self.estimated = (0 if self.reused else self.downloaded or source.stat().st_size), False
        self.emit("done", 100, self.total)


def write_item(info, folder, name, extra, *, file_stem=None, **options):
    template = str(folder / name).replace("%", "%%") + ".%(ext)s"
    item = deepcopy(info)
    item.pop("requested_formats", None)
    with YoutubeDL(ydl_options(extra, outtmpl={"default": template}, **options)) as downloader:
        downloader.process_ie_result(item, download=True)

    stem = file_stem or name
    matches = [path for path in folder.glob(f"{name}.*")
               if path.stem == stem and path.suffix not in (".part", ".ytdl", ".tmp")
               and path.is_file() and path.stat().st_size]
    if len(matches) != 1:
        raise DownloadFailure(f"No complete {name} file was produced.")
    if options.get("skip_download"):
        with matches[0].open("rb") as file:
            header = file.read(512).lstrip().lower()
        if header.startswith((b"<html", b"<!doctype html")):
            raise DownloadFailure(f"The server returned an HTML page for the {name} download.")
    return matches[0]


def write_stream(info, item, track, staging, sources, extra, hooks, log=None):
    code = item["format_id"]
    def exact_format(context):
        return (candidate for candidate in context["formats"] if candidate["format_id"] == code)

    name = f"{track}raw"
    if log:
        if code in sources:
            log(f"yt-dlp API: reused format {code} for {track}")
        else:
            log(f"yt-dlp API: download format {code} for {track}")
    if format_kind(item) != "combined":
        return write_item(info, staging, name, extra, format=exact_format, progress_hooks=hooks)

    if code not in sources:
        sources[code] = write_item(info, staging, f"source-{len(sources)}", extra,
                                   format=exact_format, progress_hooks=hooks)
    source = sources[code]
    extension = source.suffix
    if track == "audio" and extension == ".mp4":
        extension = ".m4a"
    return ffmpeg_output(
        ["-i", str(source), "-map", f"0:{track[0]}:0", "-c", "copy"],
        staging / f"{name}{extension}", log=log,
    )


def download(info, selection, extra=None, report=lambda message: None, progress=None, log=None):
    if selection.mode not in MODES or selection.container not in CONTAINERS:
        raise RequestError("Choose a valid download mode and output format.")

    tracks = ("audio", "video") if selection.mode == "full" else (selection.mode,)
    streams = {track: select_format(info, track, getattr(selection, track))
               for track in tracks if track in ("audio", "video")}
    language = selection.subtitle if selection.mode in ("full", "subtitle") else ""
    thumbnail = selection.mode == "thumbnail" or (selection.mode == "full" and selection.thumbnail)
    if selection.mode == "subtitle" and not language:
        raise RequestError("Choose a subtitle language.")
    if language and language not in {item["id"] for item in subtitles(info)}:
        raise RequestError(f"Subtitle {language!r} is unavailable.")
    if thumbnail and not info.get("thumbnails"):
        raise RequestError("No thumbnail is available for this video.")

    needs_merge = selection.mode == "full"
    needs_demux = any(format_kind(item) == "combined" for item in streams.values())
    if (needs_merge or needs_demux) and not shutil.which("ffmpeg"):
        raise DownloadFailure("FFmpeg is required for this selection.")
    if streams and not shutil.which("ffprobe"):
        raise DownloadFailure("FFprobe is required to verify audio and video streams.")

    folder = None
    files = []
    try:
        title = output_title(info)
        folder = create_folder(title)
        report(f"Destination: {folder}")
        with TemporaryDirectory(prefix=".download-", dir=folder) as temporary:
            staging = Path(temporary)
            sources, raw, codecs = {}, {}, {}
            duration = info.get("duration") or 0
            for track, item in streams.items():
                report(f"Downloading {track} format {item['format_id']}...")
                code = item["format_id"]
                tracker = TransferProgress(track, item, duration, progress, code in sources) if progress else None
                hooks = [tracker.update] if tracker and not tracker.reused else []
                path = write_stream(info, item, track, staging, sources, extra, hooks, log)
                codecs[track], track_duration = probe_track(path, track, log)
                duration = max(duration, track_duration)
                raw[track] = path.replace(folder / f"{title}_{path.name}")
                files.append(raw[track])
                if tracker:
                    tracker.finish(sources.get(code, raw[track]))
            if needs_merge:
                report(f"Merging the raw streams into {selection.container.upper()}...")
                files.append(merge_tracks(raw["video"], raw["audio"],
                                          folder / f"{title}.{selection.container}", selection.container, codecs,
                                          progress=progress, duration=duration, log=log))
            if language:
                report(f"Downloading subtitles ({language})...")
                if log:
                    log(f"yt-dlp API: download subtitle {language}")
                path = write_item(
                    info, staging, "subtitle", extra, file_stem=f"subtitle.{language}",
                    skip_download=True, writesubtitles=True, writeautomaticsub=True,
                    subtitleslangs=[re.escape(language)], subtitlesformat="vtt/best",
                    ignore_no_formats_error=True,
                )
                files.append(path.replace(folder / f"{title}_sub{path.suffix}"))
            if thumbnail:
                report("Downloading thumbnail...")
                if log:
                    log("yt-dlp API: download thumbnail")
                path = write_item(info, staging, "thumbnail", extra,
                                  skip_download=True, writethumbnail=True, ignore_no_formats_error=True)
                files.append(path.replace(folder / f"{title}_thumbnail{path.suffix}"))
    except (DownloadError, ExtractorError, DownloadFailure, OSError) as error:
        raise DownloadFailure(str(error), folder, files) from error

    result = {"directory": str(folder), "files": [str(path) for path in files]}
    if progress:
        result["saved_bytes"] = sum(path.stat().st_size for path in files)
    return result
