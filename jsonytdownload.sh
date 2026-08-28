#!/usr/bin/env bash

set -euo pipefail

if [ $# -lt 1 ] || [ $# -gt 2 ]; then
    echo "Usage: $0 <json-file> [autokeep]"
    exit 1
fi

JSON_FILE="$1"

AUTO_KEEP=false
if [ $# -eq 2 ] && [ "$2" = "autokeep" ]; then
    AUTO_KEEP=true
fi

KEEP_FILE="$(dirname "$JSON_FILE")/keep.json"

if [ ! -f "$KEEP_FILE" ]; then
    echo "[]" > "$KEEP_FILE"
fi

if [ ! -f "$JSON_FILE" ]; then
    echo "JSON tidak ditemukan:"
    echo "$JSON_FILE"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
YTDLP_SCRIPT="$SCRIPT_DIR/yt-download.sh"

if [ ! -x "$YTDLP_SCRIPT" ]; then
    echo "Script tidak ditemukan atau tidak executable:"
    echo "$YTDLP_SCRIPT"
    exit 1
fi

next_item() {

    TMP=$(mktemp)

    jq '.[1:]' "$JSON_FILE" > "$TMP"

    mv "$TMP" "$JSON_FILE"

}

keep_item() {

    TMP_KEEP=$(mktemp)

    jq --slurpfile item <(jq '.[0]' "$JSON_FILE") \
        '. + $item' "$KEEP_FILE" > "$TMP_KEEP"

    mv "$TMP_KEEP" "$KEEP_FILE"

    next_item

}

while true
do

    TOTAL=$(jq length "$JSON_FILE")

    if [ "$TOTAL" -eq 0 ]; then
        echo
        echo "========================================"
        echo "Semua download selesai."
        echo "Queue kosong."
        echo "========================================"
        exit 0
    fi

    URL=$(jq -r '.[0].url' "$JSON_FILE")
    SUB=$(jq -r '.[0].sub' "$JSON_FILE")
    RES=$(jq -r '.[0].resolution' "$JSON_FILE")

    FORMAT=""

    case "$RES" in
        4k)
            FORMAT="251+401"
            ;;
        1080)
            FORMAT="251+399"
            ;;
        720)
            FORMAT="251+398"
            ;;
        *)
            FORMAT=""
            ;;
    esac

    echo
    echo "========================================"
    echo "Sisa Queue : $TOTAL"
    echo
    echo "URL : $URL"
    echo "RES : ${RES:-none}"
    echo "SUB : ${SUB:-none}"
    echo "========================================"
    echo

    LOG=$(mktemp)

    # Download video
    if [ -n "$FORMAT" ]; then
        "$YTDLP_SCRIPT" "$FORMAT" "$URL" 2>&1 | tee "$LOG"
    else
        "$YTDLP_SCRIPT" "$URL" 2>&1 | tee "$LOG"
    fi

    # Download subtitle
    if [ -n "$SUB" ]; then
        "$YTDLP_SCRIPT" "$URL" "--just-sub=$SUB" 2>&1 | tee -a "$LOG"
    fi

    # Download thumbnail
    "$YTDLP_SCRIPT" "$URL" "--just-thumbnail" 2>&1 | tee -a "$LOG"

    if $AUTO_KEEP; then

        if grep -Eq \
            "Requested format is not available|HTTP Error 403|Error opening input files|Error opening input file|No such file or directory" \
            "$LOG"
        then
            echo
            echo "⚠ Error terdeteksi. Auto Keep."

            keep_item
        else
            echo
            echo "✓ Download berhasil. Auto Next."

            next_item
        fi

        rm -f "$LOG"

        continue

    fi

    rm -f "$LOG"

    while true
    do
        echo
        read -rp "[n] next  [k] keep  [r] retry  [e] exit : " ANSWER

        case "$ANSWER" in

            n|N)

                next_item

                echo
                echo "✓ Item selesai. Queue diperbarui."

                break
                ;;

            k|K)

                keep_item

                echo
                echo "✓ Item dipindahkan ke keep.json."

                break
                ;;

            r|R)

                echo
                echo "↻ Mengulang item yang sama..."

                break
                ;;

            e|E)

                echo
                echo "Exit."

                exit 0
                ;;

            *)

                echo "Pilihan tidak valid."

                ;;

        esac

    done

done
