set -l setyt_renderer (path dirname (path resolve (status filename)))/terminal_progress.py

function setyt --description "Download a YouTube video on the server" --inherit-variable setyt_renderer
    if test (count $argv) -lt 2; or test (count $argv) -gt 3
        printf 'Usage: setyt AUDIO+VIDEO URL [SUBTITLE]\n' >&2
        return 2
    end

    set -l server_url "$YT_SERVER"
    if test -z "$server_url"
        set -l gateway (ip route | awk '/^default / { print $3; exit }')
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

    set -l payload (jq -cn --arg resolution "$argv[1]" --arg url "$argv[2]" \
        --arg sub "$subtitle" '{url: $url, resolution: $resolution} + (if $sub == "" then {} else {sub: $sub} end)')
    or return 1

    curl --silent --no-buffer --fail-with-body --connect-timeout 10 \
        -H 'Content-Type: application/json' \
        -H 'Accept: application/x-ndjson' \
        --data "$payload" "$server_url/save" | python3 "$setyt_renderer"
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
