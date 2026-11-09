"""Render shared download events in a terminal or a plain text log."""

import json
from math import ceil
import os
import sys
from threading import Event, Lock, Thread
from time import monotonic
from unicodedata import combining, east_asian_width


def format_bytes(size):
    size = float(size)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024:
            return f"{size:.0f} B" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TiB"


def format_time(seconds):
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {seconds:02d}s"
    if minutes:
        return f"{minutes}m {seconds:02d}s"
    return f"{seconds}s"


def clean_line(value):
    return "".join(character if character.isprintable() else " " for character in str(value))


def fit_line(text, width):
    used = 0
    for index, character in enumerate(text):
        used += 0 if combining(character) else 2 if east_asian_width(character) in ("W", "F") else 1
        if used > width:
            return text[:index]
    return text


class TerminalProgress:
    """Keep a row per stage and animate it while downloads are active."""

    def __init__(self, mode, container, stream=None):
        self.stream = sys.stderr if stream is None else stream
        self.live = self.stream.isatty() and os.environ.get("TERM") != "dumb"
        self.color = self.live and "NO_COLOR" not in os.environ
        try:
            "━─⠋".encode(self.stream.encoding or "ascii")
            self.fill, self.empty, self.spinner = "━", "─", "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        except UnicodeError:
            self.fill, self.empty, self.spinner = "=", "-", "|/-\\"

        labels = {"audio": "Audio", "video": "Video", "merge": f"Merge {container.upper()}"}
        stages = ("audio", "video", "merge") if mode == "full" else (mode,)
        self.labels = {stage: labels[stage] for stage in stages if stage in labels}
        self.rows = {stage: {"state": "waiting", "percent": 0} for stage in self.labels}
        self.status_text = "Preparing download..."
        self.started = monotonic()
        self.elapsed = None
        self.saved = None
        self.success = None
        self.line_count = 0
        self.frame = 0
        self.lock = Lock()
        self.stop = Event()
        self.thread = None

    def __enter__(self):
        if self.live:
            self.draw()
            self.thread = Thread(target=self.animate, daemon=True)
            self.thread.start()
        return self

    def __exit__(self, error_type, error, traceback):
        self.stop.set()
        if self.thread:
            self.thread.join()
        if self.success is None:
            self.finish(False)
        if self.live:
            self.draw()
        else:
            print(self.summary(), file=self.stream, flush=True)

    def animate(self):
        while not self.stop.wait(0.12):
            with self.lock:
                self.frame += 1
                self.draw()

    def status(self, message):
        with self.lock:
            self.status_text = clean_line(message)
            if not self.live:
                print(self.status_text, file=self.stream, flush=True)

    def update(self, event):
        stage = event.get("stage")
        if stage not in self.rows:
            return
        with self.lock:
            previous = self.rows[stage]
            self.rows[stage] = event
            if not self.live:
                before, after = previous.get("percent"), event.get("percent")
                milestone = isinstance(after, (int, float)) and after >= 25 and (
                    before is None or int(after) // 25 > int(before) // 25
                )
                if event.get("state") == "done" or milestone:
                    print(self.render_row(stage, 18), file=self.stream, flush=True)

    def finish(self, success, *, elapsed=None, saved=None):
        with self.lock:
            self.success = success
            self.elapsed = monotonic() - self.started if elapsed is None else elapsed
            self.saved = saved
            self.status_text = "Files saved." if success else "Download stopped."
            for stage, row in self.rows.items():
                if row["state"] == "done":
                    continue
                if success:
                    self.rows[stage] = {**row, "state": "done", "percent": 100}
                else:
                    state = "skipped" if row["state"] == "waiting" else "failed"
                    self.rows[stage] = {**row, "state": state}

    def summary(self):
        elapsed = monotonic() - self.started if self.elapsed is None else self.elapsed
        title = "YouTube download" if self.success is None else "Download complete" if self.success else "Download failed"
        details = [title, f"Elapsed {format_time(elapsed)}"]
        transferred = [self.rows[stage].get("bytes") for stage in ("audio", "video") if stage in self.rows]
        if any(size is not None for size in transferred):
            details.append(f"Transferred {format_bytes(sum(size or 0 for size in transferred))}")
        if self.saved is not None:
            details.append(f"Saved {format_bytes(self.saved)}")
        return "  |  ".join(details)

    def render_row(self, stage, bar_width):
        row = self.rows[stage]
        state, percent = row["state"], row.get("percent")
        if percent is None and state == "running":
            marker = self.frame % bar_width
            bar = self.empty * marker + self.fill + self.empty * (bar_width - marker - 1)
        else:
            filled = int((percent or 0) * bar_width / 100)
            bar = self.fill * filled + self.empty * (bar_width - filled)
        percent_label = "--%" if percent is None else f"{int(percent)}%"
        text = f"{self.labels[stage]:<11} [{bar}] {percent_label:>4}"
        details = []
        if state != "running":
            details.append("Shared source reused" if row.get("reused") and state == "done" else state.capitalize())
        if row.get("bytes") is not None and not row.get("reused"):
            size = format_bytes(row["bytes"])
            if row.get("total") and state == "running":
                estimate = "~" if row.get("estimated") else ""
                size += f" / {estimate}{format_bytes(row['total'])}"
            details.append(size)
        if state == "running" and row.get("speed"):
            details.append(f"{format_bytes(row['speed'])}/s")
        if state == "running" and row.get("eta"):
            details.append(f"{format_time(ceil(row['eta']))} left")
        return text + ("  " + "  |  ".join(details) if details else "")

    def draw(self):
        try:
            columns = os.get_terminal_size(self.stream.fileno()).columns or 80
        except OSError:
            columns = 80
        bar_width = max(4, min(24, (columns - 35) // 3))
        lines = [(self.summary(), "1;36")]
        for stage, row in self.rows.items():
            shade = {"waiting": "2", "running": "36", "done": "32", "failed": "31", "skipped": "2"}[row["state"]]
            lines.append((self.render_row(stage, bar_width), shade))
        spinner = self.spinner[self.frame % len(self.spinner)] + " " if self.success is None else ""
        lines.append((spinner + self.status_text, "2"))

        if self.line_count:
            self.stream.write(f"\x1b[{self.line_count}A")
        for line, shade in lines:
            line = fit_line(line, max(1, columns - 1))
            if self.color:
                line = f"\x1b[{shade}m{line}\x1b[0m"
            self.stream.write(f"\r\x1b[2K{line}\n")
        self.stream.flush()
        self.line_count = len(lines)


def show_remote_progress():
    result = None
    error = None
    with TerminalProgress("full", "mp4") as display:
        try:
            for line in sys.stdin:
                if not line.strip():
                    continue
                event = json.loads(line)
                event_type = event.get("type")
                if event_type == "status":
                    display.status(event["message"])
                elif event_type == "progress":
                    display.update(event)
                elif event_type == "result" or "success" in event:
                    result = event
            if result is None:
                error = "The server connection closed before a download result was received."
        except (AttributeError, ValueError):
            error = "The server returned an invalid download response."
        if result is not None and error is None:
            display.finish(result.get("success", False), elapsed=result.get("elapsed"), saved=result.get("saved_bytes"))

    if error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    if not result.get("success"):
        print(f"Error: {clean_line(result.get('error', 'The download failed.'))}", file=sys.stderr)
        for path in result.get("files") or []:
            print(f"Kept {clean_line(path)}", file=sys.stderr)
        return 1
    for path in result.get("files") or []:
        print(f"Saved {clean_line(path)}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(show_remote_progress())
    except KeyboardInterrupt:
        print("\nDownload cancelled.", file=sys.stderr)
        sys.exit(130)
