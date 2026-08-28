#!/usr/bin/env bash

yt_download() {
    # ============================================================
    # CONFIG
    # ============================================================

    local outdir="${HOME}/Downloads"
    [[ -d "$outdir" ]] || outdir="/data/data/com.termux/files/home/Downloads"
    mkdir -p "$outdir"

    # ============================================================
    # HELP
    # ============================================================

    if [[ "$*" == *"--help"* ]]; then
        cat <<'EOF'
Usage:
  yt-download <url> [OPTIONS]

Modes:
  --just-audio
      Download best audio only

  --just-video
      Download best video only

  --just-sub=<lang>
  --sub=<lang>
      Download subtitle

  --thumbnail=yes
  --just-thumbnail
      Download thumbnail only

  AUDIO+VIDEO <url>
      Direct download + merge

      Example:
        yt-download 251+399 "https://youtu.be/xxxxx"

Forward native yt-dlp arguments:
  --forward="ARGUMENTS"

  Each --forward represents ONE group of yt-dlp arguments.

  Examples:

    One argument:
      --forward="--list-subs"

    Two arguments:
      --forward="--cookies /path/to/cookies.txt"

    Multiple arguments:
      --forward="--user-agent 'Mozilla/5.0'"
      --forward="--playlist-items 1-5"

    More complex:
      --forward="--extractor-args 'youtube:player_client=android'"
      --forward="--match-filter 'duration < 600'"

  Every --forward group is parsed into separate arguments
  before being passed to yt-dlp.

Examples:

  yt-download "URL" \
    --forward="--list-subs"

  yt-download "URL" \
    --forward="--cookies $HOME/cookies.txt"

  yt-download "URL" \
    --forward="--cookies $HOME/cookies.txt" \
    --forward="--user-agent 'Mozilla/5.0'"

  yt-download 251+399 "URL" \
    --forward="--cookies $HOME/cookies.txt"

EOF
        return 0
    fi

    # ============================================================
    # VALIDATION
    # ============================================================

    if (( $# < 1 )); then
        echo "Usage: yt-download <url> [OPTIONS]"
        return 1
    fi

    # ============================================================
    # VARIABLES
    # ============================================================

    local url=""
    local audio_code=""
    local video_code=""
    local sub_lang=""

    local direct_mode="no"
    local just_audio="no"
    local just_video="no"
    local just_sub="no"
    local thumb_opt="no"

    # Semua argument asli yt-dlp disimpan di sini
    local -a forward_args=()

    # ============================================================
    # PARSER UNTUK --forward
    # ============================================================
    #
    # Input:
    #
    #   --forward="--cookies /path/cookies.txt"
    #
    # Output array:
    #
    #   "--cookies"
    #   "/path/cookies.txt"
    #
    # Quote di dalam forward juga didukung:
    #
    #   --forward="--user-agent 'Mozilla/5.0'"
    #
    # menjadi:
    #
    #   "--user-agent"
    #   "Mozilla/5.0"
    #
    # ============================================================

    _yt_parse_forward() {
        local input="$1"
        local token=""
        local quote=""
        local escaped="no"
        local char

        local i

        for ((i = 0; i < ${#input}; i++)); do
            char="${input:i:1}"

            # Escape character
            if [[ "$escaped" == "yes" ]]; then
                token+="$char"
                escaped="no"
                continue
            fi

            if [[ "$char" == '\' ]]; then
                escaped="yes"
                continue
            fi

            # Single quote
            if [[ "$char" == "'" ]]; then
                if [[ -z "$quote" ]]; then
                    quote="'"
                elif [[ "$quote" == "'" ]]; then
                    quote=""
                else
                    token+="$char"
                fi
                continue
            fi

            # Double quote
            if [[ "$char" == '"' ]]; then
                if [[ -z "$quote" ]]; then
                    quote='"'
                elif [[ "$quote" == '"' ]]; then
                    quote=""
                else
                    token+="$char"
                fi
                continue
            fi

            # Space di luar quote = separator argument
            if [[ "$char" =~ [[:space:]] ]] && [[ -z "$quote" ]]; then
                if [[ -n "$token" ]]; then
                    forward_args+=("$token")
                    token=""
                fi
                continue
            fi

            token+="$char"
        done

        # Unclosed quote
        if [[ -n "$quote" ]]; then
            echo "Error: quote tidak ditutup pada --forward:"
            echo "  $input"
            return 1
        fi

        # Trailing escape
        if [[ "$escaped" == "yes" ]]; then
            token+='\'
        fi

        if [[ -n "$token" ]]; then
            forward_args+=("$token")
        fi

        return 0
    }

    # ============================================================
    # PARSE MAIN ARGUMENTS
    # ============================================================

    local -a args=("$@")
    local i=0
    local arg

    # ------------------------------------------------------------
    # Direct mode: AUDIO+VIDEO URL
    # ------------------------------------------------------------

    if [[ "${args[0]}" =~ ^([0-9A-Za-z_-]+)\+([0-9A-Za-z_-]+)$ ]]; then

        direct_mode="yes"

        audio_code="${BASH_REMATCH[1]}"
        video_code="${BASH_REMATCH[2]}"

        if [[ -z "${args[1]:-}" ]]; then
            echo "Error: URL belum diberikan."
            echo
            echo "Usage:"
            echo "  yt-download AUDIO+VIDEO URL"
            return 1
        fi

        url="${args[1]}"
        i=2

    else

        url="${args[0]}"
        i=1

    fi

    # ------------------------------------------------------------
    # Options
    # ------------------------------------------------------------

    while (( i < ${#args[@]} )); do

        arg="${args[i]}"

        case "$arg" in

            # ----------------------------------------------------
            # Subtitle
            # ----------------------------------------------------

            --sub=*|--just-sub=*)
                sub_lang="${arg#*=}"
                just_sub="yes"
                ;;

            --just-sub)
                if (( i + 1 >= ${#args[@]} )); then
                    echo "Error: --just-sub membutuhkan language."
                    return 1
                fi

                sub_lang="${args[i+1]}"
                just_sub="yes"
                ((i++))
                ;;

            # ----------------------------------------------------
            # Thumbnail
            # ----------------------------------------------------

            --thumbnail=yes|--just-thumbnail)
                thumb_opt="yes"
                ;;

            # ----------------------------------------------------
            # Audio / Video
            # ----------------------------------------------------

            --just-audio)
                just_audio="yes"
                ;;

            --just-video)
                just_video="yes"
                ;;

            # ----------------------------------------------------
            # FORWARD
            # ----------------------------------------------------
            #
            # Satu --forward = satu group.
            #
            # --forward="--cookies file.txt"
            #
            # menjadi:
            #
            # forward_args+=("--cookies")
            # forward_args+=("file.txt")
            #
            # ----------------------------------------------------

            --forward=*)
                _yt_parse_forward "${arg#*=}" || return 1
                ;;

            --forward)
                if (( i + 1 >= ${#args[@]} )); then
                    echo "Error: --forward membutuhkan value."
                    return 1
                fi

                _yt_parse_forward "${args[i+1]}" || return 1
                ((i++))
                ;;

            # ----------------------------------------------------
            # Unknown option
            # ----------------------------------------------------

            *)
                echo "Warning: argument tidak dikenal: $arg"
                ;;

        esac

        ((i++))
    done

    # ============================================================
    # SHOW FORWARDED ARGUMENTS
    # ============================================================

    if (( ${#forward_args[@]} > 0 )); then
        echo "Forwarded yt-dlp arguments:"
        printf '  %q\n' "${forward_args[@]}"
        echo
    fi

    # ============================================================
    # GET TITLE
    # ============================================================

    echo "Getting video title..."

    local title

    title=$(
        yt-dlp \
            "${forward_args[@]}" \
            --get-title \
            --no-warnings \
            "$url"
    ) || {
        echo "Error: gagal mendapatkan video title."
        return 1
    }

    # ============================================================
    # SANITIZE TITLE
    # ============================================================

    local safe_title

    safe_title="$(
        printf '%s' "$title" |
        sed -E 's#[\\/:*?"<>|]#_#g' |
        sed -E 's/[[:space:]]+$//'
    )"

    [[ -n "$safe_title" ]] || safe_title="video"

    # ============================================================
    # JUST AUDIO
    # ============================================================

    if [[ "$just_audio" == "yes" ]]; then

        local audio_file

        yt-dlp \
            "${forward_args[@]}" \
            -f bestaudio \
            -o "$outdir/${safe_title}_audioraw.%(ext)s" \
            "$url" || return 1

        audio_file=$(
            find "$outdir" \
                -maxdepth 1 \
                -type f \
                -name "${safe_title}_audioraw.*" |
            head -n1
        )

        echo "Audio downloaded: ${audio_file:-unknown}"
        return 0
    fi

    # ============================================================
    # JUST VIDEO
    # ============================================================

    if [[ "$just_video" == "yes" ]]; then

        local video_file

        yt-dlp \
            "${forward_args[@]}" \
            -f bestvideo \
            -o "$outdir/${safe_title}_videoraw.%(ext)s" \
            "$url" || return 1

        video_file=$(
            find "$outdir" \
                -maxdepth 1 \
                -type f \
                -name "${safe_title}_videoraw.*" |
            head -n1
        )

        echo "Video downloaded: ${video_file:-unknown}"
        return 0
    fi

    # ============================================================
    # JUST SUBTITLE
    # ============================================================

    if [[ "$just_sub" == "yes" ]]; then

        yt-dlp \
            "${forward_args[@]}" \
            --write-subs \
            --skip-download \
            --sub-lang "$sub_lang" \
            -o "$outdir/${safe_title}.%(ext)s" \
            "$url" || return 1

        echo "Subtitle ($sub_lang) downloaded in:"
        echo "$outdir"

        return 0
    fi

    # ============================================================
    # JUST THUMBNAIL
    # ============================================================

    if [[ "$thumb_opt" == "yes" ]]; then

        yt-dlp \
            "${forward_args[@]}" \
            --skip-download \
            --write-thumbnail \
            -o "$outdir/${safe_title}.%(ext)s" \
            "$url" || return 1

        echo "Thumbnail downloaded in:"
        echo "$outdir"

        return 0
    fi

    # ============================================================
    # MANUAL FORMAT
    # ============================================================

    if [[ "$direct_mode" == "no" ]]; then

        echo
        echo "Available formats:"
        echo

        yt-dlp \
            "${forward_args[@]}" \
            -F \
            "$url" || return 1

        echo

        read -rp "Enter audio format code: " audio_code
        read -rp "Enter video format code: " video_code

        if [[ -z "$audio_code" || -z "$video_code" ]]; then
            echo "Error: audio/video format code wajib diisi."
            return 1
        fi

    else

        echo "Direct mode"
        echo "Audio : $audio_code"
        echo "Video : $video_code"
        echo
    fi

    # ============================================================
    # DOWNLOAD AUDIO
    # ============================================================

    local audio_file
    local video_file

    echo "Downloading audio..."

    yt-dlp \
        "${forward_args[@]}" \
        -f "$audio_code" \
        -o "$outdir/${safe_title}_audioraw.%(ext)s" \
        "$url" || return 1

    audio_file=$(
        find "$outdir" \
            -maxdepth 1 \
            -type f \
            -name "${safe_title}_audioraw.*" |
        head -n1
    )

    [[ -f "$audio_file" ]] || {
        echo "Error: audio file tidak ditemukan."
        return 1
    }

    echo "Audio: $audio_file"

    # ============================================================
    # DOWNLOAD VIDEO
    # ============================================================

    echo "Downloading video..."

    yt-dlp \
        "${forward_args[@]}" \
        -f "$video_code" \
        -o "$outdir/${safe_title}_videoraw.%(ext)s" \
        "$url" || return 1

    video_file=$(
        find "$outdir" \
            -maxdepth 1 \
            -type f \
            -name "${safe_title}_videoraw.*" |
        head -n1
    )

    [[ -f "$video_file" ]] || {
        echo "Error: video file tidak ditemukan."
        return 1
    }

    echo "Video: $video_file"

    # ============================================================
    # MERGE
    # ============================================================

    local out_fmt
    local final_file

    if [[ "$direct_mode" == "yes" ]]; then

        out_fmt="mp4"

    else

        local merge_opt

        echo

        read -rp "Do you want to merge audio+video? (y/N): " merge_opt

        if [[ ! "$merge_opt" =~ ^[Yy]$ ]]; then
            echo
            echo "Files kept separately:"
            echo "Audio : $audio_file"
            echo "Video : $video_file"
            return 0
        fi

        read -rp \
            "Choose output format [mp4/mkv/webm] (default mp4): " \
            out_fmt

        [[ "$out_fmt" =~ ^(mp4|mkv|webm)$ ]] || out_fmt="mp4"
    fi

    final_file="$outdir/${safe_title}.${out_fmt}"

    echo
    echo "Merging..."

    ffmpeg \
        -i "$video_file" \
        -i "$audio_file" \
        -c copy \
        "$final_file" || {
            echo "Error: gagal merge."
            return 1
        }

    echo
    echo "Merged file:"
    echo "$final_file"

    return 0
}


# ================================================================
# RUN
# ================================================================

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    yt_download "$@"
fi