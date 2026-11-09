"use strict";

const get = (id) => document.getElementById(id);
const searchForm = get("search-form");
const downloadForm = get("download-form");
const buttons = Array.from(downloadForm.querySelectorAll("[data-mode]"));
const progressMime = "application/x-ndjson";
const logLines = [];
let currentVideo = null;
let busy = false;

const selected = (track) => downloadForm.querySelector(`input[name="${track}"]:checked`)?.value ?? "";

function formatBytes(bytes) {
    if (!Number.isFinite(bytes) || bytes < 0) return "Unknown";
    const units = ["B", "KiB", "MiB", "GiB", "TiB"];
    const unit = Math.min(Math.floor(Math.log2(Math.max(1, bytes)) / 10), units.length - 1);
    return `${(bytes / 1024 ** unit).toLocaleString("en", { maximumFractionDigits: unit ? 1 : 0 })} ${units[unit]}`;
}

function sizeLabel(size, estimated = false) {
    return Number.isFinite(size) && size >= 0 ? `${estimated ? "≈ " : ""}${formatBytes(size)}` : "Size unknown";
}

function formatTime(seconds) {
    const total = Math.max(0, Math.ceil(seconds));
    const hours = Math.floor(total / 3600);
    const minutes = Math.floor(total / 60) % 60;
    const remainder = total % 60;
    return [hours && `${hours}h`, minutes && `${minutes}m`, `${remainder}s`].filter(Boolean).join(" ");
}

function updateEstimate(audioCode, videoCode) {
    if (!currentVideo) return;
    const audio = currentVideo.formats.find((format) => format.id === audioCode);
    const video = currentVideo.formats.find((format) => format.id === videoCode);
    const subtitle = currentVideo.subtitles.find((item) => item.id === get("subtitle").value);
    const shared = audio && video && audio.id === video.id;
    const items = shared ? [["Shared audio/video source", video]] : [["Video", video], ["Audio", audio]];
    if (subtitle) items.push(["Subtitle", subtitle]);
    if (get("thumbnail").checked) {
        items.push(["Thumbnail", {
            size: currentVideo.thumbnail_size, size_estimated: currentVideo.thumbnail_size_estimated,
        }]);
    }
    const included = items.filter(([, item]) => item);
    const known = included.filter(([, item]) => Number.isFinite(item.size) && item.size >= 0);
    const subtotal = known.reduce((sum, [, item]) => sum + item.size, 0);
    const estimated = known.some(([, item]) => item.size_estimated);
    const unknown = included.length > known.length;
    get("package-size").textContent = known.length
        ? `${sizeLabel(subtotal, estimated)}${unknown ? " + unknown sizes" : ""}` : "Size unknown";
    get("package-breakdown").textContent = included.map(([label, item]) =>
        `${label} ${sizeLabel(item.size, item.size_estimated)}`).join(" · ");
}

function updateControls() {
    const audio = selected("audio");
    const video = selected("video");
    const available = {
        audio,
        video,
        subtitle: get("subtitle").value,
        thumbnail: currentVideo?.has_thumbnail,
        full: audio && video,
    };
    for (const button of buttons) {
        button.disabled = busy || !available[button.dataset.mode];
    }
    get("search-button").disabled = busy;
    get("url").disabled = busy;
    for (const radio of downloadForm.querySelectorAll('input[type="radio"]')) radio.disabled = busy;
    get("container").disabled = busy;
    get("subtitle").disabled = busy || !currentVideo?.subtitles.length;
    get("thumbnail").disabled = busy || !currentVideo?.has_thumbnail;
    updateEstimate(audio, video);
}

function appendLog(...lines) {
    if (!lines.length) return;
    logLines.push(...lines);
    if (logLines.length > 120) logLines.splice(0, logLines.length - 120);
    const log = get("log-entries");
    log.textContent = logLines.join("\n");
    if (get("activity-log").open) log.scrollTop = log.scrollHeight;
}

const basename = (path) => path.split("/").at(-1);

function showStatus(message, { error = false, files = [], downloads = null } = {}) {
    const card = get("status-card");
    const list = get("files");
    const archive = get("download-zip");
    const entries = downloads?.files ?? files.map((path) => ({ name: basename(path) }));
    get("status").textContent = message;
    card.dataset.error = String(error);
    card.hidden = false;
    list.replaceChildren(...entries.map((file) => {
        const item = document.createElement("li");
        if (file.url) {
            const link = document.createElement("a");
            link.href = file.url;
            link.download = "";
            link.className = "file-link";
            link.textContent = file.name;
            item.append(link);
        } else {
            item.textContent = file.name;
        }
        if (Number.isFinite(file.size)) {
            const size = document.createElement("small");
            size.className = "file-size";
            size.textContent = formatBytes(file.size);
            item.append(size);
        }
        return item;
    }));
    if (downloads?.archive) {
        archive.href = downloads.archive;
        archive.hidden = false;
    } else {
        archive.removeAttribute("href");
        archive.hidden = true;
    }
    get("downloaded-files").hidden = entries.length === 0;
}

function requestJSON(path, body, accept = "application/json") {
    return fetch(path, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: accept },
        body: JSON.stringify(body),
    });
}

async function post(path, body) {
    const response = await requestJSON(path, body);
    if (!response.headers.get("content-type")?.includes("application/json")) {
        throw new Error(`The server returned HTTP ${response.status}.`);
    }
    return response.json();
}

async function streamDownload(body, onEvent) {
    const response = await requestJSON("/api/download", body, progressMime);
    const contentType = response.headers.get("content-type") || "";
    if (contentType.includes("application/json")) return response.json();
    if (!response.ok || !response.body || !contentType.includes(progressMime)) {
        throw new Error(`The server returned HTTP ${response.status}.`);
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let pending = "";
    try {
        while (true) {
            const { value, done } = await reader.read();
            pending += decoder.decode(value, { stream: !done });
            const lines = pending.split("\n");
            pending = lines.pop();
            if (done) lines.push(pending);
            for (const line of lines) {
                if (!line.trim()) continue;
                const event = JSON.parse(line);
                if (event.type === "result") return event;
                onEvent(event);
            }
            if (done) throw new Error("The progress connection closed before a result was received.");
        }
    } finally {
        reader.releaseLock();
    }
}

function formatDetails(format, track) {
    const codec = track === "audio" ? format.audio_codec : format.video_codec;
    return [
        track === "audio" ? (format.audio_bitrate ? `${Math.round(format.audio_bitrate)} kbps` : "")
                          : format.resolution,
        track === "video" && format.fps ? `${format.fps} fps` : "",
        codec && codec !== "none" ? codec : "",
        format.ext?.toUpperCase(),
        sizeLabel(format.size, format.size_estimated),
        format.kind === "combined" ? "Progressive source" : format.note,
    ].filter(Boolean).join(" · ");
}

function renderFormats(formats, track) {
    const list = get(`${track}-list`);
    const options = formats.filter((format) => format.kind === track || format.kind === "combined");
    if (!options.length) {
        const empty = document.createElement("p");
        empty.textContent = `No downloadable ${track} formats.`;
        list.replaceChildren(empty);
        return;
    }
    const preferred = options.find((format) => format.kind === track) ?? options[0];
    list.replaceChildren(...options.map((format) => {
        const row = document.createElement("label");
        const radio = document.createElement("input");
        const details = document.createElement("span");
        const description = document.createElement("small");
        const code = document.createElement("strong");
        row.className = "format-row";
        radio.type = "radio";
        radio.name = track;
        radio.value = format.id;
        radio.checked = format === preferred;
        code.textContent = format.id;
        description.textContent = formatDetails(format, track);
        details.append(code, description);
        row.append(radio, details);
        return row;
    }));
    list.scrollTop = 0;
}

function renderResults(data) {
    currentVideo = data;
    get("video-title").textContent = data.title || "YouTube video";
    get("uploader").textContent = data.uploader || "";
    const preview = get("preview");
    preview.hidden = !data.thumbnail;
    if (data.thumbnail) {
        preview.src = data.thumbnail;
    } else {
        preview.removeAttribute("src");
    }

    renderFormats(data.formats, "audio");
    renderFormats(data.formats, "video");
    const subtitle = get("subtitle");
    subtitle.replaceChildren(new Option(data.subtitles.length ? "No subtitles" : "None available", ""));
    for (const item of data.subtitles) {
        subtitle.add(new Option(`${item.name} (${item.id}) · ${item.source} · ${sizeLabel(item.size, item.size_estimated)}`,
                                item.id));
    }

    const thumbnail = get("thumbnail");
    thumbnail.checked = false;
    get("thumbnail-size").textContent = data.has_thumbnail
        ? sizeLabel(data.thumbnail_size, data.thumbnail_size_estimated) : "None available";
    get("container").value = "mp4";
    const unavailable = data.formats.filter((format) => format.kind === "unavailable");
    get("unavailable-list").replaceChildren(...unavailable.map((format) => {
        const item = document.createElement("li");
        item.textContent = [format.id, format.ext, format.note, sizeLabel(format.size, format.size_estimated)]
            .filter(Boolean).join(" · ");
        return item;
    }));
    get("unavailable").hidden = !unavailable.length;
    get("results").hidden = false;
}

function updateStage(row, event) {
    const track = row.querySelector(".progress-track");
    const percent = Number.isFinite(event.percent) ? Math.max(0, Math.min(100, event.percent)) : null;
    const details = [];
    row.dataset.state = event.state;
    row.dataset.indeterminate = String(percent === null && event.state === "running");
    if (percent === null) {
        track.removeAttribute("aria-valuenow");
    } else {
        track.setAttribute("aria-valuenow", Math.floor(percent));
        row.style.setProperty("--progress", `${percent}%`);
    }
    row.querySelector(".progress-percent").textContent = percent === null ? "…" : `${Math.floor(percent)}%`;

    if (event.state === "waiting") details.push("Waiting");
    if (event.state === "done") details.push(event.reused ? "Shared source reused" : "Complete");
    if (Number.isFinite(event.bytes) && !event.reused) {
        details.push(event.state === "running" && Number.isFinite(event.total)
            ? `${formatBytes(event.bytes)} / ${sizeLabel(event.total, event.estimated)}`
            : formatBytes(event.bytes));
    }
    if (event.state === "running" && Number.isFinite(event.speed) && event.speed > 0) {
        details.push(`${formatBytes(event.speed)}/s`);
    }
    if (event.state === "running" && Number.isFinite(event.eta) && event.eta > 0) {
        details.push(`${formatTime(event.eta)} remaining`);
    }
    row.querySelector(".progress-detail").textContent = details.join(" · ") || "Working…";
}

function createProgress(mode, container) {
    const host = get("progress-stages");
    const labels = {
        video: "Download video", audio: "Download audio", merge: `Merge to ${container.toUpperCase()}`,
    };
    const stages = mode === "full" ? ["audio", "video", "merge"] : [mode].filter((stage) => stage in labels);
    const rows = new Map();
    host.replaceChildren();
    host.hidden = !stages.length;
    for (const stage of stages) {
        const row = get("progress-stage").content.firstElementChild.cloneNode(true);
        row.dataset.stage = stage;
        row.querySelector(".progress-label").textContent = labels[stage];
        row.querySelector(".progress-track").setAttribute("aria-label", labels[stage]);
        updateStage(row, { state: "waiting", percent: 0 });
        rows.set(stage, row);
        host.append(row);
    }

    const started = performance.now();
    const transferred = new Map();
    const summary = get("progress-summary");
    summary.hidden = false;
    const updateSummary = (elapsed = (performance.now() - started) / 1000, saved) => {
        const bytes = Array.from(transferred.values()).reduce((sum, value) => sum + value, 0);
        const details = [`Elapsed ${formatTime(elapsed)}`];
        if (transferred.size) details.push(`Media transferred ${formatBytes(bytes)}`);
        if (Number.isFinite(saved)) details.push(`Total saved ${formatBytes(saved)}`);
        summary.textContent = details.join(" · ");
    };
    updateSummary();
    const timer = setInterval(updateSummary, 1000);

    return {
        update(event) {
            if (event.type === "status") {
                get("status").textContent = event.message;
            } else if (event.type === "log") {
                appendLog(event.message);
            } else if (event.type === "progress" && rows.has(event.stage)) {
                updateStage(rows.get(event.stage), event);
                if (event.stage !== "merge" && Number.isFinite(event.bytes)) {
                    transferred.set(event.stage, event.bytes);
                }
                updateSummary();
            }
        },
        finish(result) {
            clearInterval(timer);
            updateSummary(result?.elapsed, result?.saved_bytes);
            if (result?.success) return;
            for (const row of rows.values()) {
                if (row.dataset.state === "done") continue;
                row.querySelector(".progress-detail").textContent = row.dataset.state === "waiting" ? "Skipped" : "Stopped";
                row.dataset.state = "failed";
                row.dataset.indeterminate = "false";
            }
        },
    };
}

searchForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (busy) return;
    const url = get("url").value.trim();
    busy = true;
    currentVideo = null;
    get("results").hidden = true;
    get("progress-summary").hidden = true;
    get("progress-stages").hidden = true;
    updateControls();
    showStatus("Looking up formats and subtitles…");
    appendLog("— Search —");
    try {
        const data = await post("/api/formats", { url });
        appendLog(...(data.logs ?? []));
        if (!data.success) throw new Error(data.error);
        renderResults(data);
        get("status-card").hidden = true;
    } catch (error) {
        showStatus(error.message, { error: true });
    } finally {
        busy = false;
        updateControls();
    }
});

async function startDownload(mode) {
    if (busy || !currentVideo) return;
    busy = true;
    updateControls();
    const container = get("container").value;
    const progress = createProgress(mode, container);
    showStatus("Preparing download…");
    appendLog("— Download —");
    get("status-card").scrollIntoView({ block: "nearest" });
    let data;
    try {
        data = await streamDownload({
            url: currentVideo.url,
            mode,
            audio: selected("audio"),
            video: selected("video"),
            container,
            sub: get("subtitle").value,
            thumbnail: get("thumbnail").checked,
        }, progress.update);
        const message = data.success ? "Download complete." : data.error;
        showStatus(data.directory ? `${message}\nFolder: ${basename(data.directory)}` : message,
                   { error: !data.success, files: data.files, downloads: data.downloads });
    } catch (error) {
        showStatus(error.message, { error: true });
    } finally {
        progress.finish(data);
        busy = false;
        updateControls();
    }
}

downloadForm.addEventListener("change", updateControls);
downloadForm.addEventListener("submit", (event) => {
    event.preventDefault();
    startDownload("full");
});
for (const button of buttons.filter((button) => button.type === "button")) {
    button.addEventListener("click", () => startDownload(button.dataset.mode));
}
