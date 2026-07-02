#!/usr/bin/env python3

from flask import Flask, jsonify, render_template, Response
import subprocess
import json

app = Flask(__name__)

PORT = 5015

SCRIPT = "yt-download.sh"


def human_size(size):

    if not size:
        return None

    size = float(size)

    for unit in ["B", "KiB", "MiB", "GiB", "TiB"]:

        if size < 1024:
            return f"{size:.2f} {unit}"

        size /= 1024

    return f"{size:.2f} PiB"


def parse_formats(data):

    audio = []
    video = []
    combined = []

    for f in data.get("formats", []):

        item = {
            "id": f.get("format_id"),
            "ext": f.get("ext"),
            "size": human_size(
                f.get("filesize")
                or f.get("filesize_approx")
            ),
            "protocol": f.get("protocol")
        }

        # Audio Only
        if f.get("vcodec") == "none":

            item.update({
                "codec": f.get("acodec"),
                "abr": f.get("abr"),
                "asr": f.get("asr")
            })

            audio.append(item)

        # Video Only
        elif f.get("acodec") == "none":

            item.update({

                "resolution": (
                    f"{f.get('width')}x{f.get('height')}"
                    if f.get("width") and f.get("height")
                    else None
                ),

                "width": f.get("width"),
                "height": f.get("height"),
                "fps": f.get("fps"),
                "codec": f.get("vcodec"),
                "vbr": f.get("vbr")

            })

            video.append(item)

        # Progressive
        else:

            item.update({

                "resolution": (
                    f"{f.get('width')}x{f.get('height')}"
                    if f.get("width") and f.get("height")
                    else None
                ),

                "width": f.get("width"),
                "height": f.get("height"),
                "fps": f.get("fps"),
                "video_codec": f.get("vcodec"),
                "audio_codec": f.get("acodec"),
                "abr": f.get("abr")

            })

            combined.append(item)

    audio.sort(
        key=lambda x: x.get("abr") or 0,
        reverse=True
    )

    video.sort(
        key=lambda x: (
            x.get("height") or 0,
            x.get("fps") or 0
        ),
        reverse=True
    )

    combined.sort(
        key=lambda x: (
            x.get("height") or 0,
            x.get("fps") or 0
        ),
        reverse=True
    )

    return audio, video, combined


@app.route("/")
def home():

    return render_template("index.html")


@app.route("/url/<path:url>", methods=["POST"])
def formats(url):

    try:

        result = subprocess.run(
            [
                "yt-dlp",
                "-J",
                url
            ],
            capture_output=True,
            text=True,
            check=True
        )

        data = json.loads(result.stdout)

        audio, video, combined = parse_formats(data)

        return jsonify({

            "success": True,

            "url": url,

            "id": data.get("id"),

            "title": data.get("title"),

            "uploader": data.get("uploader"),

            "duration": data.get("duration"),

            "thumbnail": data.get("thumbnail"),

            "view_count": data.get("view_count"),

            "upload_date": data.get("upload_date"),

            "audio": audio,

            "video": video,

            "combined": combined

        })

    except subprocess.CalledProcessError as e:

        return jsonify({

            "success": False,

            "error": e.stderr

        }), 500

    except Exception as e:

        return jsonify({

            "success": False,

            "error": str(e)

        }), 500


@app.route("/download/<codes>/<path:url>")
def download(codes, url):

    def generate():

        process = subprocess.Popen(

            [
                SCRIPT,
                codes,
                url
            ],

            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1

        )

        for line in iter(process.stdout.readline, ""):

            yield f"data: {line.rstrip()}\n\n"

        process.stdout.close()

        process.wait()

        yield "event: finish\ndata: DONE\n\n"

    return Response(
        generate(),
        mimetype="text/event-stream"
    )


if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=PORT,
        debug=False,
        threaded=True
    )