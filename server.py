#!/usr/bin/env python3
"""Local web interface and JSON API for YouTube downloads."""

from collections import OrderedDict
from hashlib import sha256
import json
import os
from queue import Empty, Queue
from threading import Event, Lock, Thread
from time import monotonic

from flask import Flask, Response, jsonify, render_template, request, stream_with_context

from browser_files import browser_files, download_links
from downloader import (
    CONTAINERS, MODES, DownloadFailure, RequestError, Selection, describe_video,
    download, download_root, inspect_video, local_cookie_options, split_pair, youtube_url,
)

app = Flask(__name__)
app.register_blueprint(browser_files)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024
PORT = 7010
PROGRESS_MIME = "application/x-ndjson"
CACHE_TTL = 300
CACHE_LIMIT = 16
metadata_cache = OrderedDict()
cache_lock = Lock()


def cookie_log(extra):
    return "Cookie: enabled (cookie.txt)" if extra.get("cookiefile") else "Cookie: disabled"


def get_info(url, extra, refresh=False, log=None):
    cookiefile = extra.get("cookiefile")
    cookie_key = sha256(cookiefile.getvalue().encode("utf-8")).digest() if cookiefile else None
    key = (url, cookie_key)
    with cache_lock:
        cached = metadata_cache.get(key)
        if not refresh and cached and monotonic() - cached[0] < CACHE_TTL:
            metadata_cache.move_to_end(key)
            if log:
                log("Metadata: reused cached video information")
            return cached[1]

    if log:
        log(f"yt-dlp API: inspect {url}")
    info = inspect_video(url, extra=extra)
    with cache_lock:
        metadata_cache[key] = (monotonic(), info)
        metadata_cache.move_to_end(key)
        if len(metadata_cache) > CACHE_LIMIT:
            metadata_cache.popitem(last=False)
    return info


def download_for_request(url, selection, log=None, **callbacks):
    extra = local_cookie_options()
    if log:
        log(cookie_log(extra))
    return download(get_info(url, extra, log=log), selection, extra=extra, log=log, **callbacks)


def download_error_details(error):
    return {"success": False, "error": str(error),
            "directory": str(error.folder) if error.folder else None,
            "files": [str(path) for path in error.files]}


def download_events(url, selection, browser_links):
    events = Queue()
    disconnected = Event()

    def emit(event_type, **data):
        if not disconnected.is_set():
            events.put({"type": event_type, **data})

    def work():
        start = monotonic()
        try:
            result = download_for_request(
                url, selection, progress=lambda event: emit("progress", **event),
                report=lambda message: emit("status", message=message),
                log=lambda message: emit("log", message=message),
            )
            outcome = {"success": True, **result}
        except DownloadFailure as error:
            outcome = download_error_details(error)
        except RequestError as error:
            outcome = {"success": False, "error": str(error)}
        except Exception:
            app.logger.exception("Download job failed")
            outcome = {"success": False, "error": "The download failed unexpectedly."}
        emit("result", elapsed=monotonic() - start, **outcome)

    Thread(target=work, daemon=True).start()
    try:
        yield json.dumps({"type": "status", "message": "Preparing download…"}) + "\n"
        while True:
            try:
                event = events.get(timeout=10)
            except Empty:
                yield "\n"
                continue
            if browser_links and event["type"] == "result":
                event["downloads"] = download_links(event, download_root())
            yield json.dumps(event, ensure_ascii=False) + "\n"
            if event["type"] == "result":
                break
    finally:
        disconnected.set()


def wants_progress():
    return any(mime == PROGRESS_MIME and quality > 0 for mime, quality in request.accept_mimetypes)


def progress_response(url, selection, *, browser_links=False):
    return Response(stream_with_context(download_events(url, selection, browser_links)),
                    mimetype=PROGRESS_MIME, headers={
                        "Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no",
                    })


def json_body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise RequestError("Send a JSON object with Content-Type: application/json.")
    return data


def text_field(data, name, default=""):
    value = data.get(name, default)
    if not isinstance(value, str):
        raise RequestError(f"{name} must be a string.")
    return value.strip()


def web_selection(data):
    mode = text_field(data, "mode", "full")
    container = text_field(data, "container", "mp4").lower()
    audio, video = text_field(data, "audio"), text_field(data, "video")
    subtitle = text_field(data, "sub")
    thumbnail = data.get("thumbnail", False)
    if mode not in MODES:
        raise RequestError("Choose a valid download mode.")
    if container not in CONTAINERS:
        raise RequestError("Choose mp4, mkv or webm.")
    if not isinstance(thumbnail, bool):
        raise RequestError("thumbnail must be a boolean.")
    if ((mode in ("full", "audio") and not audio)
            or (mode in ("full", "video") and not video)):
        raise RequestError("Choose the audio or video format codes for this download.")
    return Selection(mode, audio, video, container, subtitle, thumbnail)


@app.errorhandler(RequestError)
def invalid_request(error):
    return jsonify(success=False, error=str(error)), 400


@app.errorhandler(DownloadFailure)
def failed_download(error):
    return jsonify(**download_error_details(error)), 502


@app.errorhandler(413)
def request_too_large(error):
    return jsonify(success=False, error="The request body is too large."), 413


@app.get("/")
def home():
    return render_template("index.html")


@app.post("/api/formats")
def formats():
    url = youtube_url(json_body().get("url"))
    extra = local_cookie_options()
    logs = [cookie_log(extra)]
    try:
        info = get_info(url, extra, refresh=True, log=logs.append)
    except DownloadFailure as error:
        return jsonify(**download_error_details(error), logs=logs), 502
    except RequestError as error:
        return jsonify(success=False, error=str(error), logs=logs), 400
    return jsonify(success=True, url=url, logs=logs, **describe_video(info))


@app.post("/api/download")
def web_download():
    data = json_body()
    selection = web_selection(data)
    url = youtube_url(data.get("url"))
    if wants_progress():
        return progress_response(url, selection, browser_links=True)
    result = download_for_request(url, selection)
    return jsonify(success=True, **result)


@app.post("/save")
def save():
    data = json_body()
    audio, video = split_pair(data.get("resolution"))
    subtitle = "" if data.get("sub") is None else text_field(data, "sub")
    selection = Selection(audio=audio, video=video, subtitle=subtitle, thumbnail=True)
    url = youtube_url(data.get("url"))
    if wants_progress():
        return progress_response(url, selection)
    result = download_for_request(url, selection)
    return jsonify(success=True, **result)


if __name__ == "__main__":
    app.run(host=os.environ.get("YT_HOST", "0.0.0.0"), port=PORT, threaded=True)
