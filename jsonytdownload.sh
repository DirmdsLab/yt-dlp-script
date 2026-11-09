#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 || $# -gt 2 || ( $# -eq 2 && "$2" != autokeep ) ]]; then
    printf 'Usage: %s JSON_FILE [autokeep]\n' "${0##*/}" >&2
    exit 2
fi

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
queue_file="$1"
keep_file="$(dirname -- "$queue_file")/keep.json"
if [[ ! -f "$queue_file" ]] || ! command -v jq >/dev/null; then
    printf 'An existing JSON queue and jq are required.\n' >&2
    exit 1
fi
if [[ "$queue_file" -ef "$keep_file" ]]; then
    printf 'The queue file must be different from keep.json.\n' >&2
    exit 1
fi
if ! jq -e 'type == "array" and all(.[]; type == "object" and (.url | type == "string"))' "$queue_file" >/dev/null; then
    printf 'The queue must be a JSON array of objects with a URL.\n' >&2
    exit 1
fi

temporary="$(mktemp -- "$(dirname -- "$queue_file")/.yt-queue.XXXXXX")"
trap 'rm -f -- "$temporary"' EXIT

advance() {
    if [[ "$1" == keep ]]; then
        if [[ ! -e "$keep_file" ]]; then
            printf '[]\n' > "$keep_file"
        fi
        jq --argjson item "$item" '
            if type != "array" then error("keep.json must be an array")
            elif index($item) != null then .
            else . + [$item] end
        ' "$keep_file" > "$temporary"
        mv -- "$temporary" "$keep_file"
    fi

    jq '.[1:]' "$queue_file" > "$temporary"
    mv -- "$temporary" "$queue_file"
}

while [[ "$(jq length "$queue_file")" -gt 0 ]]; do
    item="$(jq -c '.[0]' "$queue_file")"
    url="$(jq -r '.url' <<< "$item")"
    pair="$(jq -r '.resolution // ""' <<< "$item")"
    subtitle="$(jq -r '.sub // ""' <<< "$item")"

    case "$pair" in
        4k) pair=251+401 ;;
        1080) pair=251+399 ;;
        720) pair=251+398 ;;
    esac

    options=(--thumbnail=yes)
    if [[ -n "$subtitle" ]]; then
        options+=("--sub=$subtitle")
    fi

    printf '\nDownloading %s (%s)\n' "$url" "${pair:-interactive}"
    success=no
    if [[ -n "$pair" ]]; then
        if "$script_dir/yt-download.sh" "$pair" "$url" "${options[@]}"; then
            success=yes
        fi
    elif [[ "${2:-}" != autokeep ]]; then
        if "$script_dir/yt-download.sh" --detail "$url" "${options[@]}"; then
            success=yes
        fi
    fi

    if [[ "${2:-}" == autokeep ]]; then
        if [[ "$success" == yes ]]; then
            advance next
        else
            advance keep
        fi
        continue
    fi

    while true; do
        if ! read -r -p '[n] Next  [k] Keep  [r] Retry  [e] Exit: ' answer; then
            exit 0
        fi
        case "$answer" in
            n|N) advance next; break ;;
            k|K) advance keep; break ;;
            r|R) break ;;
            e|E) exit 0 ;;
            *) printf 'Choose n, k, r or e.\n' ;;
        esac
    done
done

printf 'Queue is empty.\n'
