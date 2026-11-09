#!/usr/bin/env bash

yt_download() {
    local script_dir python
    script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)" || return
    python="${YT_PYTHON:-$script_dir/.venv/bin/python}"

    if [[ -z "${YT_PYTHON:-}" && ! -x "$python" ]]; then
        python=python3
    fi

    "$python" "$script_dir/cli.py" "$@"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    yt_download "$@"
fi
