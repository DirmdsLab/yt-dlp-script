#!/usr/bin/env bash

YTSCRIPT="yt-download.sh"
PWDYT="$HOME/Downloads"

sed -i "s|outdir=\"HereChange\"|outdir=\"$PWDYT\"|" "$YTSCRIPT"