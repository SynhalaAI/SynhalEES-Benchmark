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
const alwaysOnTopToggle = document.getElementById('alwaysOnTopToggle');

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
  if (!isFinite(ms) || ms < 0) return '--:--';
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
      <button class="replay-btn">↺ Replay</button>
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
  const replayBtn = card.querySelector('.replay-btn');
  const timeLabelEl = card.querySelector('.time-label');
  const waveformCanvas = card.querySelector('.waveform-canvas');
  const startSlider = card.querySelector('.trim-start');
  const endSlider = card.querySelector('.trim-end');
  const startLabel = card.querySelector('.trim-start-label');
  const endLabel = card.querySelector('.trim-end-label');
  const trimBtn = card.querySelector('.btn-trim');
  const removeBtn = card.querySelector('.btn-remove');

  // Track every object URL created for this card so we can revoke them on removal.
  const objectUrls = [url];

  let duration = 0;        // resolved once metadata (or the Infinity hack) loads
  let wavePeaks = null;    // cached min/max per column — avoids re-decoding on redraws
  let dragState = null;    // { startFrac, curFrac } while a crop drag is in progress
  let savedTrim = null;    // selection snapshot while a plain click seeks (restored on pointerup)

  // audioDuration() falls back to the resolved `duration` while the element
  // still reports a non-finite duration (MediaRecorder webm quirk).
  const audioDuration = () =>
    isFinite(audioEl.duration) && audioEl.duration > 0 ? audioEl.duration : duration;

  removeBtn.addEventListener('click', () => {
    audioEl.pause();
    audioEl.src = '';
    objectUrls.forEach((u) => URL.revokeObjectURL(u));
    card.remove();
  });

  // ===== Play button state is driven entirely by the media element =====
  // Auto-stop at trim end, natural end, src swaps and failed play() calls
  // all change the element state — deriving the label from events means it
  // can never drift out of sync (the old manual text sets could).
  const setPlayBtn = (playing) => { playBtn.textContent = playing ? '⏸ Stop' : '▶ Play'; };

  // ===== Playhead indicator (drawn on the waveform) =====
  let playheadFrac = null; // null until the clip has been played/seeked
  let playheadRaf = 0;
  let playStartFrac = null; // where playback started — gates trim-end auto-stop

  function updatePlayhead() {
    const dur = audioDuration();
    if (dur > 0 && isFinite(audioEl.currentTime)) {
      playheadFrac = Math.min(1, Math.max(0, audioEl.currentTime / dur));
    } else {
      playheadFrac = null;
    }
  }

  // rAF loop keeps the playhead smooth (~60fps); timeupdate alone is ~4fps.
  function startPlayheadLoop() {
    stopPlayheadLoop();
    const tick = () => {
      updatePlayhead();
      renderWave();
      playheadRaf = requestAnimationFrame(tick);
    };
    playheadRaf = requestAnimationFrame(tick);
  }

  function stopPlayheadLoop() {
    if (playheadRaf) {
      cancelAnimationFrame(playheadRaf);
      playheadRaf = 0;
    }
  }

  audioEl.addEventListener('play', () => { setPlayBtn(true); startPlayheadLoop(); });
  audioEl.addEventListener('pause', () => { setPlayBtn(false); stopPlayheadLoop(); updatePlayhead(); renderWave(); });
  audioEl.addEventListener('ended', () => { setPlayBtn(false); stopPlayheadLoop(); updatePlayhead(); renderWave(); });
  audioEl.addEventListener('emptied', () => { setPlayBtn(false); stopPlayheadLoop(); playheadFrac = null; renderWave(); });
  audioEl.addEventListener('seeked', () => { updatePlayhead(); if (audioEl.paused) renderWave(); });

  audioEl.addEventListener('loadedmetadata', () => {
    if (isFinite(audioEl.duration) && audioEl.duration > 0) {
      duration = audioEl.duration;
    } else {
      fixInfiniteDuration();
      return;
    }
    timeLabelEl.textContent = `0:00 / ${formatTime(duration * 1000)}`;
    endLabel.textContent = formatTime(duration * 1000);
    syncSliderLabels();
  });

  // MediaRecorder-produced webm blobs often report Infinity duration until
  // the element is seeked past the end; this standard workaround forces
  // Chrome to discover the real duration.
  function fixInfiniteDuration() {
    const discover = () => {
      audioEl.removeEventListener('timeupdate', discover);
      if (isFinite(audioEl.duration) && audioEl.duration > 0) duration = audioEl.duration;
      try { audioEl.currentTime = 0; } catch (e) { /* ignore */ }
      timeLabelEl.textContent = `0:00 / ${formatTime(duration * 1000)}`;
      endLabel.textContent = formatTime(duration * 1000);
      syncSliderLabels();
    };
    audioEl.addEventListener('timeupdate', discover);
    try { audioEl.currentTime = 1e101; } catch (e) { /* ignore */ }
  }

  audioEl.addEventListener('timeupdate', () => {
    updatePlayhead();
    const dur = audioDuration();
    timeLabelEl.textContent = `${formatTime(audioEl.currentTime * 1000)} / ${formatTime(dur * 1000)}`;
    // Stop playback automatically once it reaches the trim end marker —
    // but only when playback started INSIDE the selection. Starting from a
    // waveform click beyond the trim end keeps playing to the end of the
    // clip instead of instantly pausing.
    // (The button label is kept honest by the play/pause events above.)
    const startLimit = playStartFrac === null ? 0 : playStartFrac;
    if (
      dur > 0 &&
      startLimit < (endSlider.value / 1000) &&
      audioEl.currentTime >= (endSlider.value / 1000) * dur
    ) {
      audioEl.pause();
    }
  });

  playBtn.addEventListener('click', () => {
    if (audioEl.paused) {
      // Play only within the selected trim range
      const dur = audioDuration();
      const startTime = (startSlider.value / 1000) * dur;
      if (audioEl.currentTime < startTime || audioEl.currentTime >= (endSlider.value / 1000) * dur) {
        audioEl.currentTime = startTime;
      }
      if (dur > 0) playStartFrac = audioEl.currentTime / dur;
      const playPromise = audioEl.play();
      // play() can reject (e.g. unsupported source) — keep the button honest.
      if (playPromise && typeof playPromise.catch === 'function') {
        playPromise.catch((err) => {
          console.error('Playback failed:', err);
          setPlayBtn(false);
        });
      }
    } else {
      audioEl.pause();
    }
  });

  // Replay: jump back to the trim start and play again (works while playing too).
  replayBtn.addEventListener('click', () => {
    const dur = audioDuration();
    audioEl.currentTime = (startSlider.value / 1000) * dur;
    if (dur > 0) playStartFrac = audioEl.currentTime / dur;
    updatePlayhead();
    renderWave();
    const playPromise = audioEl.play();
    if (playPromise && typeof playPromise.catch === 'function') {
      playPromise.catch((err) => {
        console.error('Playback failed:', err);
        setPlayBtn(false);
      });
    }
  });

  // ===== Waveform: cached peaks + crop-selection highlight =====

  // Decodes once and caches per-column peaks, then paints the waveform with
  // the to-be-cropped region darkened. Re-decoding on every drag frame would
  // be far too slow, hence the cache.
  async function loadWaveform() {
    try {
      wavePeaks = await computeWaveformPeaks(blob, waveformCanvas.width);
    } catch (err) {
      console.error('Waveform decode failed:', err);
      wavePeaks = null;
    }
    renderWave();
  }

  function renderWave() {
    renderWaveform(waveformCanvas, wavePeaks, startSlider.value / 1000, endSlider.value / 1000, playheadFrac);
  }

  function syncSliderLabels() {
    if (parseInt(startSlider.value) > parseInt(endSlider.value)) {
      startSlider.value = endSlider.value;
    }
    startLabel.textContent = formatTime((startSlider.value / 1000) * audioDuration() * 1000);
    endLabel.textContent = formatTime((endSlider.value / 1000) * audioDuration() * 1000);
  }

  // Pushes a [startFrac, endFrac] selection (0..1) into the sliders, labels
  // and waveform highlight — one code path shared by drag + sliders.
  function syncTrim(startFrac, endFrac) {
    startSlider.value = String(Math.round(startFrac * 1000));
    endSlider.value = String(Math.round(endFrac * 1000));
    syncSliderLabels();
    renderWave();
  }

  // Drag directly on the waveform with the crosshair cursor to choose the
  // region Trim & Save keeps; everything outside it is shown darkened.
  const pointerFrac = (e) => {
    const rect = waveformCanvas.getBoundingClientRect();
    if (!rect.width) return 0;
    return Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width));
  };

  waveformCanvas.addEventListener('pointerdown', (e) => {
    e.preventDefault();
    try { waveformCanvas.setPointerCapture(e.pointerId); } catch (err) { /* ignore */ }
    const frac = pointerFrac(e);
    dragState = { startFrac: frac, curFrac: frac };
    savedTrim = { start: parseInt(startSlider.value, 10), end: parseInt(endSlider.value, 10) };
    syncTrim(frac, frac);
  });

  waveformCanvas.addEventListener('pointermove', (e) => {
    if (!dragState) return;
    dragState.curFrac = pointerFrac(e);
    const a = Math.min(dragState.startFrac, dragState.curFrac);
    const b = Math.max(dragState.startFrac, dragState.curFrac);
    syncTrim(a, b);
  });

  waveformCanvas.addEventListener('pointerup', () => {
    if (!dragState) return;
    const a = Math.min(dragState.startFrac, dragState.curFrac);
    const b = Math.max(dragState.startFrac, dragState.curFrac);
    dragState = null;
    if (b - a < 0.01) {
      // Plain click (no real drag) — restore the selection the user had,
      // then seek to the clicked spot and play from there. The playhead
      // stays exactly where the user clicked.
      if (savedTrim) {
        startSlider.value = String(savedTrim.start);
        endSlider.value = String(savedTrim.end);
        syncSliderLabels();
      }
      savedTrim = null;
      const dur = audioDuration();
      if (dur <= 0) {
        renderWave();
        return; // duration not known yet — nothing to seek
      }
      const frac = Math.min(1, Math.max(0, a));
      audioEl.currentTime = frac * dur;
      playStartFrac = frac;
      updatePlayhead();
      renderWave();
      const playPromise = audioEl.play();
      if (playPromise && typeof playPromise.catch === 'function') {
        playPromise.catch((err) => {
          console.error('Playback failed:', err);
          setPlayBtn(false);
        });
      }
      return;
    }
    syncTrim(a, b);
  });

  waveformCanvas.addEventListener('pointercancel', () => {
    dragState = null;
    if (savedTrim) {
      startSlider.value = String(savedTrim.start);
      endSlider.value = String(savedTrim.end);
      syncSliderLabels();
      savedTrim = null;
    }
    renderWave();
  });

  // Double-click resets the crop selection back to the full clip
  // (single click now seeks/plays instead).
  waveformCanvas.addEventListener('dblclick', () => {
    savedTrim = null;
    syncTrim(0, 1);
  });

  startSlider.addEventListener('input', () => { syncSliderLabels(); renderWave(); });
  endSlider.addEventListener('input', () => { syncSliderLabels(); renderWave(); });

  trimBtn.addEventListener('click', async () => {
    trimBtn.disabled = true;
    trimBtn.textContent = 'Trimming...';
    try {
      const dur = audioDuration();
      const startTime = (startSlider.value / 1000) * dur;
      const endTime = (endSlider.value / 1000) * dur;
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
      setPlayBtn(false); // the src swap aborts any playback — keep the button in sync
      const downloadLink = card.querySelector('.btn-download');
      downloadLink.href = trimmedUrl;
      downloadLink.download = `recording-trimmed-${Date.now()}.${outExt}`;
      downloadLink.textContent = 'Download';
      card.querySelector('.recording-card-header span').textContent = `${timeLabel} · trimmed .${outExt}`;

      startSlider.value = 0;
      endSlider.value = 1000;
      playheadFrac = null;

      await loadWaveform(); // recompute peaks for the trimmed clip
      syncSliderLabels();
    } catch (err) {
      console.error(err);
      alert('Trim failed: ' + err.message);
    }
    trimBtn.disabled = false;
    trimBtn.textContent = 'Trim & Save';
  });

  loadWaveform();
}

// Decodes a blob once and computes per-column min/max peaks for the
// waveform. The result is cached per card so redraws (crop drags, slider
// sync) don't re-decode the audio every frame.
async function computeWaveformPeaks(blob, width) {
  const arrayBuffer = await blob.arrayBuffer();
  const tempCtx = new (window.AudioContext || window.webkitAudioContext)();
  const audioBuffer = await tempCtx.decodeAudioData(arrayBuffer);
  tempCtx.close();

  const rawData = audioBuffer.getChannelData(0);
  const samplesPerPixel = Math.floor(rawData.length / width) || 1;
  const mins = new Float32Array(width);
  const maxs = new Float32Array(width);

  for (let x = 0; x < width; x++) {
    const start = x * samplesPerPixel;
    let min = 1.0, max = -1.0;
    for (let i = 0; i < samplesPerPixel; i++) {
      const sample = rawData[start + i];
      if (sample === undefined) break;
      if (sample < min) min = sample;
      if (sample > max) max = sample;
    }
    mins[x] = min;
    maxs[x] = max;
  }
  return { mins, maxs };
}

// Paints the cached waveform bars, then darkens the parts that Trim & Save
// will crop away (everything outside [startFrac, endFrac]), marks the
// selection edges, and draws the playback playhead when one is active.
// This is the "what will be cropped" visual the sliders, the waveform drag
// and the playhead loop all feed into.
function renderWaveform(canvas, peaks, startFrac, endFrac, playheadFrac = null) {
  const ctx = canvas.getContext('2d');
  const width = canvas.width;
  const height = canvas.height;
  const midY = height / 2;
  ctx.clearRect(0, 0, width, height);
  if (!peaks) return;

  ctx.fillStyle = '#8ab4f8';
  for (let x = 0; x < width; x++) {
    const min = peaks.mins[x];
    const max = peaks.maxs[x];
    const barHeight = Math.max(1, (max - min) * midY);
    const y = midY - (max * midY);
    ctx.fillRect(x, y, 1, barHeight);
  }

  const startX = Math.round(startFrac * width);
  const endX = Math.round(endFrac * width);

  // Darken the region that will be cropped away.
  ctx.fillStyle = 'rgba(0, 0, 0, 0.7)';
  if (startX > 0) ctx.fillRect(0, 0, startX, height);
  if (endX < width) ctx.fillRect(endX, 0, width - endX, height);

  // Bright markers on the selection boundaries.
  ctx.fillStyle = '#f5a623';
  if (startX > 0 && startX < width) ctx.fillRect(startX - 1, 0, 1, height);
  if (endX > 0 && endX < width) ctx.fillRect(Math.min(endX, width - 1), 0, 1, height);

  // Playhead: red line showing the current playback position, on top of
  // the bars, the crop dimming and the edge markers.
  if (playheadFrac !== null && playheadFrac !== undefined && isFinite(playheadFrac)) {
    const playX = Math.round(playheadFrac * width);
    if (playX >= 0 && playX < width) {
      ctx.fillStyle = '#ff5252';
      ctx.fillRect(playX, 0, 2, height);
      ctx.fillRect(playX - 1, 0, 4, 2); // small cap on top for visibility
    }
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

// ===== Always on top (Document Picture-in-Picture) =====
// Chrome exposes no API to mark an extension window always-on-top, so the
// UI is moved into a Document Picture-in-Picture window, which the OS keeps
// floating above other windows. Only the DOM moves — the recorder, the
// timer and playback all keep running from this document.
//
// The original extension window cannot be *closed* (closing the opener
// would unload the PiP document too), so it is *minimized* while the
// floating window is active and restored when always-on-top ends. Without
// this the user ends up with two recorder windows — the original one blank.
let recorderWinId = null;
let recorderWinRestoreState = 'normal';
let pipMovedNodes = null;

// Remembers the original window's state and minimizes it so only the
// floating always-on-top window stays visible.
async function saveAndMinimizeRecorderWindow() {
  try {
    const win = await chrome.windows.getCurrent();
    recorderWinId = win.id;
    recorderWinRestoreState = win.state === 'maximized' ? 'maximized' : 'normal';
    await chrome.windows.update(win.id, { state: 'minimized' });
    return true;
  } catch (err) {
    console.error('Could not minimize the recorder window:', err);
    recorderWinId = null;
    return false;
  }
}

// Brings the original window back from the taskbar. Best-effort: if the
// user closed it manually in the meantime the error is swallowed — the UI
// nodes are restored into this document either way.
function restoreRecorderWindow() {
  if (recorderWinId === null) return;
  const winId = recorderWinId;
  const state = recorderWinRestoreState;
  recorderWinId = null;
  try {
    chrome.windows.update(winId, { state, focused: true }, () => {
      void chrome.runtime.lastError; // window may already be gone — ignore
    });
  } catch (err) { /* ignore */ }
}

// ===== Always on top (Document Picture-in-Picture) =====
// Chrome exposes no API to mark an extension window always-on-top, so the
// UI is moved into a Document Picture-in-Picture window, which the OS keeps
// floating above other windows. Only the DOM moves — the recorder, the
// timer and playback all keep running from this document.
let pipWindow = null;

alwaysOnTopToggle.addEventListener('change', async () => {
  if (alwaysOnTopToggle.checked) {
    await enterAlwaysOnTop();
  } else {
    exitAlwaysOnTop();
  }
});

async function enterAlwaysOnTop() {
  if (pipWindow) return;

  if (!('documentPictureInPicture' in window)) {
    statusEl.textContent = 'Always on top needs Chrome 116 or newer.';
    alwaysOnTopToggle.checked = false;
    return;
  }

  try {
    pipWindow = await window.documentPictureInPicture.requestWindow({ width: 400, height: 660 });
  } catch (err) {
    console.error(err);
    statusEl.textContent = 'Could not open the always-on-top window: ' + (err.message || err);
    alwaysOnTopToggle.checked = false;
    return;
  }

  // Mirror the page styles into the PiP document.
  document.querySelectorAll('style').forEach((styleEl) => {
    const copy = pipWindow.document.createElement('style');
    copy.textContent = styleEl.textContent;
    pipWindow.document.head.appendChild(copy);
  });
  const override = pipWindow.document.createElement('style');
  override.textContent =
    'body { width: 100% !important; height: 100%; display: flex; flex-direction: column; }';
  pipWindow.document.head.appendChild(override);

  // Adopting an <audio> into another document can pause it — remember the
  // playing ones so they can be resumed right after the move.
  const wasPlaying = [...document.querySelectorAll('audio.player-audio')].filter((a) => !a.paused);

  pipMovedNodes = [...document.body.children].filter((n) => n.tagName === 'DIV');
  pipMovedNodes.forEach((n) => pipWindow.document.body.appendChild(n));
  wasPlaying.forEach((a) => { if (a.paused) a.play().catch(() => {}); });

  pipWindow.document.title = 'Simple Audio Recorder — Always on top';
  await saveAndMinimizeRecorderWindow();
  statusEl.textContent = 'Always on top enabled.';

  // The user closed the floating window — restore the original window and
  // move the UI back home.
  pipWindow.addEventListener('pagehide', () => {
    pipWindow = null;
    alwaysOnTopToggle.checked = false;
    restoreRecorderWindow();
    if (!pipMovedNodes) return;
    pipMovedNodes.forEach((n) => document.body.appendChild(n));
    pipMovedNodes = null;
    statusEl.textContent = 'Always on top disabled.';
  });
}

function exitAlwaysOnTop() {
  if (!pipWindow) return;
  const win = pipWindow;
  pipWindow = null;
  win.close(); // the 'pagehide' handler restores the UI
}
