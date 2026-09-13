// ===== State =====
let mediaRecorder = null;
let audioChunks = [];
let rawStream = null;       // the raw mic or tab-capture MediaStream
let currentSourceType = null; // 'mic' | 'tab' — tracks what rawStream currently is
let startTime = null;
let elapsedBeforePause = 0;
let timerInterval = null;
let isRecording = false;
let isPaused = false;
let isStarting = false; // guards against overlapping startRecording() calls

// Shared Web Audio graph (used for both webm + mp3 so tab playback keeps working)
let audioCtx = null;
let sourceNode = null;
let destNode = null;        // MediaStreamDestination -> feeds MediaRecorder (webm)
let processorNode = null;   // ScriptProcessor -> feeds mp3 encoder
let mp3Encoder = null;
let mp3Data = [];

// Auto voice-detection
let monitorCtx = null;
let monitorSource = null;
let monitorAnalyser = null;
let monitorRaf = null;
let silenceTimer = null;
const START_THRESHOLD = 0.015;
const STOP_SILENCE_MS = 2000;

// ===== DOM =====
const startBtn = document.getElementById('startBtn');
const pauseBtn = document.getElementById('pauseBtn');
const stopBtn = document.getElementById('stopBtn');
const timerEl = document.getElementById('timer');
const statusEl = document.getElementById('status');
const recordingsList = document.getElementById('recordingsList');
const sourceSelect = document.getElementById('sourceSelect');
const formatSelect = document.getElementById('formatSelect');
const autoDetectToggle = document.getElementById('autoDetectToggle');
const autoStatus = document.getElementById('autoStatus');

// A tabCapture stream ID (if any) is handed to us via URL params by
// background.js at the moment the extension icon was clicked — this is
// required because tabCapture must be authorized by that exact user gesture.
const urlParams = new URLSearchParams(window.location.search);
const grantedTabStreamId = urlParams.get('streamId');
const tabCaptureError = urlParams.get('tabError');
let tabStreamUsed = false; // a stream ID can only be consumed once

if (tabCaptureError) {
  statusEl.textContent = tabCaptureError;
  sourceSelect.value = 'mic';
  sourceSelect.querySelector('option[value="tab"]').disabled = true;
} else if (!grantedTabStreamId) {
  sourceSelect.querySelector('option[value="tab"]').disabled = true;
  sourceSelect.value = 'mic';
}

// ===== Helpers =====
function formatTime(ms) {
  const totalSeconds = Math.floor(ms / 1000);
  const minutes = String(Math.floor(totalSeconds / 60)).padStart(2, '0');
  const seconds = String(totalSeconds % 60).padStart(2, '0');
  return `${minutes}:${seconds}`;
}

function updateTimer() {
  const elapsed = elapsedBeforePause + (Date.now() - startTime);
  timerEl.textContent = formatTime(elapsed);
}

let recordingCounter = 0;

function addRecordingCard(blob, extension) {
  const id = `rec${++recordingCounter}`;
  const url = URL.createObjectURL(blob);
  const now = new Date();
  const timeLabel = now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });

  const card = document.createElement('div');
  card.className = 'recording-card';
  card.innerHTML = `
    <div class="recording-card-header">
      <span>${timeLabel} · .${extension}</span>
    </div>
    <audio class="player-audio" src="${url}" preload="metadata"></audio>
    <div class="player-row">
      <button class="play-btn">▶ Play</button>
      <span class="time-label">0:00 / 0:00</span>
    </div>
    <canvas class="waveform-canvas" width="300" height="50"></canvas>
    <div class="trim-row">
      <div class="trim-sliders">
        <input type="range" class="trim-start" min="0" max="1000" value="0" step="1">
        <input type="range" class="trim-end" min="0" max="1000" value="1000" step="1">
      </div>
      <div class="trim-labels">
        <span class="trim-start-label">0:00</span>
        <span class="trim-end-label">0:00</span>
      </div>
    </div>
    <div class="card-actions">
      <button class="btn-trim">Trim &amp; Save</button>
      <a class="btn-download" href="${url}" download="recording-${Date.now()}.${extension}">Download</a>
      <button class="btn-remove">Remove</button>
    </div>
  `;
  recordingsList.prepend(card);

  const audioEl = card.querySelector('.player-audio');
  const playBtn = card.querySelector('.play-btn');
  const timeLabelEl = card.querySelector('.time-label');
  const waveformCanvas = card.querySelector('.waveform-canvas');
  const startSlider = card.querySelector('.trim-start');
  const endSlider = card.querySelector('.trim-end');
  const startLabel = card.querySelector('.trim-start-label');
  const endLabel = card.querySelector('.trim-end-label');
  const trimBtn = card.querySelector('.btn-trim');
  const removeBtn = card.querySelector('.btn-remove');

  drawWaveform(blob, waveformCanvas);

  // Track every object URL created for this card so we can revoke them on removal.
  const objectUrls = [url];

  removeBtn.addEventListener('click', () => {
    audioEl.pause();
    audioEl.src = '';
    objectUrls.forEach((u) => URL.revokeObjectURL(u));
    card.remove();
  });

  let duration = 0;

  audioEl.addEventListener('loadedmetadata', () => {
    duration = audioEl.duration || 0;
    timeLabelEl.textContent = `0:00 / ${formatTime(duration * 1000)}`;
    endLabel.textContent = formatTime(duration * 1000);
  });

  audioEl.addEventListener('timeupdate', () => {
    timeLabelEl.textContent = `${formatTime(audioEl.currentTime * 1000)} / ${formatTime(duration * 1000)}`;
  });

  audioEl.addEventListener('ended', () => {
    playBtn.textContent = '▶ Play';
  });

  playBtn.addEventListener('click', () => {
    if (audioEl.paused) {
      // Play only within the selected trim range
      const startTime = (startSlider.value / 1000) * duration;
      if (audioEl.currentTime < startTime || audioEl.currentTime >= (endSlider.value / 1000) * duration) {
        audioEl.currentTime = startTime;
      }
      audioEl.play();
      playBtn.textContent = '⏸ Stop';
    } else {
      audioEl.pause();
      playBtn.textContent = '▶ Play';
    }
  });

  // Stop playback automatically once it reaches the trim end marker
  audioEl.addEventListener('timeupdate', () => {
    const endTime = (endSlider.value / 1000) * duration;
    if (audioEl.currentTime >= endTime) {
      audioEl.pause();
      playBtn.textContent = '▶ Play';
    }
  });

  function syncSliderLabels() {
    if (parseInt(startSlider.value) > parseInt(endSlider.value)) {
      startSlider.value = endSlider.value;
    }
    startLabel.textContent = formatTime((startSlider.value / 1000) * duration * 1000);
    endLabel.textContent = formatTime((endSlider.value / 1000) * duration * 1000);
  }

  startSlider.addEventListener('input', syncSliderLabels);
  endSlider.addEventListener('input', syncSliderLabels);

  trimBtn.addEventListener('click', async () => {
    trimBtn.disabled = true;
    trimBtn.textContent = 'Trimming...';
    try {
      const startTime = (startSlider.value / 1000) * duration;
      const endTime = (endSlider.value / 1000) * duration;
      const trimmedBlob = await trimAudioBlob(blob, startTime, endTime, extension);
      const trimmedUrl = URL.createObjectURL(trimmedBlob);
      objectUrls.push(trimmedUrl);

      // IMPORTANT: point subsequent trims at the newly trimmed clip, not the
      // original — otherwise a second trim re-applies the slider percentages
      // to the old (longer) audio and cuts the wrong section.
      blob = trimmedBlob;

      const outExt = extension === 'mp3' ? 'mp3' : 'wav';

      // Replace the player + download link with the trimmed version
      audioEl.src = trimmedUrl;
      const downloadLink = card.querySelector('.btn-download');
      downloadLink.href = trimmedUrl;
      downloadLink.download = `recording-trimmed-${Date.now()}.${outExt}`;
      downloadLink.textContent = 'Download';
      card.querySelector('.recording-card-header span').textContent = `${timeLabel} · trimmed .${outExt}`;

      startSlider.value = 0;
      endSlider.value = 1000;
      startLabel.textContent = '0:00';

      drawWaveform(blob, waveformCanvas);
    } catch (err) {
      console.error(err);
      alert('Trim failed: ' + err.message);
    }
    trimBtn.disabled = false;
    trimBtn.textContent = 'Trim & Save';
  });
}

// Decodes a blob and draws its amplitude waveform onto the given canvas.
async function drawWaveform(blob, canvas) {
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, canvas.width, canvas.height);

  let audioBuffer;
  try {
    const arrayBuffer = await blob.arrayBuffer();
    const tempCtx = new (window.AudioContext || window.webkitAudioContext)();
    audioBuffer = await tempCtx.decodeAudioData(arrayBuffer);
    tempCtx.close();
  } catch (err) {
    console.error('Waveform decode failed:', err);
    return;
  }

  const rawData = audioBuffer.getChannelData(0);
  const width = canvas.width;
  const height = canvas.height;
  const midY = height / 2;
  const samplesPerPixel = Math.floor(rawData.length / width) || 1;

  ctx.fillStyle = '#8ab4f8';

  for (let x = 0; x < width; x++) {
    const start = x * samplesPerPixel;
    let min = 1.0, max = -1.0;
    for (let i = 0; i < samplesPerPixel; i++) {
      const sample = rawData[start + i];
      if (sample === undefined) break;
      if (sample < min) min = sample;
      if (sample > max) max = sample;
    }
    const barHeight = Math.max(1, (max - min) * midY);
    const y = midY - (max * midY);
    ctx.fillRect(x, y, 1, barHeight);
  }
}

// Decodes the given blob, slices it between startSec/endSec, and re-encodes
// the slice either as MP3 (if the original recording was MP3) or WAV.
async function trimAudioBlob(blob, startSec, endSec, extension) {
  const arrayBuffer = await blob.arrayBuffer();
  const tempCtx = new (window.AudioContext || window.webkitAudioContext)();
  const decoded = await tempCtx.decodeAudioData(arrayBuffer);

  const sampleRate = decoded.sampleRate;
  const startSample = Math.floor(startSec * sampleRate);
  const endSample = Math.min(Math.floor(endSec * sampleRate), decoded.length);
  const frameCount = Math.max(0, endSample - startSample);
  const numChannels = decoded.numberOfChannels;

  const sliced = tempCtx.createBuffer(numChannels, frameCount, sampleRate);
  for (let ch = 0; ch < numChannels; ch++) {
    const channelData = decoded.getChannelData(ch).subarray(startSample, endSample);
    sliced.copyToChannel(channelData, ch);
  }

  tempCtx.close();

  if (extension === 'mp3') {
    return audioBufferToMp3Blob(sliced);
  }
  return audioBufferToWavBlob(sliced);
}

// Encodes a (mono or stereo) AudioBuffer to MP3 using lamejs, chunked to
// avoid building one giant array for long recordings.
function audioBufferToMp3Blob(buffer) {
  const numChannels = Math.min(buffer.numberOfChannels, 2);
  const sampleRate = buffer.sampleRate;
  const encoder = new lamejs.Mp3Encoder(numChannels, sampleRate, 128);
  const chunks = [];
  const blockSize = 1152; // required frame size for lamejs

  const channelData = [];
  for (let ch = 0; ch < numChannels; ch++) {
    channelData.push(buffer.getChannelData(ch));
  }

  const length = buffer.length;
  for (let i = 0; i < length; i += blockSize) {
    const leftChunkFloat = channelData[0].subarray(i, i + blockSize);
    const left = floatTo16BitPCM(leftChunkFloat);

    let mp3buf;
    if (numChannels === 2) {
      const rightChunkFloat = channelData[1].subarray(i, i + blockSize);
      const right = floatTo16BitPCM(rightChunkFloat);
      mp3buf = encoder.encodeBuffer(left, right);
    } else {
      mp3buf = encoder.encodeBuffer(left);
    }

    if (mp3buf.length > 0) chunks.push(mp3buf);
  }

  const finalBuf = encoder.flush();
  if (finalBuf.length > 0) chunks.push(finalBuf);

  return new Blob(chunks, { type: 'audio/mp3' });
}

function audioBufferToWavBlob(buffer) {
  const numChannels = buffer.numberOfChannels;
  const sampleRate = buffer.sampleRate;
  const format = 1; // PCM
  const bitDepth = 16;

  const interleaved = interleaveChannels(buffer);
  const dataLength = interleaved.length * (bitDepth / 8);
  const bufferLength = 44 + dataLength;
  const arrayBuffer = new ArrayBuffer(bufferLength);
  const view = new DataView(arrayBuffer);

  writeString(view, 0, 'RIFF');
  view.setUint32(4, 36 + dataLength, true);
  writeString(view, 8, 'WAVE');
  writeString(view, 12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, format, true);
  view.setUint16(22, numChannels, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * numChannels * (bitDepth / 8), true);
  view.setUint16(32, numChannels * (bitDepth / 8), true);
  view.setUint16(34, bitDepth, true);
  writeString(view, 36, 'data');
  view.setUint32(40, dataLength, true);

  let offset = 44;
  for (let i = 0; i < interleaved.length; i++, offset += 2) {
    const s = Math.max(-1, Math.min(1, interleaved[i]));
    view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }

  return new Blob([arrayBuffer], { type: 'audio/wav' });
}

function interleaveChannels(buffer) {
  const numChannels = buffer.numberOfChannels;
  const length = buffer.length;
  const result = new Float32Array(length * numChannels);
  for (let ch = 0; ch < numChannels; ch++) {
    const channelData = buffer.getChannelData(ch);
    for (let i = 0; i < length; i++) {
      result[i * numChannels + ch] = channelData[i];
    }
  }
  return result;
}

function writeString(view, offset, string) {
  for (let i = 0; i < string.length; i++) {
    view.setUint8(offset + i, string.charCodeAt(i));
  }
}

function getTabStreamId() {
  return new Promise((resolve, reject) => {
    if (!grantedTabStreamId) {
      reject(new Error('No tab was captured. Click the extension icon again while a webpage tab is active.'));
      return;
    }
    if (tabStreamUsed) {
      reject(new Error('Tab audio can only be captured once per icon click. Close this window and click the extension icon again.'));
      return;
    }
    tabStreamUsed = true;
    resolve(grantedTabStreamId);
  });
}

// Fetches (or reuses) a raw MediaStream for the currently selected source.
async function ensureStream() {
  const wanted = sourceSelect.value;

  if (rawStream && currentSourceType === wanted) {
    return rawStream;
  }

  // Source changed or no stream yet — tear down the old one first.
  if (rawStream) {
    rawStream.getTracks().forEach((t) => t.stop());
    rawStream = null;
  }

  if (wanted === 'tab') {
    const streamId = await getTabStreamId();
    rawStream = await navigator.mediaDevices.getUserMedia({
      audio: {
        mandatory: {
          chromeMediaSource: 'tab',
          chromeMediaSourceId: streamId
        }
      }
    });
  } else {
    rawStream = await navigator.mediaDevices.getUserMedia({ audio: true });
  }

  currentSourceType = wanted;
  return rawStream;
}

// Builds the shared Web Audio graph on top of rawStream.
function buildAudioGraph(stream, sourceType) {
  audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  sourceNode = audioCtx.createMediaStreamSource(stream);

  // For tab audio, route back to speakers so playback isn't silenced
  // while we're capturing it. For mic, skip this to avoid feedback/echo.
  if (sourceType === 'tab') {
    sourceNode.connect(audioCtx.destination);
  }

  // Destination node whose .stream feeds MediaRecorder for webm mode.
  destNode = audioCtx.createMediaStreamDestination();
  sourceNode.connect(destNode);
}

function teardownAudioGraph() {
  if (processorNode) { processorNode.disconnect(); processorNode = null; }
  if (destNode) { destNode.disconnect(); destNode = null; }
  if (sourceNode) { sourceNode.disconnect(); sourceNode = null; }
  if (audioCtx) { audioCtx.close(); audioCtx = null; }
}

// ===== Start / Stop =====
async function startRecording() {
  if (isRecording || isStarting) return;
  isStarting = true;

  let stream;
  try {
    stream = await ensureStream();
  } catch (err) {
    statusEl.textContent = err.message || 'Could not access audio source.';
    isStarting = false;
    return;
  }

  buildAudioGraph(stream, currentSourceType);
  startWebmRecording(); // always capture as webm; converted to mp3 (if needed) after Stop

  startTime = Date.now();
  elapsedBeforePause = 0;
  timerInterval = setInterval(updateTimer, 250);
  isRecording = true;
  isPaused = false;
  isStarting = false;

  const sourceLabel = currentSourceType === 'tab' ? 'tab audio' : 'microphone';
  statusEl.innerHTML = `<span class="rec-dot"></span>Recording ${sourceLabel}...`;
  startBtn.disabled = true;
  pauseBtn.disabled = false;
  stopBtn.disabled = false;
  formatSelect.disabled = true;
  sourceSelect.disabled = true;
}

function startWebmRecording() {
  audioChunks = [];
  mediaRecorder = new MediaRecorder(destNode.stream);

  mediaRecorder.ondataavailable = (e) => {
    if (e.data.size > 0) audioChunks.push(e.data);
  };

  mediaRecorder.onstop = async () => {
    const webmBlob = new Blob(audioChunks, { type: 'audio/webm' });
    teardownAudioGraph();

    const wantedFormat = formatSelect.value;
    if (wantedFormat === 'mp3') {
      statusEl.textContent = 'Encoding to MP3...';
      try {
        const mp3Blob = await webmBlobToMp3(webmBlob);
        addRecordingCard(mp3Blob, 'mp3');
      } catch (err) {
        console.error(err);
        statusEl.textContent = 'MP3 conversion failed — saved as WEBM instead.';
        addRecordingCard(webmBlob, 'webm');
        return;
      }
    } else {
      addRecordingCard(webmBlob, 'webm');
    }
    statusEl.textContent = 'Recording saved below.';
  };

  mediaRecorder.start();
}

// Decodes a recorded webm blob and re-encodes it to MP3 via lamejs.
async function webmBlobToMp3(webmBlob) {
  const arrayBuffer = await webmBlob.arrayBuffer();
  const tempCtx = new (window.AudioContext || window.webkitAudioContext)();
  const decoded = await tempCtx.decodeAudioData(arrayBuffer);
  tempCtx.close();
  return audioBufferToMp3Blob(decoded);
}

function floatTo16BitPCM(input) {
  const output = new Int16Array(input.length);
  for (let i = 0; i < input.length; i++) {
    const s = Math.max(-1, Math.min(1, input[i]));
    output[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  return output;
}

function pauseRecording() {
  if (!isPaused) {
    isPaused = true;
    if (mediaRecorder.state === 'recording') {
      mediaRecorder.pause();
    }
    clearInterval(timerInterval);
    elapsedBeforePause += Date.now() - startTime;
    pauseBtn.textContent = 'Resume';
    statusEl.textContent = 'Paused';
  } else {
    isPaused = false;
    if (mediaRecorder.state === 'paused') {
      mediaRecorder.resume();
    }
    startTime = Date.now();
    timerInterval = setInterval(updateTimer, 250);
    pauseBtn.textContent = 'Pause';
    statusEl.innerHTML = '<span class="rec-dot"></span>Recording...';
  }
}

function stopRecording() {
  clearInterval(timerInterval);
  isRecording = false;
  isPaused = false;

  mediaRecorder.stop(); // onstop handler does the format conversion + card creation

  statusEl.textContent = 'Processing recording...';
  startBtn.disabled = false;
  pauseBtn.disabled = true;
  pauseBtn.textContent = 'Pause';
  stopBtn.disabled = true;
  formatSelect.disabled = false;
  sourceSelect.disabled = false;
  timerEl.textContent = '00:00';
}

// ===== Manual button events =====
startBtn.addEventListener('click', startRecording);
pauseBtn.addEventListener('click', pauseRecording);
stopBtn.addEventListener('click', () => {
  stopRecording();
  stopVoiceMonitorSilenceTimer();
});

// ===== Auto start/stop on sound (voice activity detection) =====
async function startVoiceMonitor() {
  let stream;
  try {
    stream = await ensureStream();
  } catch (err) {
    autoStatus.textContent = err.message || 'Could not access audio source.';
    autoDetectToggle.checked = false;
    return;
  }

  monitorCtx = new (window.AudioContext || window.webkitAudioContext)();
  monitorSource = monitorCtx.createMediaStreamSource(stream);

  // Keep tab audio audible while monitoring, even before recording starts.
  if (currentSourceType === 'tab') {
    monitorSource.connect(monitorCtx.destination);
  }

  monitorAnalyser = monitorCtx.createAnalyser();
  monitorAnalyser.fftSize = 512;
  monitorSource.connect(monitorAnalyser);

  const dataArray = new Uint8Array(monitorAnalyser.fftSize);
  autoStatus.textContent = 'Listening for sound...';

  function checkVolume() {
    monitorAnalyser.getByteTimeDomainData(dataArray);
    let sumSquares = 0;
    for (let i = 0; i < dataArray.length; i++) {
      const norm = (dataArray[i] - 128) / 128;
      sumSquares += norm * norm;
    }
    const rms = Math.sqrt(sumSquares / dataArray.length);

    if (rms > START_THRESHOLD) {
      if (!isRecording && !isStarting) {
        startRecording();
        autoStatus.textContent = 'Sound detected — recording started.';
      }
      if (silenceTimer) {
        clearTimeout(silenceTimer);
        silenceTimer = null;
      }
    } else if (isRecording && !silenceTimer) {
      silenceTimer = setTimeout(() => {
        if (isRecording) {
          stopRecording();
          autoStatus.textContent = 'Silence detected — recording stopped.';
        }
        silenceTimer = null;
      }, STOP_SILENCE_MS);
    }

    monitorRaf = requestAnimationFrame(checkVolume);
  }

  checkVolume();
}

function stopVoiceMonitor() {
  if (monitorRaf) cancelAnimationFrame(monitorRaf);
  if (monitorSource) monitorSource.disconnect();
  if (monitorCtx) monitorCtx.close();
  monitorCtx = null;
  monitorSource = null;
  monitorAnalyser = null;
  monitorRaf = null;
  stopVoiceMonitorSilenceTimer();
  autoStatus.textContent = '';
}

function stopVoiceMonitorSilenceTimer() {
  if (silenceTimer) {
    clearTimeout(silenceTimer);
    silenceTimer = null;
  }
}

autoDetectToggle.addEventListener('change', () => {
  if (autoDetectToggle.checked) {
    startBtn.disabled = true;
    pauseBtn.disabled = true;
    stopBtn.disabled = true;
    sourceSelect.disabled = true;
    startVoiceMonitor();
  } else {
    startBtn.disabled = isRecording;
    pauseBtn.disabled = !isRecording;
    stopBtn.disabled = !isRecording;
    sourceSelect.disabled = isRecording;
    stopVoiceMonitor();
    if (isRecording) {
      stopRecording();
    }
  }
});
