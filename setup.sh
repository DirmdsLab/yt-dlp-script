#!/usr/bin/env bash
set -euo pipefail

source /etc/os-release
if [[ "${ID:-}" != ubuntu ]] || ! dpkg --compare-versions "${VERSION_ID:-0}" ge 24.04; then
    printf 'This setup requires Ubuntu 24.04 or newer.\n' >&2
    exit 1
fi

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
python="${YT_PYTHON:-python3}"

as_root=()
if (( EUID != 0 )); then
    as_root=(sudo)
fi

"${as_root[@]}" apt-get update
"${as_root[@]}" apt-get install -y --no-install-recommends \
    python3 python3-pip ffmpeg ca-certificates curl iproute2 jq unzip

deno_version="$(deno --version 2>/dev/null | awk 'NR == 1 {print $2}' || true)"
node_version="$(node --version 2>/dev/null || true)"
if ! dpkg --compare-versions "${deno_version:-0}" ge 2.3.0 \
    && ! dpkg --compare-versions "${node_version#v}" ge 22.0.0; then
    case "$(dpkg --print-architecture)" in
        amd64) target=x86_64-unknown-linux-gnu ;;
        arm64) target=aarch64-unknown-linux-gnu ;;
        *)
            printf 'Install a supported Deno or Node.js runtime for this architecture.\n' >&2
            exit 1
            ;;
    esac

    temp_dir="$(mktemp -d)"
    trap 'rm -rf -- "$temp_dir"' EXIT
    archive="deno-$target.zip"
    release_url="https://github.com/denoland/deno/releases/latest/download"
    curl --proto '=https' -fsSL --retry 3 "$release_url/$archive" -o "$temp_dir/$archive"
    curl --proto '=https' -fsSL --retry 3 "$release_url/$archive.sha256sum" -o "$temp_dir/checksum"
    checksum="$(awk 'NR == 1 {print $1}' "$temp_dir/checksum")"
    if [[ ! "$checksum" =~ ^[a-fA-F0-9]{64}$ ]] \
        || ! (cd -- "$temp_dir" && printf '%s  %s\n' "$checksum" "$archive" | sha256sum --check --status); then
        printf 'Deno release checksum verification failed.\n' >&2
        exit 1
    fi
    unzip -q "$temp_dir/$archive" deno -d "$temp_dir"
    "${as_root[@]}" install -D -m 0755 "$temp_dir/deno" /usr/local/bin/deno
fi

if ! "$python" -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
    printf 'Python 3.11 or newer is required.\n' >&2
    exit 1
fi

"$python" -m pip install --break-system-packages --no-cache-dir --upgrade \
    -r "$script_dir/requirements.txt"

printf 'Setup complete. Start the server with %s server.py\n' "$python"
