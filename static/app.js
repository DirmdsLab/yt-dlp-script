let currentURL = "";
let eventSource = null;

const urlInput = document.getElementById("url");

const btnFormat = document.getElementById("btnFormat");
const btnDownload = document.getElementById("btnDownload");

const videoInfo = document.getElementById("videoInfo");
const videoCard = document.getElementById("videoCard");
const audioCard = document.getElementById("audioCard");
const combinedCard = document.getElementById("combinedCard");
const downloadCard = document.getElementById("downloadCard");
const logCard = document.getElementById("logCard");

const videoList = document.getElementById("videoList");
const audioList = document.getElementById("audioList");
const combinedList = document.getElementById("combinedList");

const log = document.getElementById("log");

const selectedVideo = document.getElementById("selectedVideo");
const selectedAudio = document.getElementById("selectedAudio");
const selectedCode = document.getElementById("selectedCode");

btnFormat.onclick = getFormats;
btnDownload.onclick = downloadVideo;

function show(id){

    id.classList.remove("hidden");

}

function hide(id){

    id.classList.add("hidden");

}

function escapeHTML(str){

    return String(str ?? "")
        .replaceAll("&","&amp;")
        .replaceAll("<","&lt;")
        .replaceAll(">","&gt;");

}

async function getFormats(){

    currentURL = urlInput.value.trim();

    if(currentURL===""){

        alert("Masukkan URL YouTube.");

        return;

    }

    btnFormat.disabled = true;
    btnFormat.innerText = "Loading...";

    try{

        const res = await fetch(

            "/url/" + encodeURIComponent(currentURL),

            {
                method:"POST"
            }

        );

        const data = await res.json();

        if(!data.success){

            alert(data.error);

            return;

        }

        document.getElementById("thumbnail").src = data.thumbnail || "";

        document.getElementById("title").innerText =
            data.title || "-";

        document.getElementById("uploader").innerText =
            data.uploader || "-";

        document.getElementById("duration").innerText =
            data.duration || "-";

        document.getElementById("views").innerText =
            data.view_count || "-";

        document.getElementById("uploadDate").innerText =
            data.upload_date || "-";

        buildVideo(data.video);

        buildAudio(data.audio);

        buildCombined(data.combined);

        show(videoInfo);
        show(videoCard);
        show(audioCard);

        if(data.combined.length){

            show(combinedCard);

        }else{

            hide(combinedCard);

        }

        show(downloadCard);
        show(logCard);

        log.textContent = "Ready.\n";

    }

    catch(e){

        alert(e);

    }

    finally{

        btnFormat.disabled = false;
        btnFormat.innerText = "Get Format";

    }

}

function buildVideo(list){

    videoList.innerHTML="";

    list.forEach(v=>{

        videoList.innerHTML += `

<label class="format-item">

<input
type="radio"
name="video"
value="${v.id}"
onchange="updateSelection()"
>

<div class="format-info">

<div class="format-title">

${escapeHTML(v.id)}
-
${escapeHTML(v.resolution||"-")}

</div>

<div class="format-meta">

FPS : ${escapeHTML(v.fps||"-")}

|

Codec : ${escapeHTML(v.codec||"-")}

|

Size : ${escapeHTML(v.size||"-")}

</div>

</div>

</label>

`;

    });

}

function buildAudio(list){

    audioList.innerHTML="";

    list.forEach(a=>{

        audioList.innerHTML += `

<label class="format-item">

<input
type="radio"
name="audio"
value="${a.id}"
onchange="updateSelection()"
>

<div class="format-info">

<div class="format-title">

${escapeHTML(a.id)}

</div>

<div class="format-meta">

${escapeHTML(a.codec||"-")}

|

${escapeHTML(a.abr||"-")} kbps

|

${escapeHTML(a.size||"-")}

</div>

</div>

</label>

`;

    });

}

function buildCombined(list){

    combinedList.innerHTML="";

    list.forEach(c=>{

        combinedList.innerHTML += `

<div class="format-item">

<div class="format-info">

<div class="format-title">

${escapeHTML(c.id)}

-
${escapeHTML(c.resolution||"-")}

</div>

<div class="format-meta">

Video :

${escapeHTML(c.video_codec)}

|

Audio :

${escapeHTML(c.audio_codec)}

|

${escapeHTML(c.size||"-")}

</div>

</div>

</div>

`;

    });

}

function updateSelection(){

    const video =
        document.querySelector(
            "input[name=video]:checked"
        );

    const audio =
        document.querySelector(
            "input[name=audio]:checked"
        );

    selectedVideo.innerText =
        video ? video.value : "-";

    selectedAudio.innerText =
        audio ? audio.value : "-";

    if(video && audio){

        selectedCode.innerText =
            audio.value + "+" + video.value;

    }else{

        selectedCode.innerText="-";

    }

}

function downloadVideo(){

    const video =
        document.querySelector(
            "input[name=video]:checked"
        );

    const audio =
        document.querySelector(
            "input[name=audio]:checked"
        );

    if(!video){

        alert("Pilih Video");

        return;

    }

    if(!audio){

        alert("Pilih Audio");

        return;

    }

    if(eventSource){

        eventSource.close();

    }

    log.textContent="";

    btnDownload.disabled=true;
    btnDownload.innerText="Downloading...";

    const code =
        audio.value + "+" + video.value;

    eventSource = new EventSource(

        "/download/"

        + encodeURIComponent(code)

        + "/"

        + encodeURIComponent(currentURL)

    );

    eventSource.onmessage = function(e){

        log.textContent += e.data + "\n";

        log.scrollTop = log.scrollHeight;

    };

    eventSource.addEventListener(

        "finish",

        function(){

            btnDownload.disabled=false;

            btnDownload.innerText="Download";

            eventSource.close();

            eventSource=null;

            log.textContent +=
                "\n==========\nFinished.\n";

            log.scrollTop =
                log.scrollHeight;

        }

    );

    eventSource.onerror = function(){

        btnDownload.disabled=false;

        btnDownload.innerText="Download";

        if(eventSource){

            eventSource.close();

            eventSource=null;

        }

    };

}