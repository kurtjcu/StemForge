/**
 * Synth tab — Stable Audio Open generation + SFX Stem Builder.
 *
 * Generate audio clips, then place them on an SFX canvas with
 * per-clip volume/fade controls. A DAW-style multi-track timeline
 * shows the reference stem and clip placements on a shared time axis.
 */

import { appState, api, pollJob, el, formatTime, saveFileAs } from '../app.js';
import { createWaveform } from './waveform.js';
import { transportLoad } from './audio-player.js';

function clearChildren(elem) {
  while (elem.firstChild) elem.removeChild(elem.firstChild);
}

// ─── Module state ─────────────────────────────────────────────────────────

let _currentSfxId = null;
let _canvasWs = null;          // hidden wavesurfer for audio-only playback
let _alignAudioPath = null;    // resolved audio path for reference lane
let _alignStemType = null;     // 'audio' | 'midi'
let _timelineDurationMs = 0;   // current canvas duration in ms
let _alignedStemPaths = {};    // label → path, from stemsReady (audio, green)
let _alignedMidiLabels = [];   // labels, from midiReady (purple, rendered on demand)

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

  const genBtn = el('button', { className: 'btn btn-primary', id: 'gen-start' }, 'Generate');

  // -- SFX Canvas setup (below generation controls) --
  const sfxSetupCard = el('div', { className: 'card', style: { marginTop: '8px' } },
    el('div', { className: 'card-header' }, 'SFX STEM BUILDER'),
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

  left.append(promptGroup, durationGroup, stepsGroup, cfgGroup, condSection, vpGroup, genBtn, sfxSetupCard);

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

  // -- SFX canvas section --
  const sfxSection = el('div', { className: 'hidden', id: 'sfx-section' },
    // Timeline card: header controls + align dropdown + timeline canvas
    el('div', { className: 'card', id: 'sfx-canvas-card' },
      el('div', { className: 'stem-card-header' },
        el('span', { className: 'stem-label', id: 'sfx-canvas-title' }, ''),
        el('div', { className: 'stem-actions' },
          el('button', { className: 'btn btn-sm', id: 'sfx-play-btn' }, '\u25B6 Play'),
          el('button', { className: 'btn btn-sm', id: 'sfx-stop-btn' }, '\u23F9'),
          el('button', { className: 'btn btn-sm', id: 'sfx-rewind-btn' }, '\u23EE'),
          el('span', { className: 'time-label', id: 'sfx-time-label' }, ''),
          el('button', { className: 'btn btn-sm', id: 'sfx-save-btn' }, '\u2193 Save'),
          el('button', { className: 'btn btn-sm btn-primary', id: 'sfx-show-mix-btn' }, 'Show in Mix'),
        ),
      ),
      // Align dropdown inside timeline card
      el('div', { className: 'form-group', style: { margin: '8px 0 6px' } },
        el('label', { style: { fontSize: '11px', textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--text-dim)', marginBottom: '3px' } }, 'Align to'),
        el('select', { id: 'sfx-align-select', style: { width: '100%' } },
          el('option', { value: '' }, '-- none --'),
        ),
      ),
      // DAW-style timeline
      el('div', { className: 'sfx-timeline', id: 'sfx-timeline' },
        el('div', { className: 'sfx-timeline-ruler', id: 'sfx-timeline-ruler' }),
        el('div', { className: 'sfx-timeline-lanes', id: 'sfx-timeline-lanes' }),
        el('div', { className: 'sfx-timeline-playhead', id: 'sfx-timeline-playhead' }),
      ),
      // Hidden wavesurfer host (audio playback only — no visible rendering)
      el('div', { id: 'sfx-canvas-waveform', style: { height: '0', overflow: 'hidden' } }),
      el('div', { className: 'sfx-canvas-info', id: 'sfx-canvas-info' }),
    ),
    // Settings row
    el('div', { className: 'card', style: { marginTop: '8px' } },
      el('div', { className: 'stem-card-header' },
        el('span', { className: 'card-header', style: { marginBottom: '0' } }, 'SETTINGS'),
        el('div', { className: 'stem-actions' },
          el('label', { style: { display: 'flex', alignItems: 'center', gap: '8px', fontSize: '13px' } },
            el('span', { className: 'toggle' },
              el('input', { type: 'checkbox', id: 'sfx-limiter' }),
              el('span', { className: 'toggle-slider' }),
            ),
            'Soft limiter',
          ),
          el('button', { className: 'btn btn-sm btn-danger', id: 'sfx-delete-btn' }, 'Delete Canvas'),
        ),
      ),
    ),
    // Add clip controls
    el('div', { className: 'card', id: 'sfx-add-card', style: { marginTop: '8px' } },
      el('div', { className: 'card-header' }, 'ADD CLIP MANUALLY'),
      el('div', { className: 'form-group' },
        el('div', { style: { display: 'flex', justifyContent: 'space-between', alignItems: 'center' } },
          el('label', { style: { margin: '0' } }, 'Clip source'),
          el('button', { className: 'btn btn-sm', id: 'sfx-import-btn', style: { padding: '2px 8px', fontSize: '11px' } }, '+ Import'),
        ),
        el('input', { type: 'file', id: 'sfx-import-input', accept: '.wav,.flac,.mp3,.ogg', style: { display: 'none' } }),
        el('select', { id: 'sfx-clip-select' },
          el('option', { value: '' }, '-- loading clips --'),
        ),
      ),
      el('div', { style: { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px' } },
        el('div', { className: 'form-group' },
          el('label', {}, 'Start (ms)'),
          el('input', { type: 'number', id: 'sfx-clip-start', value: '0', min: '0', step: '100' }),
        ),
        el('div', { className: 'form-group' },
          el('label', {}, 'Volume'),
          el('div', { className: 'slider-row' },
            el('input', { type: 'range', id: 'sfx-clip-volume', min: '0', max: '200', value: '100', step: '5' }),
            el('span', { className: 'slider-value', id: 'sfx-clip-volume-val' }, '100%'),
          ),
        ),
      ),
      el('div', { style: { display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '8px' } },
        el('div', { className: 'form-group' },
          el('label', {}, 'Fade in (ms)'),
          el('input', { type: 'number', id: 'sfx-clip-fade-in', value: '0', min: '0', step: '50' }),
        ),
        el('div', { className: 'form-group' },
          el('label', {}, 'Fade out (ms)'),
          el('input', { type: 'number', id: 'sfx-clip-fade-out', value: '0', min: '0', step: '50' }),
        ),
        el('div', { className: 'form-group' },
          el('label', {}, 'Curve'),
          el('select', { id: 'sfx-clip-fade-curve' },
            el('option', { value: 'linear' }, 'Linear'),
            el('option', { value: 'cosine' }, 'Cosine'),
          ),
        ),
      ),
      el('button', { className: 'btn', id: 'sfx-add-clip-btn', style: { marginTop: '4px' } }, 'Add Clip'),
    ),
    // Placements list
    el('div', { className: 'card', style: { marginTop: '8px' } },
      el('div', { className: 'card-header' }, 'PLACEMENTS'),
      el('div', { id: 'sfx-placements-list' }),
    ),
  );

  right.append(progressCard, resultContainer, sfxSection);
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
  document.getElementById('sfx-clip-volume').addEventListener('input', (e) => {
    document.getElementById('sfx-clip-volume-val').textContent = `${e.target.value}%`;
  });
  document.getElementById('sfx-create-btn').addEventListener('click', createSfxCanvas);
  document.getElementById('sfx-select').addEventListener('change', (e) => {
    if (e.target.value) loadSfx(e.target.value);
  });
  document.getElementById('sfx-add-clip-btn').addEventListener('click', addClipManually);

  // Import clip: button triggers hidden file input, file input uploads
  document.getElementById('sfx-import-btn').addEventListener('click', () => {
    document.getElementById('sfx-import-input').click();
  });
  document.getElementById('sfx-import-input').addEventListener('change', async (e) => {
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
      await refreshClipList();
      document.getElementById('sfx-clip-select').value = result.path;
    } catch (err) {
      alert(`Import failed: ${err.message}`);
    }
    e.target.value = '';  // reset so same file can be re-imported
  });
  document.getElementById('sfx-save-btn').addEventListener('click', saveSfx);
  document.getElementById('sfx-show-mix-btn').addEventListener('click', () => {
    document.querySelector('.tab-btn[data-tab="mix"]').click();
  });
  document.getElementById('sfx-delete-btn').addEventListener('click', deleteSfx);
  document.getElementById('sfx-limiter').addEventListener('change', toggleLimiter);
  document.getElementById('sfx-align-select').addEventListener('change', onAlignSelectChange);

  // SFX local playback (no global transport)
  document.getElementById('sfx-play-btn').addEventListener('click', () => {
    if (!_canvasWs) return;
    if (_canvasWs.isPlaying()) {
      _canvasWs.pause();
    } else {
      _canvasWs.play();
    }
  });
  document.getElementById('sfx-stop-btn').addEventListener('click', () => {
    if (_canvasWs) _canvasWs.stop();
  });
  document.getElementById('sfx-rewind-btn').addEventListener('click', () => {
    if (_canvasWs) _canvasWs.setTime(0);
  });

  // Click on empty timeline space → set start_ms
  document.getElementById('sfx-timeline-lanes').addEventListener('click', (e) => {
    if (!_timelineDurationMs) return;
    const t = e.target;
    if (t.classList.contains('sfx-clip-block') || t.classList.contains('sfx-clip-label')) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const ms = Math.round((e.clientX - rect.left) / rect.width * _timelineDurationMs / 100) * 100;
    document.getElementById('sfx-clip-start').value = Math.max(0, ms);
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
    refreshClipList();
  });

  // Populate MIDI stems in align dropdown
  appState.on('midiReady', (result) => {
    _alignedMidiLabels = result.labels || [];
    refreshAlignDropdown();
  });

  appState.on('generateReady', () => refreshClipList());

  // Load existing SFX canvases on init
  refreshSfxSelector();
  refreshClipList();
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
      interval: 3000,
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

  appState.musicgenPath = result.audio_path;
  appState.emit('generateReady', result.audio_path);

  const card = el('div', { className: 'stem-card' },
    el('div', { className: 'stem-card-header' },
      el('span', { className: 'stem-label' }, `Generated (${formatTime(result.duration)})`),
      el('div', { className: 'stem-actions' },
        el('button', {
          className: 'btn btn-sm',
          onClick: () => transportLoad(
            `/api/audio/stream?path=${encodeURIComponent(result.audio_path)}`,
            'Generated audio',
          ),
        }, '\u25B6 Play'),
        el('button', {
          className: 'btn btn-sm',
          onClick: () => {
            const name = result.audio_path.split('/').pop() || 'generated.wav';
            saveFileAs(`/api/audio/download?path=${encodeURIComponent(result.audio_path)}`, name);
          },
        }, '\u2193 Save'),
        el('button', {
          className: 'btn btn-sm btn-primary',
          onClick: () => addClipToCanvas(result.audio_path),
        }, '+ SFX Canvas'),
      ),
    ),
  );

  const waveContainer = el('div', { className: 'stem-waveform' });
  card.appendChild(waveContainer);
  container.appendChild(card);

  const ws = createWaveform(waveContainer, { height: 50 });
  ws.load(`/api/audio/stream?path=${encodeURIComponent(result.audio_path)}`);

  // Refresh clip list so the new clip is available
  refreshClipList();
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
    refreshClipList();
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

async function refreshClipList() {
  try {
    let url = '/sfx/available-clips';
    if (_currentSfxId) url += `?exclude_id=${_currentSfxId}`;
    const data = await api(url);
    const select = document.getElementById('sfx-clip-select');
    clearChildren(select);
    select.appendChild(el('option', { value: '' }, '-- select clip --'));

    const groups = { session: [], saved_sfx: [], imported: [] };
    for (const clip of data.clips || []) {
      const g = clip.group || 'session';
      if (groups[g]) groups[g].push(clip);
    }

    const groupLabels = { session: 'This Session', saved_sfx: 'Saved SFX', imported: 'Imported' };
    for (const [key, label] of Object.entries(groupLabels)) {
      const items = groups[key];
      if (!items.length) continue;
      const optgroup = document.createElement('optgroup');
      optgroup.label = label;
      for (const clip of items) {
        let text = clip.name;
        if (key === 'saved_sfx') {
          const secs = ((clip.duration_ms || 0) / 1000).toFixed(1);
          const n = clip.clip_count ?? 0;
          text = `${clip.name} (${secs}s, ${n} clip${n !== 1 ? 's' : ''})`;
        }
        optgroup.appendChild(el('option', { value: clip.path }, text));
      }
      select.appendChild(optgroup);
    }
  } catch { /* silent */ }
}

/** Quick-add a clip from a generated result card to the current canvas. */
async function addClipToCanvas(clipPath) {
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
    await api(`/sfx/${_currentSfxId}/placements`, {
      method: 'POST',
      body: JSON.stringify({
        clip_path: clipPath,
        start_ms: 0,
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

async function addClipManually() {
  if (!_currentSfxId) { alert('Create or select an SFX canvas first'); return; }

  const clipPath = document.getElementById('sfx-clip-select').value;
  if (!clipPath) { alert('Select a clip first'); return; }

  const startMs = parseInt(document.getElementById('sfx-clip-start').value) || 0;
  const volume = parseInt(document.getElementById('sfx-clip-volume').value) / 100;
  const fadeIn = parseInt(document.getElementById('sfx-clip-fade-in').value) || 0;
  const fadeOut = parseInt(document.getElementById('sfx-clip-fade-out').value) || 0;
  const fadeCurve = document.getElementById('sfx-clip-fade-curve').value;

  try {
    await api(`/sfx/${_currentSfxId}/placements`, {
      method: 'POST',
      body: JSON.stringify({
        clip_path: clipPath,
        start_ms: startMs,
        volume,
        fade_in_ms: fadeIn,
        fade_out_ms: fadeOut,
        fade_curve: fadeCurve,
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

async function deleteSfx() {
  if (!_currentSfxId) return;
  if (!confirm('Delete this SFX canvas and all its placements?')) return;

  try {
    await api(`/sfx/${_currentSfxId}`, { method: 'DELETE' });
    _currentSfxId = null;
    _alignAudioPath = null;
    _alignStemType = null;
    _timelineDurationMs = 0;
    document.getElementById('sfx-section').classList.add('hidden');
    if (_canvasWs) { _canvasWs.destroy(); _canvasWs = null; }
    document.getElementById('sfx-align-select').value = '';
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
  for (const [label, path] of Object.entries(_alignedStemPaths)) {
    const opt = el('option', { value: path }, label);
    opt.dataset.stemType = 'audio';
    select.appendChild(opt);
  }
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

  // Hidden wavesurfer for audio-only playback
  const waveContainer = document.getElementById('sfx-canvas-waveform');
  if (_canvasWs) { _canvasWs.destroy(); _canvasWs = null; }

  if (rendered_path) {
    _canvasWs = createWaveform(waveContainer, { height: 80, color: 'sfx' });
    _canvasWs.load(`/api/sfx/${manifest.id}/stream`);

    const playBtn = document.getElementById('sfx-play-btn');
    const timeLabel = document.getElementById('sfx-time-label');
    if (playBtn) playBtn.textContent = '\u25B6 Play';

    _canvasWs.on('play',   () => { if (playBtn) playBtn.textContent = '\u23F8 Pause'; });
    _canvasWs.on('pause',  () => { if (playBtn) playBtn.textContent = '\u25B6 Play'; });
    _canvasWs.on('finish', () => { if (playBtn) playBtn.textContent = '\u25B6 Play'; });
    _canvasWs.on('timeupdate', (time) => {
      const dur = _canvasWs.getDuration();
      if (timeLabel) timeLabel.textContent = `${formatTime(time)} / ${formatTime(dur)}`;
      // Move playhead
      if (dur > 0) {
        const playhead = document.getElementById('sfx-timeline-playhead');
        if (playhead) {
          playhead.style.display = '';
          playhead.style.left = `${(time / dur * 100).toFixed(2)}%`;
        }
      }
    });
  }

  // Placements list
  renderPlacements(manifest.placements || []);
}

/**
 * Pack placements into non-overlapping rows (greedy by start_ms).
 * Returns an array of rows, each row being an array of placements.
 */
function packPlacements(placements) {
  const sorted = [...placements].sort((a, b) => a.start_ms - b.start_ms);
  const rows = [];
  const rowEnds = [];

  for (const p of sorted) {
    const startMs = p.start_ms;
    const durMs = p.clip_duration_ms || 1000;
    const endMs = startMs + durMs;

    let placed = false;
    for (let i = 0; i < rows.length; i++) {
      if (startMs >= rowEnds[i]) {
        rows[i].push(p);
        rowEnds[i] = endMs;
        placed = true;
        break;
      }
    }
    if (!placed) {
      rows.push([p]);
      rowEnds.push(endMs);
    }
  }
  return rows;
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

  // Reference lane (if align is set)
  if (_alignAudioPath) {
    const refLane = el('div', { className: 'sfx-lane sfx-lane-ref' });
    const refBlock = el('div', {
      className: `sfx-clip-block sfx-clip-ref${_alignStemType === 'midi' ? ' sfx-clip-ref-midi' : ''}`,
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
      'Click to set start position, then add a clip below'));
    lanesContainer.appendChild(emptyLane);
  } else {
    const rows = packPlacements(placements);
    for (const row of rows) {
      const lane = el('div', { className: 'sfx-lane' });
      for (const p of row) {
        const clipDurMs = p.clip_duration_ms || 1000;
        const leftPct = (p.start_ms / durationMs * 100).toFixed(2);
        const widthPct = Math.max(0.5, clipDurMs / durationMs * 100).toFixed(2);
        const clipName = p.clip_name || (p.clip_path || '').split('/').pop() || 'clip';

        const block = el('div', { className: 'sfx-clip-block' });
        block.style.left = `${leftPct}%`;
        block.style.width = `${widthPct}%`;
        block.title = `${clipName} @ ${p.start_ms}ms`;
        block.appendChild(el('span', { className: 'sfx-clip-label' }, clipName));

        // Click to open edit mode for this placement
        block.addEventListener('click', (e) => {
          e.stopPropagation();
          const row = document.querySelector(`#sfx-placements-list [data-pid="${p.id}"]`);
          const editBtn = row?.querySelector('.btn:not(.btn-danger)');
          if (editBtn) editBtn.click();
        });

        lane.appendChild(block);
      }
      lanesContainer.appendChild(lane);
    }
  }
}

function renderPlacements(placements) {
  const container = document.getElementById('sfx-placements-list');
  clearChildren(container);

  if (placements.length === 0) {
    container.appendChild(el('span', { className: 'text-dim' },
      'No clips placed yet. Generate audio above and click "+ SFX Canvas" to add.'));
    return;
  }

  for (const p of placements) {
    const clipName = p.clip_name || (p.clip_path || '').split('/').pop();
    const row = el('div', { className: 'sfx-placement-row' },
      el('div', { className: 'sfx-placement-info' },
        el('span', { className: 'sfx-placement-name' }, clipName),
        el('span', { className: 'text-dim', style: { fontSize: '11px' } },
          `@ ${p.start_ms}ms | vol ${Math.round(p.volume * 100)}%` +
          (p.fade_in_ms ? ` | fi ${p.fade_in_ms}ms` : '') +
          (p.fade_out_ms ? ` | fo ${p.fade_out_ms}ms` : ''),
        ),
      ),
      el('div', { className: 'sfx-placement-actions' },
        buildEditButton(p),
        el('button', {
          className: 'btn btn-sm btn-danger',
          onClick: () => removePlacement(p.id),
        }, 'Remove'),
      ),
    );
    row.dataset.pid = p.id;
    container.appendChild(row);
  }
}

function buildEditButton(placement) {
  const btn = el('button', { className: 'btn btn-sm' }, 'Edit');
  btn.addEventListener('click', () => {
    // Populate the manual add form with current values
    document.getElementById('sfx-clip-start').value = placement.start_ms;
    document.getElementById('sfx-clip-volume').value = Math.round(placement.volume * 100);
    document.getElementById('sfx-clip-volume-val').textContent = `${Math.round(placement.volume * 100)}%`;
    document.getElementById('sfx-clip-fade-in').value = placement.fade_in_ms;
    document.getElementById('sfx-clip-fade-out').value = placement.fade_out_ms;
    document.getElementById('sfx-clip-fade-curve').value = placement.fade_curve || 'linear';

    // Swap Add button with Update + Cancel
    const addBtn = document.getElementById('sfx-add-clip-btn');
    const wrapper = el('div', { style: { display: 'flex', gap: '8px', marginTop: '4px' } },
      el('button', { className: 'btn btn-primary', onClick: doUpdate }, 'Update Clip'),
      el('button', { className: 'btn', onClick: cancelEdit }, 'Cancel'),
    );
    addBtn.classList.add('hidden');
    addBtn.parentNode.insertBefore(wrapper, addBtn.nextSibling);

    async function doUpdate() {
      wrapper.remove();
      addBtn.classList.remove('hidden');
      await updatePlacement(placement.id, {
        start_ms: parseInt(document.getElementById('sfx-clip-start').value) || 0,
        volume: parseInt(document.getElementById('sfx-clip-volume').value) / 100,
        fade_in_ms: parseInt(document.getElementById('sfx-clip-fade-in').value) || 0,
        fade_out_ms: parseInt(document.getElementById('sfx-clip-fade-out').value) || 0,
        fade_curve: document.getElementById('sfx-clip-fade-curve').value,
      });
    }

    function cancelEdit() {
      wrapper.remove();
      addBtn.classList.remove('hidden');
    }
  });
  return btn;
}
