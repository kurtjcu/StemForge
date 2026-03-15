/**
 * Synth tab — Stable Audio Open generation + SFX Stem Builder.
 *
 * Generate audio clips, then place them on an SFX canvas with
 * per-clip volume/fade controls. A DAW-style multi-track timeline
 * shows the reference stem and clip placements on a shared time axis.
 */

import { appState, api, pollJob, el, formatTime, saveFileAs } from '../app.js';
import { createWaveform } from './waveform.js';
import { transportLoad, transportStop } from './audio-player.js';

function clearChildren(elem) {
  while (elem.firstChild) elem.removeChild(elem.firstChild);
}

/**
 * Create a click-to-edit name label. getPath returns the current audio path;
 * onRenamed is called with the new path after a successful rename.
 */
function makeEditableName(initialName, getPath, onRenamed) {
  let currentName = initialName;
  const span = el('span', { className: 'stem-label editable-name', title: 'Click to rename' }, currentName);

  span.addEventListener('click', () => {
    const input = el('input', {
      type: 'text',
      className: 'editable-name-input',
      value: currentName,
    });
    span.replaceWith(input);
    input.focus();
    input.select();

    let committed = false;
    async function commit() {
      if (committed) return;
      committed = true;
      const newName = input.value.trim();
      if (newName && newName !== currentName) {
        try {
          const res = await api('/sfx/rename-clip', {
            method: 'POST',
            body: JSON.stringify({ path: getPath(), new_name: newName }),
          });
          currentName = newName;
          span.textContent = newName;
          if (onRenamed) onRenamed(res.new_path);
        } catch (err) {
          alert(`Rename failed: ${err.message}`);
        }
      }
      input.replaceWith(span);
    }

    input.addEventListener('blur', commit);
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); commit(); }
      if (e.key === 'Escape') { committed = true; input.replaceWith(span); }
    });
  });

  return span;
}

// ─── Inline audio players (exclusive playback) ───────────────────────────

/** All active inline players — playing one stops the others. */
const _players = [];

function _stopOtherPlayers(except) {
  for (const p of _players) {
    if (p.ws !== except && p.ws.isPlaying()) {
      p.ws.stop();
      p.playBtn.textContent = '\u25B6 Play';
    }
  }
}

/**
 * Build a standard stem-card player with Play/Stop/Rewind/time/Save + waveform.
 * Returns { card, ws, setUrl(url, label) } so the URL can be updated later.
 */
function createStemPlayer(label, url, { getUrl, saveLabel, extraButtons = [] } = {}) {
  const playBtn = el('button', { className: 'btn btn-sm' }, '\u25B6 Play');
  const stopBtn = el('button', { className: 'btn btn-sm' }, '\u25A0 Stop');
  const rewindBtn = el('button', { className: 'btn btn-sm' }, '\u23EA Rewind');
  const timeLabel = el('span', { className: 'stem-time' }, '0:00 / 0:00');
  const saveBtn = el('button', { className: 'btn btn-sm' }, '\u2193 Save');

  const labelSpan = typeof label === 'string'
    ? el('span', { className: 'stem-label' }, label) : label;

  const card = el('div', { className: 'stem-card' },
    el('div', { className: 'stem-card-header' },
      labelSpan,
      el('div', { className: 'stem-actions' },
        playBtn, stopBtn, rewindBtn, timeLabel, saveBtn, ...extraButtons,
      ),
    ),
  );

  const waveContainer = el('div', { className: 'stem-waveform' });
  card.appendChild(waveContainer);

  const ws = createWaveform(waveContainer, { height: 50 });
  if (url) ws.load(url);

  const player = { ws, playBtn };
  _players.push(player);

  const _getLabel = () => typeof label === 'string' ? label : 'Synth';

  playBtn.addEventListener('click', () => {
    if (ws.isPlaying()) {
      ws.pause();
      playBtn.textContent = '\u25B6 Play';
    } else {
      _stopOtherPlayers(ws);
      ws.play();
      playBtn.textContent = '\u23F8 Pause';
      const currentUrl = getUrl ? getUrl() : url;
      transportLoad(currentUrl, _getLabel(), false, 'Synth');
    }
  });

  stopBtn.addEventListener('click', () => {
    ws.stop();
    transportStop();
    playBtn.textContent = '\u25B6 Play';
  });

  rewindBtn.addEventListener('click', () => ws.setTime(0));

  ws.on('timeupdate', (time) => {
    const dur = ws.getDuration();
    timeLabel.textContent = `${formatTime(time)} / ${formatTime(dur)}`;
  });
  ws.on('finish', () => { playBtn.textContent = '\u25B6 Play'; transportStop(); });

  saveBtn.addEventListener('click', () => {
    const currentLabel = saveLabel || url || '';
    const name = currentLabel.split('/').pop() || 'audio.wav';
    const path = getUrl ? getUrl() : url;
    // Build download URL from the stream URL or raw path
    const dlUrl = path?.includes('/api/audio/stream')
      ? path.replace('/audio/stream', '/audio/download')
      : `/api/audio/download?path=${encodeURIComponent(path || '')}`;
    saveFileAs(dlUrl, name);
  });

  function setUrl(newUrl, newLabel) {
    ws.load(newUrl);
    if (newLabel && typeof labelSpan.textContent !== 'undefined') {
      labelSpan.textContent = newLabel;
    }
  }

  return { card, ws, setUrl };
}

// ─── Module state ─────────────────────────────────────────────────────────

let _currentSfxId = null;
let _canvasPlayer = null;      // createStemPlayer instance for canvas playback
let _alignAudioPath = null;    // resolved audio path for reference lane
let _alignStemType = null;     // 'audio' | 'midi'
let _refPlayer = null;         // createStemPlayer instance for reference stem
let _timelineDurationMs = 0;   // current canvas duration in ms
let _alignedStemPaths = {};    // label → path, from stemsReady (audio, green)
let _alignedMidiLabels = [];   // labels, from midiReady (purple, rendered on demand)
let _alignedRefPaths = {};     // label → path, from fileLoaded or manual import
let _activeClipId = null;      // currently selected placement id
let _dragOffsetPct = 0;        // offset from left edge of dragged clip (0-1)

export function initGenerate() {
  const panel = document.getElementById('panel-synth');
  const layout = el('div', { className: 'two-col' });

  // ═══════════════════════════════════════════════════════════════════════
  // Left column: generation controls + SFX canvas setup
  // ═══════════════════════════════════════════════════════════════════════
  const left = el('div', { className: 'col-left' });

  // -- Generation controls --
  const promptGroup = el('div', { className: 'form-group' },
    el('label', {}, 'Prompt (required)'),
    el('textarea', { id: 'gen-prompt', rows: '3', placeholder: 'Describe the audio to generate...' }),
  );

  const durationGroup = el('div', { className: 'form-group' },
    el('label', {}, 'Duration (seconds)'),
    el('div', { className: 'slider-row' },
      el('input', { type: 'range', id: 'gen-duration', min: '0', max: '120', value: '30', step: '5' }),
      el('span', { className: 'slider-value', id: 'gen-duration-val' }, '30s'),
    ),
  );

  const stepsGroup = el('div', { className: 'form-group' },
    el('label', {}, 'Steps'),
    el('div', { className: 'slider-row' },
      el('input', { type: 'range', id: 'gen-steps', min: '10', max: '200', value: '100', step: '10' }),
      el('span', { className: 'slider-value', id: 'gen-steps-val' }, '100'),
    ),
  );

  const cfgGroup = el('div', { className: 'form-group' },
    el('label', {}, 'CFG Scale'),
    el('div', { className: 'slider-row' },
      el('input', { type: 'range', id: 'gen-cfg', min: '1', max: '15', value: '7', step: '0.5' }),
      el('span', { className: 'slider-value', id: 'gen-cfg-val' }, '7.0'),
    ),
  );

  // Conditioning
  const condSection = el('div', { className: 'conditioning-section' },
    el('div', { className: 'card-header' }, 'CONDITIONING'),
    el('div', { className: 'form-group' },
      el('label', {}, 'Source'),
      el('select', { id: 'gen-cond-source' },
        el('option', { value: 'none' }, 'None'),
        el('option', { value: 'audio' }, 'Audio stem'),
        el('option', { value: 'midi' }, 'Session MIDI'),
        el('option', { value: 'mix' }, 'Mix render'),
      ),
    ),
    el('div', { className: 'form-group hidden', id: 'gen-cond-audio-group' },
      el('label', {}, 'Audio stem'),
      el('select', { id: 'gen-cond-audio' }),
    ),
  );

  // Vocal Preservation
  const vpGroup = el('div', { className: 'form-group' },
    el('label', { style: { display: 'flex', alignItems: 'center', gap: '8px' } },
      el('span', { className: 'toggle' },
        el('input', { type: 'checkbox', id: 'gen-vp' }),
        el('span', { className: 'toggle-slider' }),
      ),
      'Vocal Preservation Mode',
    ),
  );

  const genRow = el('div', { style: { display: 'flex', gap: '8px', alignItems: 'center' } },
    el('button', { className: 'btn btn-primary', id: 'gen-start', style: { flex: '1' } }, 'Generate'),
    el('button', { className: 'btn', id: 'sfx-add-sound-btn', style: { flex: '1' } }, '+ Add Sound'),
    el('input', { type: 'file', id: 'sfx-add-sound-input', accept: '.wav,.flac,.mp3,.ogg,.aiff', style: { display: 'none' } }),
  );

  // -- SFX Reference (pick before creating a canvas) --
  const sfxRefCard = el('div', { className: 'card', style: { marginTop: '8px' } },
    el('div', { className: 'card-header' }, 'SFX REFERENCE'),
    el('div', { className: 'form-group', style: { margin: '4px 0' } },
      el('div', { style: { display: 'flex', justifyContent: 'space-between', alignItems: 'center' } },
        el('label', { style: { fontSize: '11px', textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--text-dim)', margin: '0' } }, 'Align to'),
        el('button', { className: 'btn btn-sm', id: 'sfx-align-import-btn', style: { padding: '2px 8px', fontSize: '11px' } }, '+ Load Reference'),
      ),
      el('input', { type: 'file', id: 'sfx-align-import-input', accept: '.wav,.flac,.mp3,.ogg', style: { display: 'none' } }),
      el('select', { id: 'sfx-align-select', style: { width: '100%' } },
        el('option', { value: '' }, '-- none --'),
      ),
    ),
  );

  // -- SFX Canvas setup --
  const sfxSetupCard = el('div', { className: 'card', style: { marginTop: '8px' } },
    el('div', { className: 'card-header' }, 'SFX CANVAS'),
    el('div', { className: 'form-group' },
      el('label', {}, 'Canvas name'),
      el('input', { type: 'text', id: 'sfx-name', value: 'Untitled SFX', placeholder: 'SFX stem name' }),
    ),
    el('div', { className: 'form-group' },
      el('label', {}, 'Canvas duration (seconds)'),
      el('div', { className: 'slider-row' },
        el('input', { type: 'range', id: 'sfx-duration', min: '0', max: '120', value: '30', step: '1' }),
        el('span', { className: 'slider-value', id: 'sfx-duration-val' }, '30s'),
      ),
    ),
    el('div', { style: { display: 'flex', gap: '8px', marginTop: '4px' } },
      el('button', { className: 'btn btn-primary', id: 'sfx-create-btn' }, 'New Canvas'),
      el('select', { id: 'sfx-select', style: { flex: '1' } },
        el('option', { value: '' }, '-- or select existing --'),
      ),
    ),
  );

  left.append(promptGroup, durationGroup, stepsGroup, cfgGroup, condSection, vpGroup, genRow, sfxRefCard, sfxSetupCard);

  // ═══════════════════════════════════════════════════════════════════════
  // Right column: generation results + SFX canvas
  // ═══════════════════════════════════════════════════════════════════════
  const right = el('div', { className: 'col-right' });

  const progressCard = el('div', { className: 'card hidden', id: 'gen-progress' },
    el('div', { className: 'progress-container' },
      el('div', { className: 'progress-bar' },
        el('div', { className: 'progress-fill', id: 'gen-progress-fill' }),
      ),
      el('div', { className: 'progress-label' },
        el('span', { id: 'gen-stage' }, ''),
        el('span', { id: 'gen-pct' }, '0%'),
      ),
    ),
  );

  const resultContainer = el('div', { id: 'gen-result' });

  // -- Reference player (always visible when a reference is selected) --
  const refPlayerSection = el('div', { id: 'sfx-ref-player-container' });

  // -- SFX canvas section --
  const sfxSection = el('div', { className: 'hidden', id: 'sfx-section' },
    el('div', { className: 'card', id: 'sfx-canvas-card' },
      // Header: title + actions
      el('div', { className: 'stem-card-header' },
        el('span', { className: 'stem-label', id: 'sfx-canvas-title' }, ''),
        el('div', { className: 'stem-actions' },
          el('label', { style: { display: 'flex', alignItems: 'center', gap: '6px', fontSize: '12px' } },
            el('span', { className: 'toggle' },
              el('input', { type: 'checkbox', id: 'sfx-limiter' }),
              el('span', { className: 'toggle-slider' }),
            ),
            'Limiter',
          ),
          el('button', { className: 'btn btn-sm', id: 'sfx-merge-canvas-btn' }, 'Merge In\u2026'),
          el('button', { className: 'btn btn-sm', id: 'sfx-save-btn' }, '\u2193 Save'),
          el('button', { className: 'btn btn-sm btn-primary', id: 'sfx-render-mix-btn' }, 'Render Canvas'),
          el('button', { className: 'btn btn-sm btn-danger', id: 'sfx-delete-btn' }, '\u2715'),
        ),
      ),
      // DAW-style timeline
      el('div', { className: 'sfx-timeline', id: 'sfx-timeline' },
        el('div', { className: 'sfx-timeline-ruler', id: 'sfx-timeline-ruler' }),
        el('div', { className: 'sfx-timeline-lanes', id: 'sfx-timeline-lanes' }),
        el('div', { className: 'sfx-timeline-playhead', id: 'sfx-timeline-playhead' }),
      ),
      // Active clip controls (shown when a clip is selected on the timeline)
      el('div', { className: 'sfx-clip-controls hidden', id: 'sfx-clip-controls' },
        el('span', { className: 'sfx-clip-controls-label', id: 'sfx-active-clip-name' }, ''),
        el('div', { className: 'sfx-clip-controls-fields' },
          el('label', {}, 'Fade in'),
          el('input', { type: 'number', id: 'sfx-clip-fade-in', min: '0', step: '50', value: '0' }),
          el('label', {}, 'Fade out'),
          el('input', { type: 'number', id: 'sfx-clip-fade-out', min: '0', step: '50', value: '0' }),
          el('span', { className: 'text-dim', style: { fontSize: '11px' } }, 'ms'),
        ),
      ),
      // Canvas player (populated after render)
      el('div', { id: 'sfx-canvas-player-container' }),
    ),
  );

  right.append(progressCard, resultContainer, refPlayerSection, sfxSection);
  layout.append(left, right);
  panel.appendChild(layout);

  // ═══════════════════════════════════════════════════════════════════════
  // Wire events
  // ═══════════════════════════════════════════════════════════════════════

  // Generation slider labels
  document.getElementById('gen-duration').addEventListener('input', (e) => {
    document.getElementById('gen-duration-val').textContent = `${e.target.value}s`;
  });
  document.getElementById('gen-steps').addEventListener('input', (e) => {
    document.getElementById('gen-steps-val').textContent = e.target.value;
  });
  document.getElementById('gen-cfg').addEventListener('input', (e) => {
    document.getElementById('gen-cfg-val').textContent = parseFloat(e.target.value).toFixed(1);
  });
  document.getElementById('gen-cond-source').addEventListener('change', (e) => {
    document.getElementById('gen-cond-audio-group').classList.toggle('hidden', e.target.value !== 'audio');
  });
  document.getElementById('gen-start').addEventListener('click', startGeneration);

  // SFX controls
  document.getElementById('sfx-duration').addEventListener('input', (e) => {
    document.getElementById('sfx-duration-val').textContent = `${e.target.value}s`;
  });
  document.getElementById('sfx-create-btn').addEventListener('click', createSfxCanvas);
  document.getElementById('sfx-select').addEventListener('change', (e) => {
    if (e.target.value) loadSfx(e.target.value);
  });
  document.getElementById('sfx-merge-canvas-btn').addEventListener('click', mergeCanvasPrompt);
  document.getElementById('sfx-save-btn').addEventListener('click', saveSfx);
  document.getElementById('sfx-render-mix-btn').addEventListener('click', renderCanvasToMix);
  document.getElementById('sfx-delete-btn').addEventListener('click', deleteSfx);
  document.getElementById('sfx-limiter').addEventListener('change', toggleLimiter);
  document.getElementById('sfx-align-select').addEventListener('change', onAlignSelectChange);

  // Align: load reference WAV directly
  document.getElementById('sfx-align-import-btn').addEventListener('click', () => {
    document.getElementById('sfx-align-import-input').click();
  });
  document.getElementById('sfx-align-import-input').addEventListener('change', async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const form = new FormData();
    form.append('file', file);
    try {
      const resp = await fetch('/api/sfx/upload-clip', { method: 'POST', body: form });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.detail || resp.statusText);
      }
      const result = await resp.json();
      _alignedRefPaths[result.name] = result.path;
      refreshAlignDropdown();
      document.getElementById('sfx-align-select').value = result.path;
      onAlignSelectChange();
    } catch (err) {
      alert(`Load reference failed: ${err.message}`);
    }
    e.target.value = '';
  });

  // Add sound from disk
  document.getElementById('sfx-add-sound-btn').addEventListener('click', () => {
    document.getElementById('sfx-add-sound-input').click();
  });
  document.getElementById('sfx-add-sound-input').addEventListener('change', async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const form = new FormData();
    form.append('file', file);
    try {
      const resp = await fetch('/api/sfx/upload-clip', { method: 'POST', body: form });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.detail || resp.statusText);
      }
      const result = await resp.json();
      // Auto-keep and add to canvas
      await api('/sfx/keep-clip', { method: 'POST', body: JSON.stringify({ path: result.path }) });
      await addClipToCanvas(result.path);
    } catch (err) {
      alert(`Add sound failed: ${err.message}`);
    }
    e.target.value = '';
  });

  // Fade controls commit on change
  document.getElementById('sfx-clip-fade-in').addEventListener('change', commitActiveFades);
  document.getElementById('sfx-clip-fade-out').addEventListener('change', commitActiveFades);

  // Deselect clip when clicking empty timeline space
  document.getElementById('sfx-timeline-lanes').addEventListener('click', (e) => {
    if (e.target.classList.contains('sfx-clip-block') || e.target.classList.contains('sfx-clip-label')
        || e.target.classList.contains('sfx-clip-x')) return;
    _selectClip(null);
  });

  // Populate conditioning sources + align dropdown when stems are ready
  appState.on('stemsReady', (stemPaths) => {
    _alignedStemPaths = stemPaths;
    const condSelect = document.getElementById('gen-cond-audio');
    clearChildren(condSelect);
    for (const label of Object.keys(stemPaths)) {
      condSelect.appendChild(el('option', { value: stemPaths[label] }, label));
    }
    refreshAlignDropdown();
  });

  // Populate MIDI stems in align dropdown
  appState.on('midiReady', (result) => {
    _alignedMidiLabels = result.labels || [];
    refreshAlignDropdown();
  });

  // Uploaded file → available as align reference
  appState.on('fileLoaded', (info) => {
    if (info && info.path && info.filename) {
      _alignedRefPaths[info.filename] = info.path;
      refreshAlignDropdown();
    }
  });

  // Load existing SFX canvases on init
  refreshSfxSelector();
}

// ═════════════════════════════════════════════════════════════════════════
// Generation (unchanged logic)
// ═════════════════════════════════════════════════════════════════════════

async function startGeneration() {
  const prompt = document.getElementById('gen-prompt').value.trim();
  if (!prompt) { alert('Prompt is required'); return; }

  const duration = parseFloat(document.getElementById('gen-duration').value);
  const steps = parseInt(document.getElementById('gen-steps').value);
  const cfgScale = parseFloat(document.getElementById('gen-cfg').value);
  const condSource = document.getElementById('gen-cond-source').value;
  const vp = document.getElementById('gen-vp').checked;

  let condPath = null;
  if (condSource === 'audio') {
    condPath = document.getElementById('gen-cond-audio').value;
  }

  const progressCard = document.getElementById('gen-progress');
  const resultContainer = document.getElementById('gen-result');
  progressCard.classList.remove('hidden');
  clearChildren(resultContainer);
  document.getElementById('gen-start').disabled = true;

  try {
    const { job_id } = await api('/generate', {
      method: 'POST',
      body: JSON.stringify({
        prompt,
        duration,
        steps,
        cfg_scale: cfgScale,
        conditioning_source: condSource,
        conditioning_path: condPath,
        vocal_preservation: vp,
      }),
    });

    pollJob(job_id, {
      interval: 10000,
      onProgress(progress, stage) {
        document.getElementById('gen-progress-fill').style.width = `${(progress * 100).toFixed(0)}%`;
        document.getElementById('gen-pct').textContent = `${(progress * 100).toFixed(0)}%`;
        document.getElementById('gen-stage').textContent = stage;
      },
      onDone(result) {
        progressCard.classList.add('hidden');
        document.getElementById('gen-start').disabled = false;
        showResult(result);
      },
      onError(msg) {
        progressCard.classList.add('hidden');
        document.getElementById('gen-start').disabled = false;
        resultContainer.appendChild(
          el('div', { className: 'banner banner-error' }, `Generation failed: ${msg}`),
        );
      },
    });
  } catch (err) {
    progressCard.classList.add('hidden');
    document.getElementById('gen-start').disabled = false;
    resultContainer.appendChild(
      el('div', { className: 'banner banner-error' }, `Error: ${err.message}`),
    );
  }
}

function showResult(result) {
  const container = document.getElementById('gen-result');

  // Mutable closure — updated if the clip is renamed
  let audioPath = result.audio_path;

  appState.musicgenPath = audioPath;
  appState.emit('generateReady', audioPath);

  const clipName = result.name || audioPath.split('/').pop()?.replace('.wav', '') || 'Clip';
  const nameSpan = makeEditableName(clipName, () => audioPath, (newPath) => {
    audioPath = newPath;
    appState.musicgenPath = newPath;
  });

  const keepBtn = el('button', { className: 'btn btn-sm' }, 'Keep');
  keepBtn.addEventListener('click', async () => {
    await api('/sfx/keep-clip', {
      method: 'POST',
      body: JSON.stringify({ path: audioPath }),
    });
    keepBtn.textContent = 'Kept';
    keepBtn.disabled = true;
  });

  const sfxBtn = el('button', {
    className: 'btn btn-sm btn-primary',
    onClick: () => addClipToCanvas(audioPath),
  }, '+ SFX Canvas');

  const url = `/api/audio/stream?path=${encodeURIComponent(audioPath)}`;
  const { card } = createStemPlayer(nameSpan, url, {
    getUrl: () => `/api/audio/stream?path=${encodeURIComponent(audioPath)}`,
    saveLabel: audioPath,
    extraButtons: [keepBtn, sfxBtn],
  });

  container.appendChild(card);
}

// ═════════════════════════════════════════════════════════════════════════
// SFX Canvas
// ═════════════════════════════════════════════════════════════════════════

async function createSfxCanvas() {
  const name = document.getElementById('sfx-name').value.trim() || 'Untitled SFX';
  const durationMs = parseInt(document.getElementById('sfx-duration').value) * 1000;

  try {
    const result = await api('/sfx/create', {
      method: 'POST',
      body: JSON.stringify({ name, mode: 'manual', duration_ms: durationMs }),
    });
    _currentSfxId = result.id;
    await refreshSfxSelector();
    await loadSfx(result.id);
    appState.emit('sfxReady', { id: _currentSfxId });
  } catch (err) {
    alert(`Failed to create SFX canvas: ${err.message}`);
  }
}

async function loadSfx(sfxId) {
  try {
    const data = await api(`/sfx/${sfxId}`);
    _currentSfxId = sfxId;

    // Mark selector
    document.getElementById('sfx-select').value = sfxId;

    showSfxCanvas(data);
  } catch (err) {
    alert(`Failed to load SFX: ${err.message}`);
  }
}

async function refreshSfxSelector() {
  try {
    const data = await api('/sfx');
    const select = document.getElementById('sfx-select');
    const current = select.value;
    clearChildren(select);
    select.appendChild(el('option', { value: '' }, '-- or select existing --'));
    for (const sfx of data.sfx_stems || []) {
      const opt = el('option', { value: sfx.id },
        `${sfx.name} (${formatTime(sfx.duration_ms / 1000)}, ${sfx.placement_count} clips)`,
      );
      select.appendChild(opt);
    }
    if (current) select.value = current;
  } catch { /* silent */ }
}


/** Quick-add a clip from a generated result card to the current canvas. */
async function addClipToCanvas(clipPath) {
  // Auto-keep the clip so it appears in the clip selector
  try { await api('/sfx/keep-clip', { method: 'POST', body: JSON.stringify({ path: clipPath }) }); } catch { /* ok */ }
  if (!_currentSfxId) {
    // Auto-create a canvas if none exists
    const name = document.getElementById('sfx-name').value.trim() || 'Untitled SFX';
    const durationMs = parseInt(document.getElementById('sfx-duration').value) * 1000;
    try {
      const result = await api('/sfx/create', {
        method: 'POST',
        body: JSON.stringify({ name, mode: 'manual', duration_ms: durationMs }),
      });
      _currentSfxId = result.id;
      await refreshSfxSelector();
    } catch (err) {
      alert(`Failed to create SFX canvas: ${err.message}`);
      return;
    }
  }

  try {
    // Place after the last clip's end so they don't all pile up at 0
    let startMs = 0;
    const data = await api(`/sfx/${_currentSfxId}`);
    const placements = data.manifest?.placements || [];
    if (placements.length > 0) {
      const maxEnd = Math.max(...placements.map(p => (p.start_ms || 0) + (p.clip_duration_ms || 0)));
      startMs = Math.min(maxEnd, (data.manifest?.duration_ms || Infinity) - 100);
      if (startMs < 0) startMs = 0;
    }

    await api(`/sfx/${_currentSfxId}/placements`, {
      method: 'POST',
      body: JSON.stringify({
        clip_path: clipPath,
        start_ms: startMs,
        volume: 1.0,
        fade_in_ms: 0,
        fade_out_ms: 0,
        fade_curve: 'linear',
      }),
    });
    await loadSfx(_currentSfxId);
    appState.emit('sfxReady', { id: _currentSfxId });
  } catch (err) {
    alert(`Failed to add clip: ${err.message}`);
  }
}


async function updatePlacement(placementId, updates) {
  if (!_currentSfxId) return;
  try {
    await api(`/sfx/${_currentSfxId}/placements/${placementId}`, {
      method: 'PUT',
      body: JSON.stringify(updates),
    });
    await loadSfx(_currentSfxId);
    appState.emit('sfxReady', { id: _currentSfxId });
  } catch (err) {
    alert(`Failed to update placement: ${err.message}`);
  }
}

async function mergeLanes(targetLane, sourceLane) {
  if (!_currentSfxId) return;
  try {
    await api(`/sfx/${_currentSfxId}/merge-lanes`, {
      method: 'POST',
      body: JSON.stringify({ target_lane: targetLane, source_lane: sourceLane }),
    });
    await loadSfx(_currentSfxId);
    appState.emit('sfxReady', { id: _currentSfxId });
  } catch (err) {
    alert(`Failed to merge lanes: ${err.message}`);
  }
}

async function removePlacement(placementId) {
  if (!_currentSfxId) return;
  try {
    await api(`/sfx/${_currentSfxId}/placements/${placementId}`, { method: 'DELETE' });
    await loadSfx(_currentSfxId);
    appState.emit('sfxReady', { id: _currentSfxId });
  } catch (err) {
    alert(`Failed to remove placement: ${err.message}`);
  }
}

async function saveSfx() {
  if (!_currentSfxId) return;
  try {
    const data = await api(`/sfx/${_currentSfxId}`);
    if (data.rendered_path) {
      const name = data.rendered_path.split('/').pop() || 'sfx.wav';
      saveFileAs(`/api/sfx/${_currentSfxId}/stream`, name);
    }
  } catch (err) {
    alert(`Save failed: ${err.message}`);
  }
}

async function mergeCanvasPrompt() {
  if (!_currentSfxId) { alert('No canvas loaded'); return; }
  try {
    const data = await api('/sfx');
    const others = (data.sfx_stems || []).filter(s => s.id !== _currentSfxId);
    if (others.length === 0) {
      alert('No other canvases to merge in.');
      return;
    }
    // Build a simple prompt listing available canvases
    const choices = others.map((s, i) => `${i + 1}. ${s.name} (${s.placement_count} clips)`).join('\n');
    const input = prompt(`Merge another canvas into this one.\nPick a number:\n\n${choices}`);
    if (!input) return;
    const idx = parseInt(input, 10) - 1;
    if (idx < 0 || idx >= others.length) { alert('Invalid selection'); return; }

    const sourceId = others[idx].id;
    await api(`/sfx/${_currentSfxId}/merge-canvas`, {
      method: 'POST',
      body: JSON.stringify({ source_id: sourceId }),
    });
    await refreshSfxSelector();
    await loadSfx(_currentSfxId);
    appState.emit('sfxReady', { id: _currentSfxId });
  } catch (err) {
    alert(`Merge failed: ${err.message}`);
  }
}

async function renderCanvasToMix() {
  if (!_currentSfxId) { alert('Create or select an SFX canvas first'); return; }
  const btn = document.getElementById('sfx-render-mix-btn');
  btn.disabled = true;
  btn.textContent = 'Rendering…';
  try {
    const result = await api(`/sfx/${_currentSfxId}/send-to-mix`, { method: 'POST' });
    appState.emit('sfxReady', { id: _currentSfxId });
    btn.textContent = `Sent: ${result.label}`;
    setTimeout(() => { btn.textContent = 'Render Canvas'; }, 2000);
  } catch (err) {
    alert(`Render to mix failed: ${err.message}`);
  } finally {
    btn.disabled = false;
  }
}

async function deleteSfx() {
  if (!_currentSfxId) return;
  if (!confirm('Delete this SFX canvas and all its placements?')) return;

  try {
    await api(`/sfx/${_currentSfxId}`, { method: 'DELETE' });
    _currentSfxId = null;
    _timelineDurationMs = 0;
    _activeClipId = null;
    document.getElementById('sfx-section').classList.add('hidden');
    if (_canvasPlayer) {
      _canvasPlayer.ws.destroy();
      const idx = _players.indexOf(_players.find(p => p.ws === _canvasPlayer.ws));
      if (idx >= 0) _players.splice(idx, 1);
      _canvasPlayer = null;
    }
    clearChildren(document.getElementById('sfx-canvas-player-container'));
    clearChildren(document.getElementById('sfx-timeline-ruler'));
    clearChildren(document.getElementById('sfx-timeline-lanes'));
    await refreshSfxSelector();
  } catch (err) {
    alert(`Delete failed: ${err.message}`);
  }
}

function refreshAlignDropdown() {
  const select = document.getElementById('sfx-align-select');
  if (!select) return;
  const current = select.value;
  clearChildren(select);
  select.appendChild(el('option', { value: '' }, '-- none --'));

  // Reference files (uploaded or imported directly)
  for (const [label, path] of Object.entries(_alignedRefPaths)) {
    const opt = el('option', { value: path }, label);
    opt.dataset.stemType = 'audio';
    select.appendChild(opt);
  }
  // Separated stems
  for (const [label, path] of Object.entries(_alignedStemPaths)) {
    const opt = el('option', { value: path }, label);
    opt.dataset.stemType = 'audio';
    select.appendChild(opt);
  }
  // MIDI stems
  for (const label of _alignedMidiLabels) {
    const opt = el('option', { value: `midi:${label}` }, `${label} [MIDI]`);
    opt.dataset.stemType = 'midi';
    select.appendChild(opt);
  }
  if (current && [...select.options].some(o => o.value === current)) {
    select.value = current;
  }
}

async function onAlignSelectChange() {
  const select = document.getElementById('sfx-align-select');
  const value = select.value;
  const stemType = select.selectedOptions[0]?.dataset.stemType || 'audio';

  _alignAudioPath = null;
  _alignStemType = null;

  // Remove reference player when deselected
  const refContainer = document.getElementById('sfx-ref-player-container');
  clearChildren(refContainer);
  _refPlayer = null;

  if (!value) {
    // Re-render timeline without reference lane
    if (_currentSfxId) {
      const data = await api(`/sfx/${_currentSfxId}`);
      renderTimeline(data.manifest);
    }
    return;
  }

  try {
    let audioPath;
    if (stemType === 'midi') {
      const midiLabel = value.slice(5); // strip 'midi:'
      const rendered = await api('/midi/render', {
        method: 'POST',
        body: JSON.stringify({ stem_label: midiLabel }),
      });
      audioPath = rendered.audio_path;
    } else {
      audioPath = value;
    }

    const info = await api(`/audio/info?path=${encodeURIComponent(audioPath)}`);
    const stemDurationMs = Math.round(info.duration * 1000);
    const stemSecs = Math.max(0, Math.min(120, Math.round(info.duration)));

    // Update canvas duration slider
    const slider = document.getElementById('sfx-duration');
    const label = document.getElementById('sfx-duration-val');
    if (slider) { slider.value = stemSecs; label.textContent = `${stemSecs}s`; }

    // Store for timeline rendering
    _alignAudioPath = audioPath;
    _alignStemType = stemType;

    // Create reference stem player
    const refLabel = select.selectedOptions[0]?.text || 'Reference';
    const refUrl = `/api/audio/stream?path=${encodeURIComponent(audioPath)}`;
    _refPlayer = createStemPlayer(`Reference: ${refLabel}`, refUrl, {
      getUrl: () => refUrl,
      saveLabel: audioPath,
    });
    const refContainer2 = document.getElementById('sfx-ref-player-container');
    clearChildren(refContainer2);
    refContainer2.appendChild(_refPlayer.card);

    // Resize the active canvas to match so clips and reference share the same timescale
    if (_currentSfxId) {
      await api(`/sfx/${_currentSfxId}`, {
        method: 'PATCH',
        body: JSON.stringify({ duration_ms: stemDurationMs }),
      });
      await loadSfx(_currentSfxId);
    }
  } catch (err) {
    console.error('Align to stem failed:', err);
  }
}

async function toggleLimiter() {
  if (!_currentSfxId) return;
  try {
    await api(`/sfx/${_currentSfxId}`, {
      method: 'PATCH',
      body: JSON.stringify({ apply_limiter: document.getElementById('sfx-limiter').checked }),
    });
    await loadSfx(_currentSfxId);
    appState.emit('sfxReady', { id: _currentSfxId });
  } catch (err) {
    alert(`Failed to update limiter: ${err.message}`);
  }
}

// ═════════════════════════════════════════════════════════════════════════
// SFX Canvas UI
// ═════════════════════════════════════════════════════════════════════════

function showSfxCanvas(data) {
  const { manifest, rendered_path } = data;

  document.getElementById('sfx-section').classList.remove('hidden');

  // Title
  document.getElementById('sfx-canvas-title').textContent =
    `${manifest.name} (${formatTime(manifest.duration_ms / 1000)})`;

  // Limiter toggle
  document.getElementById('sfx-limiter').checked = manifest.apply_limiter || false;

  // Render the DAW-style timeline
  renderTimeline(manifest);

  // Canvas player — visible stem-card below the timeline
  const playerContainer = document.getElementById('sfx-canvas-player-container');
  if (_canvasPlayer) {
    _canvasPlayer.ws.destroy();
    const idx = _players.indexOf(_players.find(p => p.ws === _canvasPlayer.ws));
    if (idx >= 0) _players.splice(idx, 1);
    _canvasPlayer = null;
  }
  clearChildren(playerContainer);

  if (rendered_path) {
    const url = `/api/sfx/${manifest.id}/stream`;
    _canvasPlayer = createStemPlayer('Canvas', url, {
      getUrl: () => `/api/sfx/${manifest.id}/stream`,
      saveLabel: rendered_path,
    });
    playerContainer.appendChild(_canvasPlayer.card);

    // Wire playhead to timeline
    _canvasPlayer.ws.on('timeupdate', (time) => {
      const dur = _canvasPlayer.ws.getDuration();
      if (dur > 0) {
        const playhead = document.getElementById('sfx-timeline-playhead');
        if (playhead) {
          playhead.style.display = '';
          playhead.style.left = `${(time / dur * 100).toFixed(2)}%`;
        }
      }
    });
  }

}

/**
 * Group placements by their explicit lane assignment.
 * Returns a Map<laneIndex, placement[]> sorted by lane number.
 */
function groupByLane(placements) {
  const map = new Map();
  for (const p of placements) {
    const lane = p.lane ?? 0;
    if (!map.has(lane)) map.set(lane, []);
    map.get(lane).push(p);
  }
  return new Map([...map.entries()].sort((a, b) => a[0] - b[0]));
}


/**
 * Render the DAW-style timeline: ruler ticks, reference lane, clip lanes.
 */
function renderTimeline(manifest) {
  const durationMs = manifest.duration_ms || 0;
  _timelineDurationMs = durationMs;

  const ruler = document.getElementById('sfx-timeline-ruler');
  const lanesContainer = document.getElementById('sfx-timeline-lanes');
  const playhead = document.getElementById('sfx-timeline-playhead');
  clearChildren(ruler);
  clearChildren(lanesContainer);
  if (playhead) playhead.style.display = 'none';

  if (durationMs === 0) return;

  const durSecs = durationMs / 1000;
  const tickInterval = durSecs <= 10 ? 1 : durSecs <= 60 ? 5 : 10;

  // Build ruler
  for (let t = 0; t <= durSecs; t += tickInterval) {
    const tick = el('div', { className: 'sfx-ruler-tick' });
    tick.style.left = `${(t / durSecs * 100).toFixed(2)}%`;
    tick.textContent = formatTime(t);
    ruler.appendChild(tick);
  }

  // Reference lane (label-only bar — playable waveform lives in the ref player above)
  if (_alignAudioPath) {
    const isMidi = _alignStemType === 'midi';
    const refLane = el('div', { className: 'sfx-lane sfx-lane-ref' });
    const refBlock = el('div', {
      className: `sfx-clip-block sfx-clip-ref${isMidi ? ' sfx-clip-ref-midi' : ''}`,
    });
    refBlock.style.left = '0';
    refBlock.style.width = '100%';

    const stemLabel = document.getElementById('sfx-align-select').selectedOptions[0]?.text || 'Reference';
    refBlock.appendChild(el('span', { className: 'sfx-clip-label' }, stemLabel));
    refLane.appendChild(refBlock);
    lanesContainer.appendChild(refLane);
  }

  // Clip lanes
  const placements = manifest.placements || [];
  if (placements.length === 0) {
    const emptyLane = el('div', { className: 'sfx-lane' });
    emptyLane.appendChild(el('span', { className: 'sfx-lane-hint text-dim' },
      'Use "+ SFX Canvas" on a result card, or "+ Add Sound" below to place clips'));
    lanesContainer.appendChild(emptyLane);
  } else {
    const laneMap = groupByLane(placements);
    const laneIndices = [...laneMap.keys()];

    for (const [laneIdx, clips] of laneMap) {
      const laneRow = el('div', { className: 'sfx-lane-row' });

      // Merge button (merge this lane into the one above)
      const laneActions = el('div', { className: 'sfx-lane-actions' });
      if (laneIndices.indexOf(laneIdx) > 0) {
        const targetLane = laneIndices[laneIndices.indexOf(laneIdx) - 1];
        const mergeBtn = el('button', {
          className: 'sfx-merge-btn',
          title: 'Merge into lane above',
        }, '\u2191');
        mergeBtn.addEventListener('click', () => mergeLanes(targetLane, laneIdx));
        laneActions.appendChild(mergeBtn);
      }
      laneRow.appendChild(laneActions);

      const lane = el('div', { className: 'sfx-lane' });
      lane.dataset.lane = laneIdx;

      for (const p of clips) {
        const clipDurMs = p.clip_duration_ms || 1000;
        const leftPct = (p.start_ms / durationMs * 100).toFixed(2);
        const widthPct = Math.max(0.5, clipDurMs / durationMs * 100).toFixed(2);
        const clipName = p.clip_name || (p.clip_path || '').split('/').pop() || 'clip';
        const isActive = _activeClipId === p.id;

        const xBtn = el('span', { className: 'sfx-clip-x', title: 'Remove' }, '\u00d7');
        xBtn.addEventListener('click', (e) => { e.stopPropagation(); removePlacement(p.id); });

        const block = el('div', {
          className: `sfx-clip-block${isActive ? ' sfx-clip-active' : ''}`,
        });
        block.style.left = `${leftPct}%`;
        block.style.width = `${widthPct}%`;
        block.title = `${clipName} @ ${(p.start_ms / 1000).toFixed(1)}s`;
        block.appendChild(el('span', { className: 'sfx-clip-label' }, clipName));
        block.appendChild(xBtn);
        block._pid = p.id;

        // Click to select
        block.addEventListener('click', (e) => { e.stopPropagation(); _selectClip(p); });

        // Drag to reposition
        block.draggable = true;
        block.addEventListener('dragstart', (e) => {
          e.dataTransfer.setData('text/plain', p.id);
          e.dataTransfer.effectAllowed = 'move';
          _dragOffsetPct = (e.clientX - block.getBoundingClientRect().left) / lanesContainer.getBoundingClientRect().width;
        });

        lane.appendChild(block);
      }
      laneRow.appendChild(lane);
      lanesContainer.appendChild(laneRow);
    }
  }

  // Drop target on lanes container
  lanesContainer.addEventListener('dragover', (e) => { e.preventDefault(); e.dataTransfer.dropEffect = 'move'; });
  lanesContainer.addEventListener('drop', (e) => {
    e.preventDefault();
    const pid = e.dataTransfer.getData('text/plain');
    if (!pid || !_timelineDurationMs) return;
    const rect = lanesContainer.getBoundingClientRect();
    const dropPct = (e.clientX - rect.left) / rect.width - (_dragOffsetPct || 0);
    const ms = Math.round(Math.max(0, dropPct * _timelineDurationMs) / 100) * 100;
    updatePlacement(pid, { start_ms: ms });
  });
}

/** Select a clip on the timeline — shows fade controls, highlights the block. */
function _selectClip(placement) {
  _activeClipId = placement ? placement.id : null;
  const controls = document.getElementById('sfx-clip-controls');

  // Update highlight on timeline blocks
  document.querySelectorAll('#sfx-timeline-lanes .sfx-clip-block').forEach(b => {
    b.classList.toggle('sfx-clip-active', b._pid === _activeClipId);
  });

  if (!placement) {
    controls.classList.add('hidden');
    return;
  }

  controls.classList.remove('hidden');
  const clipName = placement.clip_name || (placement.clip_path || '').split('/').pop() || 'clip';
  document.getElementById('sfx-active-clip-name').textContent = clipName;
  document.getElementById('sfx-clip-fade-in').value = placement.fade_in_ms || 0;
  document.getElementById('sfx-clip-fade-out').value = placement.fade_out_ms || 0;
}

/** Commit fade values for the active clip. */
async function commitActiveFades() {
  if (!_activeClipId || !_currentSfxId) return;
  const fadeIn = parseInt(document.getElementById('sfx-clip-fade-in').value) || 0;
  const fadeOut = parseInt(document.getElementById('sfx-clip-fade-out').value) || 0;
  await updatePlacement(_activeClipId, { fade_in_ms: fadeIn, fade_out_ms: fadeOut });
}
