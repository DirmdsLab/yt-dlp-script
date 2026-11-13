
function setyt_format_bytes
    set -l size $argv[1]

    if test -z "$size"
        set size 0
    end

    set -l units B KiB MiB GiB TiB
    set -l index 1

    while test $size -ge 1024; and test $index -lt 5
        set size (math "$size / 1024")
        set index (math "$index + 1")
    end

    if test $index -eq 1
        printf "%.0f %s" $size $units[$index]
    else
        printf "%.1f %s" $size $units[$index]
    end
end


function setyt_format_time
    set -l raw "$argv[1]"

    if not string match -rq '^[0-9]+([.][0-9]+)?$' -- "$raw"
        set raw 0
    end

    set -l seconds (math "floor($raw)")

    if test "$seconds" -lt 0
        set seconds 0
    end

    set -l hours (math "floor($seconds / 3600)")
    set -l remainder (math "$seconds % 3600")
    set -l minutes (math "floor($remainder / 60)")
    set -l remaining (math "$remainder % 60")

    if test "$hours" -gt 0
        printf "%dh %02dm %02ds" $hours $minutes $remaining
    else if test "$minutes" -gt 0
        printf "%dm %02ds" $minutes $remaining
    else
        printf "%ds" $remaining
    end
end


function setyt_render_row
    set -l label $argv[1]
    set -l state $argv[2]
    set -l percent $argv[3]
    set -l bytes $argv[4]
    set -l total $argv[5]
    set -l speed $argv[6]
    set -l eta $argv[7]
    set -l reused $argv[8]

    set -l width 20
    set -l filled 0
    set -l bar ""

    if test -n "$percent"
        set filled (math "min($width, max(0, floor($percent * $width / 100)))")
    end

    if test "$state" = running; and test -z "$percent"
        set bar (string repeat -n $width "-")
    else
        set bar (string repeat -n $filled "=")
        set bar "$bar"(string repeat -n (math "$width - $filled") "-")
    end

    if test -n "$percent"
        set -l percentage (printf "%.0f" $percent)
        set percentage "$percentage%"
    else
        set percentage "--%"
    end

    set -l details

    if test "$state" != running
        if test "$state" = done; and test "$reused" = true
            set details "Shared source reused"
        else
            set details (string upper (string sub -l 1 -- "$state"))(string sub -s 2 -- "$state")
        end
    end

    if test -n "$bytes"; and test "$reused" != true
        set -l size (setyt_format_bytes "$bytes")

        if test -n "$total"; and test "$state" = running
            set size "$size / "(setyt_format_bytes "$total")
        end

        set -a details "$size"
    end

    if test "$state" = running
        if test -n "$speed"; and test "$speed" -gt 0
            set -a details (setyt_format_bytes "$speed")/s
        end

        if test -n "$eta"; and test "$eta" -gt 0
            set -a details (setyt_format_time "$eta")" left"
        end
    end

    printf "%-11s [%s] %4s" "$label" "$bar" "$percentage"

    if test (count $details) -gt 0
        printf "  %s" (string join " | " $details)
    end

    printf "\n"
end

function setyt_progress
    set -l mode full
    set -l container mp4

    set -l audio_state waiting
    set -l audio_percent 0
    set -l audio_bytes 0
    set -l audio_total ""
    set -l audio_speed ""
    set -l audio_eta ""
    set -l audio_reused false

    set -l video_state waiting
    set -l video_percent 0
    set -l video_bytes 0
    set -l video_total ""
    set -l video_speed ""
    set -l video_eta ""
    set -l video_reused false

    set -l merge_state waiting
    set -l merge_percent 0
    set -l merge_bytes ""
    set -l merge_total ""
    set -l merge_speed ""
    set -l merge_eta ""
    set -l merge_reused false

    set -l status_text "Preparing download..."
    set -l started (date +%s)
    set -l elapsed ""
    set -l saved ""
    set -l success ""
    set -l result_received false
    set -l error_message ""
    set -l line_count 0

    set -l live false
    if isatty stderr; and test "$TERM" != dumb
        set live true
    end

    set -l last_audio_milestone 0
    set -l last_video_milestone 0
    set -l last_merge_milestone 0

    while read -l line
        test -z "$line"; and continue

        if not printf '%s\n' "$line" | jq -e 'type == "object"' >/dev/null 2>&1
            set error_message "The server returned an invalid download response."
            break
        end

        set -l event_type (printf '%s\n' "$line" | jq -r '.type // ""')

        switch $event_type
            case status
                set status_text (printf '%s\n' "$line" | jq -r '.message // ""')

            case progress
                set -l stage (printf '%s\n' "$line" | jq -r '.stage // ""')
                set -l state (printf '%s\n' "$line" | jq -r '.state // ""')
                set -l percent (printf '%s\n' "$line" | jq -r '.percent // ""')
                set -l bytes (printf '%s\n' "$line" | jq -r '.bytes // ""')
                set -l total (printf '%s\n' "$line" | jq -r '.total // ""')
                set -l speed (printf '%s\n' "$line" | jq -r '.speed // ""')
                set -l eta (printf '%s\n' "$line" | jq -r '.eta // ""')
                set -l reused (printf '%s\n' "$line" | jq -r '.reused // false')

                switch $stage
                    case audio
                        set audio_state $state
                        set audio_percent $percent
                        set audio_bytes $bytes
                        set audio_total $total
                        set audio_speed $speed
                        set audio_eta $eta
                        set audio_reused $reused

                    case video
                        set video_state $state
                        set video_percent $percent
                        set video_bytes $bytes
                        set video_total $total
                        set video_speed $speed
                        set video_eta $eta
                        set video_reused $reused

                    case merge
                        set merge_state $state
                        set merge_percent $percent
                        set merge_bytes $bytes
                        set merge_total $total
                        set merge_speed $speed
                        set merge_eta $eta
                        set merge_reused $reused
                end

            case result
                set result_received true
                set success (printf '%s\n' "$line" | jq -r '.success // false')
                set elapsed (printf '%s\n' "$line" | jq -r '.elapsed // ""')
                set saved (printf '%s\n' "$line" | jq -r '.saved_bytes // ""')
                set error_message (printf '%s\n' "$line" | jq -r '.error // ""')

                if test "$success" = true
                    set status_text "Files saved."

                    for stage in audio video merge
                        switch $stage
                            case audio
                                if test "$audio_state" != done
                                    set audio_state done
                                    set audio_percent 100
                                end
                            case video
                                if test "$video_state" != done
                                    set video_state done
                                    set video_percent 100
                                end
                            case merge
                                if test "$merge_state" != done
                                    set merge_state done
                                    set merge_percent 100
                                end
                        end
                    end
                else
                    set status_text "Download stopped."
                end

            case '*'
                if printf '%s\n' "$line" | jq -e 'has("success")' >/dev/null 2>&1
                    set result_received true
                    set success (printf '%s\n' "$line" | jq -r '.success // false')
                    set elapsed (printf '%s\n' "$line" | jq -r '.elapsed // ""')
                    set saved (printf '%s\n' "$line" | jq -r '.saved_bytes // ""')
                    set error_message (printf '%s\n' "$line" | jq -r '.error // ""')
                end
        end

        if test "$live" = true
            if test $line_count -gt 0
                printf '\033[%dA' $line_count >&2
            end

            set -l current_time (date +%s)
            set -l elapsed_now (math "$current_time - $started")
            set -l title "YouTube download"

            if test "$result_received" = true
                if test "$success" = true
                    set title "Download complete"
                else
                    set title "Download failed"
                end
            end

            printf '\033[2K\033[36m%s\033[0m  |  Elapsed %s\n' \
                "$title" (setyt_format_time "$elapsed_now") >&2

            set -l row_count 1

            if test "$mode" = full
                setyt_render_row "Audio" "$audio_state" "$audio_percent" "$audio_bytes" "$audio_total" "$audio_speed" "$audio_eta" "$audio_reused" >&2
                setyt_render_row "Video" "$video_state" "$video_percent" "$video_bytes" "$video_total" "$video_speed" "$video_eta" "$video_reused" >&2
                setyt_render_row "Merge MP4" "$merge_state" "$merge_percent" "$merge_bytes" "$merge_total" "$merge_speed" "$merge_eta" "$merge_reused" >&2
                set row_count 4
            end

            printf '\033[2K%s\n' "$status_text" >&2
            set line_count (math "$row_count + 1")
            if test "$result_received" = true
                set line_count (math "$line_count + 1")
            end

            if test -n "$saved"
                printf '\033[2KSaved: %s\n' (setyt_format_bytes "$saved") >&2
            end

            if test "$result_received" = true
                printf '\033[2K\n' >&2
            end

            if test "$result_received" = true
                set line_count 0
            end
        else
            for stage in audio video merge
                switch $stage
                    case audio
                        set state $audio_state
                        set percent $audio_percent
                        set milestone $last_audio_milestone
                    case video
                        set state $video_state
                        set percent $video_percent
                        set milestone $last_video_milestone
                    case merge
                        set state $merge_state
                        set percent $merge_percent
                        set milestone $last_merge_milestone
                end

                if test "$state" = done
                    set should_print true
                else if string match -qr '^[0-9]+([.][0-9]+)?$' -- "$percent"
                    set current_milestone (math "floor($percent / 25) * 25")
                    set should_print false

                    if test $current_milestone -gt $milestone; and test $current_milestone -gt 0
                        set should_print true
                    end
                else
                    set should_print false
                end

                if test "$should_print" = true
                    switch $stage
                        case audio
                            setyt_render_row "Audio" "$audio_state" "$audio_percent" "$audio_bytes" "$audio_total" "$audio_speed" "$audio_eta" "$audio_reused" >&2
                            set last_audio_milestone (math "floor($audio_percent / 25) * 25")
                        case video
                            setyt_render_row "Video" "$video_state" "$video_percent" "$video_bytes" "$video_total" "$video_speed" "$video_eta" "$video_reused" >&2
                            set last_video_milestone (math "floor($video_percent / 25) * 25")
                        case merge
                            setyt_render_row "Merge MP4" "$merge_state" "$merge_percent" "$merge_bytes" "$merge_total" "$merge_speed" "$merge_eta" "$merge_reused" >&2
                            set last_merge_milestone (math "floor($merge_percent / 25) * 25")
                    end
                end
            end

            if test "$event_type" = status
                printf '%s\n' "$status_text" >&2
            end
        end
    end

    if test "$result_received" != true; and test -z "$error_message"
        set error_message "The server connection closed before a download result was received."
    end

    if test -n "$error_message"; and test "$success" != true
        printf 'Error: %s\n' "$error_message" >&2
        return 1
    end

    if test "$success" != true
        printf 'Error: %s\n' "$error_message" >&2
        return 1
    end

    if test -n "$elapsed"
        printf 'Elapsed: %s\n' (setyt_format_time "$elapsed") >&2
    end

    if test -n "$saved"
        printf 'Saved: %s\n' (setyt_format_bytes "$saved") >&2
    end

    return 0
end

function _setyt_download_one
    set -l server_url "$YT_SERVER"

    if test -z "$server_url"
        set -l gateway (ip route | string match -r '^default via ([^ ]+)' | string replace -r '^default via ' '' | head -n 1)

        if test -z "$gateway"
            printf 'Set YT_SERVER to the server address, including port 7010.\n' >&2
            return 1
        end

        set server_url "http://$gateway:7010"
    end

    set -l subtitle ''

    if set -q argv[3]
        set subtitle "$argv[3]"
    end

    set -l payload (jq -cn \
        --arg resolution "$argv[1]" \
        --arg url "$argv[2]" \
        --arg sub "$subtitle" \
        '{url: $url, resolution: $resolution} + (if $sub == "" then {} else {sub: $sub} end)')

    or return 1

    curl --silent --no-buffer --fail-with-body --connect-timeout 10 \
        -H 'Content-Type: application/json' \
        -H 'Accept: application/x-ndjson' \
        --data "$payload" \
        "$server_url/save" | setyt_progress

    set -l codes $pipestatus

    if test $codes[1] -ne 0; and test $codes[1] -ne 22
        printf 'Server request failed (curl exit %s).\n' "$codes[1]" >&2
        return $codes[1]
    end

    if test $codes[2] -ne 0
        return $codes[2]
    end

    return $codes[1]
end


function setyt --description "Download YouTube videos from URL or text file"
    if test (count $argv) -lt 2; or test (count $argv) -gt 3
        printf 'Usage:\n'
        printf '  setyt AUDIO+VIDEO URL [SUBTITLE]\n'
        printf '  setyt AUDIO+VIDEO FILE.txt\n' >&2
        return 2
    end

    set -l resolution "$argv[1]"
    set -l target "$argv[2]"

    # Batch mode: second argument is an existing file
    if test -f "$target"; and test (count $argv) -eq 2
        set -l fail_file "/tmp/setyt-failed.txt"
        set -l total 0
        set -l failed 0

        touch "$fail_file"

        printf '\nBatch download started: %s\n' "$target"
        printf 'Failed URLs will be saved to: %s\n\n' "$fail_file"

        while read -l url
            set url (string replace -r '\r$' '' -- "$url")

            test -z "$url"; and continue

            set total (math "$total + 1")

            printf '\n========================================\n'
            printf '[%s] Processing: %s\n' "$total" "$url"
            printf '========================================\n'

            if _setyt_download_one "$resolution" "$url"
                printf 'SUCCESS: %s\n' "$url"
            else
                set failed (math "$failed + 1")
                printf '%s\n' "$url" >> "$fail_file"
                printf 'FAILED: %s\n' "$url"
            end
        end < "$target"

        # Remove the original input file after processing.
        rm -f -- "$target"

        printf '\n========================================\n'
        printf 'Batch finished.\n'
        printf 'Total: %s\n' "$total"
        printf 'Failed: %s\n' "$failed"
        printf 'Successful: %s\n' (math "$total - $failed")
        printf 'Failed URLs: %s\n' "$fail_file"
        printf '========================================\n'

        if test "$failed" -gt 0
            return 1
        end

        return 0
    end

    # Single URL mode
    if test (count $argv) -eq 3
        _setyt_download_one "$resolution" "$target" "$argv[3]"
    else
        _setyt_download_one "$resolution" "$target"
    end
end
