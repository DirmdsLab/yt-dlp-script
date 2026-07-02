#!/usr/bin/env bash

# --- yt-download: YouTube downloader helper script (Bash version) ---

yt_download() {
    # --- HELP ---
    for arg in "$@"; do
        if [[ "$arg" == "--help" ]]; then
            echo "Usage: yt-download <url> [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --just-audio          Download only audio"
            echo "  --just-video          Download only video"
            echo "  --just-sub=<lang>     Download subtitle with language code (e.g., en, ja)"
            echo "  --just-thumbnail      Download only thumbnail"
            echo "  --thumbnail=yes       Save thumbnail along with normal download"
            echo ""
            echo "Normal usage steps:"
            echo "1. yt-download <url>                : List available formats and prompt to choose audio/video"
            echo "2. Enter audio format code"
            echo "3. Enter video format code"
            echo "4. Downloads audio and video separately"
            echo "5. Ask user if want to merge audio+video"
            echo "6. If merge, ask output format (mp4/mkv/webm)"
            echo "7. Save merged file in ~/Downloads/ with sanitized name"
            return
        fi
    done

    if [[ $# -lt 1 ]]; then
        echo "Usage: yt-download <url> [--help]"
        return 1
    fi


    direct_mode="no"
    audio_code=""
    video_code=""

    # Jika argumen pertama berbentuk 251+399
    if [[ "$1" =~ ^([0-9A-Za-z_-]+)\+([0-9A-Za-z_-]+)$ ]]; then
        direct_mode="yes"
        audio_code="${BASH_REMATCH[1]}"
        video_code="${BASH_REMATCH[2]}"
        url="$2"

        if [[ -z "$url" ]]; then
            echo "Usage:"
            echo "  $0 AUDIO+VIDEO URL"
            echo "Example:"
            echo "  $0 251+399 https://youtu.be/xxxxx"
            return 1
        fi
    else
        url="$1"
    fi

    sub_lang=""
    thumb_opt="no"
    just_audio="no"
    just_video="no"
    just_sub="no"

    # Parse optional args
    for arg in "${@:2}"; do
        case "$arg" in
            --sub=*|--just-sub=*)
                sub_lang="${arg#*=}"
                just_sub="yes"
                ;;
            --thumbnail=yes)
                thumb_opt="yes"
                ;;
            --just-audio)
                just_audio="yes"
                ;;
            --just-video)
                just_video="yes"
                ;;
            --just-thumbnail)
                thumb_opt="yes"
                ;;
        esac
    done

    # Get video title and sanitize filename
    title=$(yt-dlp --get-title "$url")
    safe_title=$(echo "$title" | sed -E 's#[\\/:"*?<>|]#_#g' | sed -E 's/[[:space:]]+$//')
    outdir="HereChange"

    # --- Just Audio ---
    if [[ "$just_audio" == "yes" ]]; then
        yt-dlp -f bestaudio -o "$outdir/${safe_title}_audioraw.%(ext)s" "$url"
        audio_file=$(ls -t "$outdir/${safe_title}_audioraw".* 2>/dev/null | head -n1)
        echo "Audio downloaded: $audio_file"
        return
    fi

    # --- Just Video ---
    if [[ "$just_video" == "yes" ]]; then
        yt-dlp -f bestvideo -o "$outdir/${safe_title}_videoraw.%(ext)s" "$url"
        video_file=$(ls -t "$outdir/${safe_title}_videoraw".* 2>/dev/null | head -n1)
        echo "Video downloaded: $video_file"
        return
    fi

    # --- Just Subtitle ---
    if [[ "$just_sub" == "yes" ]]; then
        yt-dlp --write-subs --skip-download --sub-lang "$sub_lang" -o "$outdir/${safe_title}.%(ext)s" "$url"
        echo "Subtitle ($sub_lang) downloaded in $outdir"
        return
    fi

    # --- Just Thumbnail ---
    if [[ "$thumb_opt" == "yes" ]]; then
        yt-dlp --skip-download --write-thumbnail -o "$outdir/${safe_title}.%(ext)s" "$url"
        echo "Thumbnail downloaded in $outdir"
        return
    fi

    # --- Manual / Direct Mode ---
    if [[ "$direct_mode" == "no" ]]; then
        yt-dlp -F "$url"
        read -rp "Enter audio format code: " audio_code
        read -rp "Enter video format code: " video_code
    else
        echo "Direct mode"
        echo "Audio : $audio_code"
        echo "Video : $video_code"
    fi

    # Download audio
    yt-dlp -f "$audio_code" -o "$outdir/${safe_title}_audioraw.%(ext)s" "$url"
    audio_file=$(ls -t "$outdir/${safe_title}_audioraw".* 2>/dev/null | head -n1)

    # Download video
    yt-dlp -f "$video_code" -o "$outdir/${safe_title}_videoraw.%(ext)s" "$url"
    video_file=$(ls -t "$outdir/${safe_title}_videoraw".* 2>/dev/null | head -n1)

    if [[ "$direct_mode" == "yes" ]]; then
        out_fmt="mp4"
        final_file="$outdir/${safe_title}.${out_fmt}"
    
        ffmpeg \
            -i "$video_file" \
            -i "$audio_file" \
            -c copy \
            "$final_file"
    
        echo "Merged file: $final_file"
    
    else
        read -rp "Do you want to merge audio+video? (y/N): " merge_opt
    
        if [[ "$merge_opt" =~ ^[Yy]$ ]]; then
            read -rp "Choose output format [mp4/mkv/webm] (default mp4): " out_fmt
    
            [[ "$out_fmt" =~ ^(mp4|mkv|webm)$ ]] || out_fmt="mp4"
    
            final_file="$outdir/${safe_title}.${out_fmt}"
    
            ffmpeg \
                -i "$video_file" \
                -i "$audio_file" \
                -c copy \
                "$final_file"
    
            echo "Merged file: $final_file"
        fi
    fi
}

# If script is run directly (not sourced), execute function
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    yt_download "$@"
fi
