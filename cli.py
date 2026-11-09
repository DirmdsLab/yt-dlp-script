"""Interactive and direct download modes for yt-download.sh."""

import argparse
from dataclasses import replace
import logging
import shlex
import sys

from yt_dlp import YoutubeDL, parse_options

from downloader import (
    CONTAINERS, DownloadFailure, RequestError, Selection, download, inspect_video,
    local_cookie_options, split_pair, subtitles,
)
from terminal_progress import TerminalProgress

# Forward extractor and network settings while keeping output and stream selection managed.
FORWARD_SETTINGS = (
    "cookiefile", "cookiesfrombrowser", "proxy", "geo_verification_proxy",
    "http_headers", "extractor_args", "js_runtimes", "remote_components", "socket_timeout",
    "source_address", "impersonate", "retries", "fragment_retries", "extractor_retries",
    "nocheckcertificate", "username", "password", "usenetrc", "netrc_location",
    "videopassword", "http_chunk_size",
)


def parse_cli():
    parser = argparse.ArgumentParser(
        prog="yt-download.sh",
        description="Download separate YouTube streams and merge them locally.",
        epilog="Examples: yt-download.sh 251+399 URL | yt-download.sh justsub=en URL",
    )
    parser.add_argument("input", nargs="+", metavar="URL_OR_SELECTOR")
    parser.add_argument("--detail", action="store_true", help="Prompt for container, subtitles and thumbnail.")
    parser.add_argument("--just-audio", action="store_true", help="Download the best audio only.")
    parser.add_argument("--just-video", action="store_true", help="Download the best video only.")
    parser.add_argument("--just-sub", metavar="LANG", help="Download only this subtitle.")
    parser.add_argument("--just-thumbnail", action="store_true", help="Download only the thumbnail.")
    parser.add_argument("--sub", metavar="LANG", help="Add a subtitle to a direct download.")
    parser.add_argument("--thumbnail", choices=("yes", "no"), help="Add a thumbnail to a direct download.")
    parser.add_argument("--forward", action="append", default=[], metavar="OPTIONS",
                        help="Forward yt-dlp extractor or network options, such as cookies.")
    return parser.parse_intermixed_args()


def forwarded_options(groups):
    if not groups:
        return {}, False, False
    arguments = [arg for group in groups for arg in shlex.split(group)]
    parsed = parse_options(["--ignore-config", *arguments])
    if parsed.urls:
        raise RequestError("Pass the video URL to yt-download.sh, outside --forward.")
    options = {key: parsed.ydl_opts[key] for key in FORWARD_SETTINGS
               if parsed.ydl_opts.get(key) is not None}
    if options.get("js_runtimes") == {"deno": {"path": None}}:
        options.pop("js_runtimes")
    if "no-certifi" in parsed.ydl_opts.get("compat_opts", ()):
        options["compat_opts"] = {"no-certifi"}
    return options, parsed.options.listformats, parsed.options.listsubtitles


def show_formats(info):
    with YoutubeDL({"quiet": False}) as downloader:
        downloader.list_formats(info)


def show_subtitles(info):
    available = subtitles(info)
    if not available:
        print("No subtitles available.")
        return False
    print("Available subtitles:")
    for item in available:
        print(f"  {item['id']:<12} {item['name']} ({item['source']})")
    return True


def prepare_selection(args):
    selector, url = args.input if len(args.input) == 2 else ("", args.input[0])
    if len(args.input) > 2:
        raise RequestError("Provide one URL and at most one selector.")

    language = args.just_sub or ""
    if selector.startswith("justsub="):
        if args.just_sub is not None:
            raise RequestError("Provide only one subtitle selector.")
        language = selector.partition("=")[2]
        if not language:
            raise RequestError("Choose a subtitle language, for example justsub=en.")
        selector = ""

    if not selector and not args.detail:
        language = language or args.sub or ""
    only = [mode for mode, enabled in (
        ("audio", args.just_audio), ("video", args.just_video),
        ("subtitle", language or args.just_sub is not None),
        ("thumbnail", args.just_thumbnail or (not selector and not args.detail and args.thumbnail == "yes")),
    ) if enabled]
    if len(only) > 1 or (only and (selector or args.detail)) or (selector and args.detail):
        raise RequestError("Choose one download mode.")

    if only:
        return url, Selection(mode=only[0], subtitle=language), False
    if selector:
        audio, video = split_pair(selector)
        return url, Selection(audio=audio, video=video, subtitle=args.sub or "",
                              thumbnail=args.thumbnail == "yes"), False
    return url, Selection(subtitle=args.sub or "", thumbnail=args.thumbnail == "yes"), True


def prompt_selection(info, selection, detail):
    show_formats(info)
    audio = input("Audio format code: ").strip()
    video = input("Video format code: ").strip()
    if not audio or not video:
        raise RequestError("Both format codes are required.")
    selection = replace(selection, audio=audio, video=video)
    if not detail:
        return selection

    container = input("Output format [mp4/mkv/webm] (MP4): ").strip().lower() or "mp4"
    if container not in CONTAINERS:
        raise RequestError("Choose mp4, mkv or webm.")
    subtitle = selection.subtitle
    if show_subtitles(info):
        choice = input(f"Subtitle code [{subtitle or 'skip'}]: ").strip()
        subtitle = choice or subtitle
    thumbnail = input("Download thumbnail? [y/N]: ").strip().lower()
    if thumbnail not in ("", "y", "yes", "n", "no"):
        raise RequestError("Enter y or n for the thumbnail.")
    return replace(selection, container=container, subtitle=subtitle,
                   thumbnail=thumbnail in ("y", "yes") if thumbnail else selection.thumbnail)


def main():
    args = parse_cli()
    try:
        url, selection, interactive = prepare_selection(args)
        extra, list_formats, list_subtitles = forwarded_options(args.forward)
        if not (extra.get("cookiefile") or extra.get("cookiesfrombrowser")):
            extra.update(local_cookie_options())
        extra["noprogress"] = True
        print("Reading video information...", flush=True)
        info = inspect_video(url, extra)
        if list_formats or list_subtitles:
            if list_formats:
                show_formats(info)
            if list_subtitles:
                show_subtitles(info)
            return 0
        if interactive:
            selection = prompt_selection(info, selection, args.detail)
        # The terminal display owns stderr while yt-dlp downloads. Exceptions still report failures.
        extra["logger"] = logging.Logger("yt-dlp-terminal", level=logging.CRITICAL + 1)
        with TerminalProgress(selection.mode, selection.container) as display:
            result = download(info, selection, extra, report=display.status, progress=display.update)
            display.finish(True, saved=result.get("saved_bytes"))
        for path in result["files"]:
            print(f"Saved {path}")
        return 0
    except DownloadFailure as error:
        print(f"Error: {error}", file=sys.stderr)
        for path in error.files:
            print(f"Kept {path}", file=sys.stderr)
    except ValueError as error:
        print(f"Error: {error}", file=sys.stderr)
    except (EOFError, KeyboardInterrupt):
        print("\nDownload cancelled.", file=sys.stderr)
        return 130
    return 1


if __name__ == "__main__":
    sys.exit(main())
