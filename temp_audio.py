"""Cache audio and cover art for Waguri's temporary player."""

from hashlib import sha256
import json
from math import isfinite
import mimetypes
from pathlib import Path
import re
from tempfile import NamedTemporaryFile, TemporaryDirectory
from threading import Lock
from urllib.parse import parse_qs, urlsplit

from flask import Blueprint, abort, send_file, url_for
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError, ExtractorError

from downloader import (
    DownloadFailure, RequestError, YOUTUBE_HOSTS, download_root, format_kind,
    local_cookie_options, probe_track, select_format, write_item, write_stream,
    ydl_options, youtube_url,
)


temp_audio = Blueprint("temp_audio", __name__)
FORMAT_CODE = re.compile(r"[A-Za-z0-9_.-]{1,100}")
VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")
CACHE_KEY = re.compile(r"[a-f0-9]{64}")
ASSET_NAME = re.compile(r"(?:audioraw|thumbnail)\.[a-zA-Z0-9]{1,12}")
CACHE_LOCKS = [Lock() for _ in range(32)]


def cache_root():
    return download_root() / ".waguri-audio"


def cached_result(folder):
    if folder.is_symlink():
        return None
    try:
        result = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
        for name in (result["audio"], result.get("thumbnail")):
            if name is None:
                continue
            file = folder / name
            if (not ASSET_NAME.fullmatch(name) or file.is_symlink() or not file.is_file()
                    or file.stat().st_size <= 0):
                return None
        return result
    except (OSError, ValueError, TypeError, KeyError):
        return None


def prepare_audio(url, requested_format, get_info, log):
    url = youtube_url(url)
    if requested_format and not FORMAT_CODE.fullmatch(requested_format):
        raise RequestError("Choose a valid audio format code.")

    root = cache_root()
    key_for = lambda code: sha256(f"{url}\n{code}".encode()).hexdigest()
    video_id = url.rsplit("=", 1)[-1]
    with CACHE_LOCKS[int(sha256(video_id.encode()).hexdigest(), 16) % len(CACHE_LOCKS)]:
        key = key_for(requested_format or "251")
        cached = cached_result(root / key)
        default_format = root / f"{video_id}.default"
        if not cached and not requested_format:
            try:
                saved_key = default_format.read_text(encoding="ascii").strip()
            except (OSError, UnicodeError):
                saved_key = ""
            if CACHE_KEY.fullmatch(saved_key):
                cached = cached_result(root / saved_key)
                if cached:
                    key = saved_key
        if cached:
            return response_paths(key, cached, reused=True)

        extra = local_cookie_options()
        info = get_info(url, extra)
        if requested_format:
            item = select_format(info, "audio", requested_format)
        else:
            options = info.get("formats") or []
            item = next((entry for entry in options if entry.get("format_id") == "251"
                         and format_kind(entry) in ("audio", "combined")), None)
            if item is None:
                item = select_format(info, "audio", "")
        code = item["format_id"]
        key = key_for(code)
        folder = root / key
        cached = cached_result(folder)
        if cached:
            if not requested_format:
                remember_default(default_format, key)
            return response_paths(key, cached, reused=True)

        try:
            if folder.is_symlink():
                raise OSError("The audio cache directory is invalid.")
            folder.mkdir(parents=True, exist_ok=True)
            with TemporaryDirectory(prefix=".pending-", dir=folder) as temporary:
                stage = Path(temporary)
                audio = write_stream(info, item, "audio", stage, {}, extra, [], log)
                _, duration = probe_track(audio, "audio", log)
                cover = None
                if info.get("thumbnails"):
                    try:
                        cover = write_item(info, stage, "thumbnail", extra, skip_download=True,
                                           writethumbnail=True, ignore_no_formats_error=True)
                    except (DownloadError, ExtractorError, DownloadFailure, OSError) as error:
                        log(f"Could not download the audio cover: {error}")
                result = {
                    "title": info.get("title") or video_id,
                    "duration_ms": int(duration * 1000) if isfinite(duration) else 0,
                    "format": code,
                    "audio": audio.name,
                    "thumbnail": cover.name if cover else None,
                }
                audio.replace(folder / audio.name)
                if cover:
                    cover.replace(folder / cover.name)
                manifest = stage / "metadata.json"
                manifest.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
                manifest.replace(folder / manifest.name)
            if not requested_format:
                remember_default(default_format, key)
        except (DownloadError, ExtractorError, OSError) as error:
            raise DownloadFailure(str(error)) from error
        return response_paths(key, result, reused=False)


def remember_default(path, key):
    temporary = None
    try:
        with NamedTemporaryFile(mode="w", encoding="ascii", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(key)
        temporary.replace(path)
    except OSError:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def response_paths(key, result, *, reused):
    return {
        **result,
        "reused": reused,
        "audio": url_for("temp_audio.asset", key=key, name=result["audio"]),
        "thumbnail": url_for("temp_audio.asset", key=key, name=result["thumbnail"])
        if result.get("thumbnail") else None,
    }


@temp_audio.get("/audio-cache/<key>/<name>")
def asset(key, name):
    if not CACHE_KEY.fullmatch(key) or not ASSET_NAME.fullmatch(name):
        abort(404)
    folder = cache_root() / key
    result = cached_result(folder)
    if result is None or name not in (result["audio"], result.get("thumbnail")):
        abort(404)
    mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
    if name == "audioraw.webm":
        mime = "audio/webm"
    return send_file(folder / name, mimetype=mime, conditional=True)


def playlist_tracks(value):
    try:
        parsed = urlsplit(value)
        playlist_id = parse_qs(parsed.query).get("list", [""])[0]
        if (parsed.scheme != "https" or parsed.hostname not in YOUTUBE_HOSTS
                or parsed.username is not None or parsed.password is not None or parsed.port is not None
                or parsed.path != "/playlist" or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", playlist_id)):
            raise ValueError
    except ValueError as error:
        raise RequestError("Choose a YouTube playlist.") from error

    url = f"https://www.youtube.com/playlist?list={playlist_id}"
    try:
        with YoutubeDL(ydl_options(local_cookie_options(), noplaylist=False,
                                   extract_flat="in_playlist", ignoreerrors=True)) as downloader:
            info = downloader.extract_info(url, download=False)
            tracks = []
            for entry in (info or {}).get("entries") or []:
                if not entry or not VIDEO_ID.fullmatch(entry.get("id") or ""):
                    continue
                video_id = entry["id"]
                tracks.append({"url": f"https://www.youtube.com/watch?v={video_id}",
                               "title": entry.get("title") or video_id})
    except (DownloadError, ExtractorError) as error:
        raise DownloadFailure(str(error)) from error
    if not tracks:
        raise RequestError("The playlist has no available videos.")
    return tracks
