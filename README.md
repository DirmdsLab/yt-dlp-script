# YouTube Downloader

Download the selected audio and video formats separately, then merge them with FFmpeg. A full download keeps both raw streams alongside the merged video. Subtitles and thumbnails are saved as separate files.

## Setup

Run setup inside an Ubuntu 24.04 or newer container. It installs FFmpeg, FFprobe and Python, and adds Deno if no compatible JavaScript runtime is available. The Fish client helper uses Python 3, `curl`, and `jq`. It uses `ip` to detect the gateway when no server URL is configured.

```sh
./setup.sh
```

Setup installs Flask and yt-dlp into the container's Python environment. Run it again to update yt-dlp if YouTube changes its extractor requirements.

## CLI

```sh
./yt-download.sh 'https://youtu.be/VIDEO_ID'
./yt-download.sh --detail 'https://youtu.be/VIDEO_ID'
./yt-download.sh 251+399 'https://youtu.be/VIDEO_ID'
./yt-download.sh justsub=en 'https://youtu.be/VIDEO_ID'
```

- URL only lists the formats, prompts for audio and video codes, and creates an MP4.
- `--detail` also prompts for MP4, MKV or WebM, then lists subtitle languages and asks whether to include a thumbnail. Enter a blank subtitle code to skip it.
- `AUDIO+VIDEO` selects the two exact format codes and creates an MP4 immediately. Format availability varies by video.
- `justsub=LANG` downloads only that exact subtitle language. Manual and automatic captions are supported. A manual track takes precedence when both have the same code.
- `--just-audio`, `--just-video`, and `--just-thumbnail` download a single item. For a direct full download, add `--sub=en` and/or `--thumbnail=yes`.
- `--forward="--cookies /path/to/cookies.txt"` forwards yt-dlp extraction and network settings to the CLI. `--forward="--list-subs"` and `--forward="--list-formats"` list metadata and exit.

Place a Netscape-format `cookie.txt` beside `yt-download.sh` and `server.py` to use cookies automatically for CLI, web and API requests. A missing, unreadable, malformed or fully expired file is ignored. An explicit CLI cookie option takes precedence.

Progressive formats can be selected as audio or video sources. FFmpeg separates the requested track before the final merge. Compatible tracks are copied into the output. When MP4 or WebM needs a different codec, FFmpeg encodes the affected track in the final video while preserving the raw files.

The CLI shows separate terminal bars for audio, video and the selected output container. Available transfer sizes, speed, and estimated time remaining appear while each step runs. Non-interactive sessions receive plain text progress updates.

## Web server

```sh
python3 server.py
```

Open <http://localhost:7010> or use the server machine's LAN IP and port `7010`. The server listens on all interfaces by default and is intended for a trusted local network. Set `YT_HOST=127.0.0.1` to limit access to the same machine.

Search for a YouTube URL, pick the stream codes and output container, and add optional subtitles or a thumbnail. The individual buttons save only the selected item. The web page reports the files saved on the server.

The format list shows exact or estimated sizes where available, and the package summary combines the selected downloads. During a job the web page shows progress for audio, video and the selected output container. Transfer speed and estimated time remaining appear when the downloader can calculate them.

The collapsed command log shows the yt-dlp operations, FFmpeg and FFprobe commands, and whether the server loaded `cookie.txt`. After a download, the results panel offers the generated files individually or as a ZIP containing that job's folder. Browser links are kept for recent web downloads while the server is running. The original files stay in the download directory.

## Download over HTTP

`POST /save` accepts a JSON object. Pass the format pair in `resolution` and the optional language in `sub`. A request creates an MP4 and downloads the thumbnail. The response arrives when the download finishes and includes the output paths. The legacy `saved` field may be omitted.

```sh
curl --fail-with-body http://localhost:7010/save \
  -H 'Content-Type: application/json' \
  --data '{"url":"https://youtu.be/VIDEO_ID","resolution":"251+399","sub":"en"}'
```

For Fish, source the provided function to stream the server's progress to the same terminal display. Set `YT_SERVER` to the server URL if it is running somewhere other than your default gateway. Keep `terminal_progress.py` beside `setyt.fish` on the client machine.

```fish
source ./setyt.fish
set -gx YT_SERVER http://localhost:7010
setyt 251+399 'https://youtu.be/VIDEO_ID' en
setyt 251+399 'https://youtu.be/VIDEO_ID'
```

## Output location

All entry points use `$HOME/Downloads` on the machine performing the download. Each request gets a separate folder named after the video title. If that name is already in use, the new folder receives a number. A full MP4 download can contain the following files.

```text
Video title/
  Video title_audioraw.webm
  Video title_videoraw.mp4
  Video title.mp4
  Video title_sub.vtt
  Video title_thumbnail.jpg
```

Raw extensions follow the chosen streams. Set `YT_DOWNLOAD_DIR` to an absolute path to override the default. For instance, run `YT_DOWNLOAD_DIR="$PWD" python3 server.py` from the project folder to save beside the script.

## Existing JSON queues

`./jsonytdownload.sh yt.json` processes the existing array-based queue. Add `autokeep` to move failed items to `keep.json` and continue. The legacy values `720`, `1080`, and `4k` are still recognized by the queue helper. New queue entries can store an explicit format pair in `resolution`.
