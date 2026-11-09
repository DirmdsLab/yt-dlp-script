"""Expose only the files produced by a recent browser download."""

from collections import OrderedDict
from contextlib import ExitStack
from dataclasses import dataclass
import os
from pathlib import Path
from secrets import token_urlsafe
from stat import S_ISREG
from threading import Lock
from unicodedata import normalize
from urllib.parse import quote
from zipfile import ZIP_STORED, ZipFile

from flask import Blueprint, Response, abort, send_file, url_for
from werkzeug.http import dump_options_header

browser_files = Blueprint("browser_files", __name__)
JOB_LIMIT = 64
CHUNK_SIZE = 1024 * 1024
jobs = OrderedDict()
jobs_lock = Lock()


@dataclass(frozen=True)
class SavedFile:
    path: Path
    device: int
    inode: int
    size: int
    modified_ns: int

    def open(self):
        descriptor = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            metadata = os.fstat(descriptor)
            identity = (metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns)
            if (not S_ISREG(metadata.st_mode)
                    or identity != (self.device, self.inode, self.size, self.modified_ns)):
                raise OSError("The saved file has changed.")
            return os.fdopen(descriptor, "rb")
        except BaseException:
            os.close(descriptor)
            raise


def download_links(result, root):
    if not result.get("directory") or not result.get("files"):
        return None

    directory = Path(result["directory"])
    try:
        canonical = directory.resolve(strict=True)
        if canonical.parent != root.resolve() or directory.is_symlink():
            return None
    except (OSError, RuntimeError):
        return None

    entries = []
    for filename in result["files"]:
        path = Path(filename)
        if path.parent != directory:
            continue
        path = canonical / path.name
        try:
            metadata = path.lstat()
        except OSError:
            continue
        if S_ISREG(metadata.st_mode):
            entries.append(SavedFile(path, metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns))
    if not entries:
        return None

    token = token_urlsafe(24)
    with jobs_lock:
        jobs[token] = (canonical.name, tuple(entries))
        if len(jobs) > JOB_LIMIT:
            jobs.popitem(last=False)

    return {
        "archive": url_for("browser_files.archive", token=token),
        "files": [{"name": entry.path.name, "size": entry.size,
                   "url": url_for("browser_files.file", token=token, index=index)}
                  for index, entry in enumerate(entries)],
    }


def get_job(token):
    with jobs_lock:
        job = jobs.get(token)
        if job is None:
            abort(404)
        jobs.move_to_end(token)
    return job


class ZipBuffer:
    """An unseekable writer that retains only the next response chunk."""

    def __init__(self):
        self.chunks = []

    def write(self, data):
        if data:
            self.chunks.append(data)
        return len(data)

    def flush(self):
        pass

    def drain(self):
        chunks, self.chunks = self.chunks, []
        return chunks


def archive_chunks(folder, files, stack):
    buffer = ZipBuffer()
    with stack:
        with ZipFile(buffer, "w", compression=ZIP_STORED) as archive:
            for entry, source in files:
                with archive.open(f"{folder}/{entry.path.name}", "w", force_zip64=True) as target:
                    while chunk := source.read(CHUNK_SIZE):
                        target.write(chunk)
                        yield from buffer.drain()
                yield from buffer.drain()
        yield from buffer.drain()


@browser_files.get("/results/<token>/files/<int:index>")
def file(token, index):
    _, entries = get_job(token)
    if index >= len(entries):
        abort(404)
    entry = entries[index]
    try:
        source = entry.open()
    except OSError:
        abort(404)
    try:
        response = send_file(source, as_attachment=True, download_name=entry.path.name, conditional=False)
    except BaseException:
        source.close()
        raise
    response.content_length = entry.size
    response.cache_control.no_store = True
    return response


@browser_files.get("/results/<token>/archive.zip")
def archive(token):
    folder, entries = get_job(token)
    stack = ExitStack()
    try:
        files = [(entry, stack.enter_context(entry.open())) for entry in entries]
    except OSError:
        stack.close()
        abort(404)

    name = f"{folder}.zip"
    fallback = normalize("NFKD", folder).encode("ascii", errors="ignore").decode() or "download"
    disposition = dump_options_header("attachment", {
        "filename": f"{fallback}.zip", "filename*": f"UTF-8''{quote(name, safe='')}",
    })
    response = Response(archive_chunks(folder, files, stack), mimetype="application/zip", headers={
        "Content-Disposition": disposition, "Cache-Control": "no-store",
    })
    response.call_on_close(stack.close)
    return response
