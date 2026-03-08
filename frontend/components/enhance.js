/**
 * Enhance tab — vocal cleanup and audio enhancement.
 *
 * Phase 1: UVR denoise / dereverb via audio-separator presets.
 * Shows original stem + processed result with diff waveform overlay.
 * Supports batch mode: process multiple files with one preset.
 */

import { appState, api, pollJob, el, formatTime, saveFileAs } from '../app.js';
import { createWaveform } from './waveform.js';
import { decodeAudioPeaks, renderDiffWaveform } from './waveform-diff.js';
import { transportLoad } from './audio-player.js';

function clearChildren(elem) {
  while (elem.firstChild) elem.removeChild(elem.firstChild);
}

// ─── Mode state ─────────────────────────────────────────────────────

let _batchMode = false;
let _batchFiles = [];    // [{filename, path, duration?, ...}]
let _autotuneMode = false;  // false = denoise/dereverb, true = auto-tune

// ─── Inline audio players (exclusive playback) ───────────────────────

const _players = [];

function _stopOtherPlayers(except) {
  for (const p of _players) {
    if (p.ws !== except && p.ws.isPlaying()) {
      p.ws.stop();
      p.playBtn.textContent = '\u25B6 Play';
    }
  }
}

function _createPlayer(label, url, audioPath) {
  const playBtn = el('button', { className: 'btn btn-sm' }, '\u25B6 Play');
  const stopBtn = el('button', { className: 'btn btn-sm' }, '\u25A0 Stop');
  const rewindBtn = el('button', { className: 'btn btn-sm' }, '\u23EA Rewind');
  const timeLabel = el('span', { className: 'stem-time' }, '0:00 / 0:00');
  const saveBtn = el('button', {
    className: 'btn btn-sm',
    onClick: () => {
      const name = (audioPath || '').split('/').pop() || 'audio.wav';
      saveFileAs(`/api/audio/download?path=${encodeURIComponent(audioPath || '')}`, name);
    },
  }, '\u2193 Save');

  const card = el('div', { className: 'stem-card' },
    el('div', { className: 'stem-card-header' },
      el('span', { className: 'stem-label' }, label),
      el('div', { className: 'stem-actions' },
        playBtn, stopBtn, rewindBtn, timeLabel, saveBtn,
      ),
    ),
  );

  const waveContainer = el('div', { className: 'stem-waveform' });
  card.appendChild(waveContainer);

  const ws = createWaveform(waveContainer, { height: 50 });
  ws.load(url);

  const player = { ws, playBtn };
  _players.push(player);

  playBtn.addEventListener('click', () => {
    if (ws.isPlaying()) {
      ws.pause();
      playBtn.textContent = '\u25B6 Play';
    } else {
      _stopOtherPlayers(ws);
      ws.play();
      playBtn.textContent = '\u23F8 Pause';
      const source = _autotuneMode ? 'Enhance \u203A Tune' : 'Enhance \u203A Clean Up';
      transportLoad(url, label, false, source);
    }
  });

  stopBtn.addEventListener('click', () => {
    ws.stop();
    playBtn.textContent = '\u25B6 Play';
  });

  rewindBtn.addEventListener('click', () => ws.setTime(0));

  ws.on('timeupdate', (time) => {
    const dur = ws.getDuration();
    timeLabel.textContent = `${formatTime(time)} / ${formatTime(dur)}`;
  });
  ws.on('finish', () => { playBtn.textContent = '\u25B6 Play'; });

  return { card, ws };
}

// ─── Init ─────────────────────────────────────────────────────────────

export function initEnhance() {
  const panel = document.getElementById('panel-enhance');

  // ─── Mode bar (Clean Up | Tune | Effects) ───
  const modeBar = el('div', { className: 'enhance-mode-bar' },
    el('div', { className: 'enhance-mode-selector' },
      el('button', { className: 'enhance-mode-btn active', 'data-mode': 'cleanup', onClick: () => switchEnhanceMode('cleanup') }, 'Clean Up'),
      el('button', { className: 'enhance-mode-btn', 'data-mode': 'tune', onClick: () => switchEnhanceMode('tune') }, 'Tune'),
      el('button', { className: 'enhance-mode-btn', 'data-mode': 'effects', onClick: () => switchEnhanceMode('effects') }, 'Effects'),
    ),
  );

  // Batch toggle — only shown in Clean Up mode
  const batchCheckbox = el('input', { type: 'checkbox', id: 'enhance-batch-toggle' });
  const batchToggle = el('div', { className: 'batch-toggle', id: 'enhance-batch-toggle-row' },
    el('label', {}, batchCheckbox, ' Batch mode'),
  );

  // ─── Single mode: stem selector + file browse (shared across modes) ───
  const stemLabel = el('label', { className: 'field-label' }, 'Source Audio');
  const stemSelect = el('select', { id: 'enhance-stem', className: 'select' });
  const singleFileInput = el('input', { type: 'file', accept: 'audio/*,.wav,.flac,.mp3,.ogg,.aiff', style: { display: 'none' }, id: 'enhance-single-input' });
  const stemGroup = el('div', { className: 'field-group', id: 'enhance-stem-group', style: { marginBottom: '12px' } },
    stemLabel, stemSelect, singleFileInput,
  );

  // ─── Batch mode: file upload zone (matches Separate tab drop-zone) ───
  const batchFileInput = el('input', { type: 'file', multiple: true, accept: 'audio/*,.wav,.flac,.mp3,.ogg,.aiff', style: { display: 'none' }, id: 'enhance-batch-input' });
  const batchDropZone = el('div', {
    className: 'drop-zone',
    id: 'enhance-batch-drop',
    style: { display: 'none' },
  },
    el('span', { className: 'drop-icon' }, '\u{1F3B5}'),
    el('div', { className: 'drop-text' }, 'Drop audio files here or click to browse'),
    el('div', { className: 'drop-hint' }, 'WAV, FLAC, MP3, OGG, AIFF'),
  );
  const batchFileList = el('div', { className: 'batch-file-list', id: 'enhance-batch-files', style: { display: 'none', marginTop: '8px', marginBottom: '12px' } });

  // ─── Clean Up mode: preset selector ───
  const presetLabel = el('label', { className: 'field-label' }, 'Enhancement');
  const presetSelect = el('select', { id: 'enhance-preset', className: 'select' });
  const presetDesc = el('div', {
    id: 'enhance-preset-desc',
    className: 'text-dim',
    style: { fontSize: '12px', marginTop: '4px' },
  });
  const presetGroup = el('div', { className: 'field-group', id: 'enhance-preset-group', style: { marginBottom: '12px' } },
    presetLabel, presetSelect, presetDesc,
  );

  // ─── Tune mode: auto-tune controls (hidden by default) ───
  const atKeySelect = el('select', { id: 'enhance-at-key', className: 'select' });
  const atScaleSelect = el('select', { id: 'enhance-at-scale', className: 'select' });

  const atStrengthSlider = el('input', {
    type: 'range', id: 'enhance-at-strength', min: '0', max: '100', value: '80',
    style: { flex: '1' },
  });
  const atStrengthLabel = el('span', { className: 'text-dim', style: { minWidth: '36px', textAlign: 'right' } }, '80%');

  const atHumanizeSlider = el('input', {
    type: 'range', id: 'enhance-at-humanize', min: '0', max: '100', value: '15',
    style: { flex: '1' },
  });
  const atHumanizeLabel = el('span', { className: 'text-dim', style: { minWidth: '36px', textAlign: 'right' } }, '15%');

  const autotuneGroup = el('div', { id: 'enhance-autotune-group', style: { display: 'none', marginBottom: '12px' } },
    el('div', { style: { display: 'flex', gap: '12px', marginBottom: '8px' } },
      el('div', { className: 'field-group', style: { flex: '1' } },
        el('label', { className: 'field-label' }, 'Key'), atKeySelect),
      el('div', { className: 'field-group', style: { flex: '1' } },
        el('label', { className: 'field-label' }, 'Scale'), atScaleSelect),
    ),
    el('div', { style: { display: 'flex', gap: '12px' } },
      el('div', { className: 'field-group', style: { flex: '1' } },
        el('label', { className: 'field-label' }, 'Correction Strength'),
        el('div', { style: { display: 'flex', alignItems: 'center', gap: '8px' } },
          atStrengthSlider, atStrengthLabel),
      ),
      el('div', { className: 'field-group', style: { flex: '1' } },
        el('label', { className: 'field-label' }, 'Humanize'),
        el('div', { style: { display: 'flex', alignItems: 'center', gap: '8px' } },
          atHumanizeSlider, atHumanizeLabel),
      ),
    ),
  );

  // ─── Effects mode: stub (hidden by default) ───
  const effectsGroup = el('div', { id: 'enhance-effects-group', style: { display: 'none' } },
    el('div', { className: 'banner banner-info' }, 'Effects chain (EQ, compression, limiting, chorus, delay) \u2014 coming soon.'),
  );

  // Process button
  const processBtn = el('button', {
    className: 'btn btn-primary',
    id: 'enhance-process',
    disabled: 'true',
  }, 'Process');

  // Progress
  const progressCard = el('div', { className: 'card hidden', id: 'enhance-progress' },
    el('div', { className: 'progress-container' },
      el('div', { className: 'progress-bar' },
        el('div', { className: 'progress-fill', id: 'enhance-progress-fill' }),
      ),
      el('div', { className: 'progress-label' },
        el('span', { id: 'enhance-stage' }, ''),
        el('span', { id: 'enhance-pct' }, '0%'),
      ),
    ),
  );

  // Original stem player (shown when stem is selected, single mode only)
  const originalSection = el('div', { id: 'enhance-original', style: { display: 'none', marginTop: '16px' } },
    el('div', { className: 'section-title', style: { fontSize: '13px', marginBottom: '6px' } }, 'Original'),
  );

  // Results container
  const resultsSection = el('div', { id: 'enhance-results', style: { marginTop: '16px' } });

  const controlRow = el('div', {
    id: 'enhance-controls',
    style: { display: 'flex', gap: '12px', alignItems: 'flex-start' },
  },
    el('div', { style: { flex: '1' } }, stemGroup),
    el('div', { style: { flex: '1' }, id: 'enhance-right-col' }, presetGroup),
    el('div', { style: { paddingTop: '22px' } }, processBtn),
  );

  // Batch drop zone sits below the control row (hidden by default)
  const batchSection = el('div', { id: 'enhance-batch-section', style: { display: 'none' } },
    batchDropZone, batchFileInput, batchFileList,
  );

  panel.append(modeBar, batchToggle, controlRow, autotuneGroup, effectsGroup, batchSection, progressCard, originalSection, resultsSection);

  // ─── Load presets + initial stems + autotune options ───
  loadPresets();
  refreshStems();
  loadAutotuneOptions();

  // ─── Wire events ───
  processBtn.addEventListener('click', () => {
    if (_batchMode) startBatchEnhance();
    else if (_autotuneMode) startAutotune();
    else startEnhance();
  });

  // Auto-tune slider labels
  atStrengthSlider.addEventListener('input', () => {
    atStrengthLabel.textContent = `${atStrengthSlider.value}%`;
  });
  atHumanizeSlider.addEventListener('input', () => {
    atHumanizeLabel.textContent = `${atHumanizeSlider.value}%`;
  });

  stemSelect.addEventListener('change', () => {
    if (stemSelect.value === '__browse__') {
      singleFileInput.click();
      stemSelect.value = '';  // reset so dropdown doesn't stick on "Browse..."
      return;
    }
    updateOriginalPreview();
    processBtn.disabled = !stemSelect.value;
  });

  singleFileInput.addEventListener('change', async () => {
    if (!singleFileInput.files.length) return;
    const file = singleFileInput.files[0];
    singleFileInput.value = '';
    const formData = new FormData();
    formData.append('file', file);
    try {
      const data = await (await fetch('/api/upload', { method: 'POST', body: formData })).json();
      if (data.path) {
        await refreshStems();
        stemSelect.value = data.path;
        stemSelect.dispatchEvent(new Event('change'));
      }
    } catch { /* ignore */ }
  });

  presetSelect.addEventListener('change', updatePresetDescription);

  // Batch toggle
  batchCheckbox.addEventListener('change', () => {
    _batchMode = batchCheckbox.checked;
    _batchFiles = [];
    toggleBatchMode();
  });

  // Batch drop zone
  batchDropZone.addEventListener('click', () => batchFileInput.click());
  batchDropZone.addEventListener('dragover', (e) => { e.preventDefault(); batchDropZone.classList.add('dragover'); });
  batchDropZone.addEventListener('dragleave', () => batchDropZone.classList.remove('dragover'));
  batchDropZone.addEventListener('drop', (e) => {
    e.preventDefault();
    batchDropZone.classList.remove('dragover');
    handleBatchFiles(e.dataTransfer.files);
  });
  batchFileInput.addEventListener('change', () => {
    if (batchFileInput.files.length) handleBatchFiles(batchFileInput.files);
    batchFileInput.value = '';  // reset so re-selecting same files triggers change
  });

  // Listen for stems and files becoming available
  appState.on('stemsReady', () => refreshStems());
  appState.on('fileLoaded', () => refreshStems());
  appState.on('enhanceReady', () => refreshStems());
  appState.on('generateReady', () => refreshStems());
  appState.on('composeReady', () => refreshStems());
}

// ─── Mode switching (Clean Up | Tune | Effects) ─────────────────────

let _currentEnhanceMode = 'cleanup';

function switchEnhanceMode(mode) {
  _currentEnhanceMode = mode;
  _autotuneMode = mode === 'tune';

  // Update mode bar buttons
  document.querySelectorAll('#panel-enhance .enhance-mode-btn').forEach(b =>
    b.classList.toggle('active', b.dataset.mode === mode),
  );

  const presetGroupEl = document.getElementById('enhance-preset-group');
  const autotuneGroupEl = document.getElementById('enhance-autotune-group');
  const effectsGroupEl = document.getElementById('enhance-effects-group');
  const batchRow = document.getElementById('enhance-batch-toggle-row');
  const processBtn = document.getElementById('enhance-process');
  const rightCol = document.getElementById('enhance-right-col');
  const controlRow = document.getElementById('enhance-controls');

  // Hide all mode-specific panels
  presetGroupEl.style.display = 'none';
  autotuneGroupEl.style.display = 'none';
  effectsGroupEl.style.display = 'none';

  if (mode === 'cleanup') {
    presetGroupEl.style.display = '';
    rightCol.style.display = '';
    controlRow.style.display = '';
    batchRow.style.display = '';
    processBtn.style.display = '';
  } else if (mode === 'tune') {
    rightCol.style.display = 'none';
    controlRow.style.display = '';
    autotuneGroupEl.style.display = '';
    processBtn.style.display = '';
    // Disable batch mode for auto-tune
    batchRow.style.display = 'none';
    if (_batchMode) {
      document.getElementById('enhance-batch-toggle').checked = false;
      _batchMode = false;
      toggleBatchMode();
    }
  } else if (mode === 'effects') {
    rightCol.style.display = 'none';
    controlRow.style.display = '';
    effectsGroupEl.style.display = '';
    batchRow.style.display = 'none';
    processBtn.style.display = 'none';
    if (_batchMode) {
      document.getElementById('enhance-batch-toggle').checked = false;
      _batchMode = false;
      toggleBatchMode();
    }
  }
}

// ─── Batch mode toggle ──────────────────────────────────────────────

function toggleBatchMode() {
  const stemGroup = document.getElementById('enhance-stem-group');
  const batchSection = document.getElementById('enhance-batch-section');
  const batchDrop = document.getElementById('enhance-batch-drop');
  const batchFiles = document.getElementById('enhance-batch-files');
  const originalSection = document.getElementById('enhance-original');
  const processBtn = document.getElementById('enhance-process');
  const resultsSection = document.getElementById('enhance-results');

  clearChildren(resultsSection);

  if (_batchMode) {
    stemGroup.style.display = 'none';
    batchSection.style.display = '';
    batchDrop.style.display = '';
    originalSection.style.display = 'none';
    processBtn.textContent = 'Process All';
    processBtn.disabled = true;
    clearChildren(batchFiles);
    batchFiles.style.display = 'none';
  } else {
    stemGroup.style.display = '';
    batchSection.style.display = 'none';
    batchDrop.style.display = 'none';
    batchFiles.style.display = 'none';
    processBtn.textContent = 'Process';
    refreshStems();
  }
}

// ─── Batch file upload ──────────────────────────────────────────────

async function handleBatchFiles(fileList) {
  const formData = new FormData();
  for (const f of fileList) formData.append('files', f);

  const batchFileListEl = document.getElementById('enhance-batch-files');
  const processBtn = document.getElementById('enhance-process');
  const resultsSection = document.getElementById('enhance-results');

  // Clear previous results when loading new files
  clearChildren(resultsSection);
  _batchResults = [];

  clearChildren(batchFileListEl);
  batchFileListEl.style.display = '';
  batchFileListEl.appendChild(el('div', { className: 'batch-file-item' }, 'Uploading...'));

  try {
    const resp = await fetch('/api/upload-batch', { method: 'POST', body: formData });
    const data = await resp.json();
    const files = data.files || [];

    _batchFiles = files.filter(f => !f.error);
    clearChildren(batchFileListEl);

    for (const f of files) {
      const icon = f.error ? '\u274C' : '\u2705';
      const text = f.error ? `${f.filename} — ${f.error}` : f.filename;
      const cls = f.error ? 'batch-file-item batch-file-error' : 'batch-file-item';
      batchFileListEl.appendChild(el('div', { className: cls }, `${icon} ${text}`));
    }

    processBtn.disabled = _batchFiles.length === 0;
  } catch (err) {
    clearChildren(batchFileListEl);
    batchFileListEl.appendChild(
      el('div', { className: 'batch-file-item batch-file-error' }, `Upload failed: ${err.message}`),
    );
    processBtn.disabled = true;
  }
}

// ─── Presets ──────────────────────────────────────────────────────────

let _presets = [];

async function loadPresets() {
  try {
    const data = await api('/enhance/presets');
    _presets = data.presets || [];
    const select = document.getElementById('enhance-preset');
    clearChildren(select);

    // Group by type
    const denoiseGroup = el('optgroup', { label: 'Denoise' });
    const dereverbGroup = el('optgroup', { label: 'Dereverb' });

    for (const p of _presets) {
      const opt = el('option', { value: p.key }, p.label);
      if (p.key.startsWith('dereverb')) {
        dereverbGroup.appendChild(opt);
      } else {
        denoiseGroup.appendChild(opt);
      }
    }

    select.append(denoiseGroup, dereverbGroup);
    updatePresetDescription();
  } catch (err) {
    console.error('Failed to load presets:', err);
  }
}

function updatePresetDescription() {
  const select = document.getElementById('enhance-preset');
  const desc = document.getElementById('enhance-preset-desc');
  const preset = _presets.find(p => p.key === select.value);
  desc.textContent = preset ? preset.description : '';
}

// ─── Auto-tune options ──────────────────────────────────────────────

async function loadAutotuneOptions() {
  try {
    const data = await api('/enhance/autotune-options');
    const keySelect = document.getElementById('enhance-at-key');
    const scaleSelect = document.getElementById('enhance-at-scale');

    clearChildren(keySelect);
    for (const k of data.keys || []) {
      keySelect.appendChild(el('option', { value: k }, k));
    }

    clearChildren(scaleSelect);
    for (const s of data.scales || []) {
      scaleSelect.appendChild(el('option', { value: s.key }, s.label));
    }
  } catch (err) {
    console.error('Failed to load autotune options:', err);
  }
}

// ─── Stems ───────────────────────────────────────────────────────────

async function refreshStems() {
  if (_batchMode) return;
  try {
    const data = await api('/enhance/stems');
    const stems = data.stems || [];
    const select = document.getElementById('enhance-stem');
    const prevValue = select.value;

    clearChildren(select);

    select.appendChild(el('option', { value: '' }, '-- Select audio --'));

    // Group by source
    if (stems.length > 0) {
      const groups = {};
      for (const s of stems) {
        const groupLabel = s.source === 'separation' ? 'Separated Stems'
          : s.source === 'enhanced' ? 'Enhanced'
          : s.source === 'upload' ? 'Uploads'
          : 'Other';
        if (!groups[groupLabel]) groups[groupLabel] = [];
        groups[groupLabel].push(s);
      }

      for (const [label, items] of Object.entries(groups)) {
        const group = el('optgroup', { label });
        for (const s of items) {
          group.appendChild(el('option', { value: s.path }, s.label));
        }
        select.appendChild(group);
      }
    }

    // Always offer file browse
    select.appendChild(el('option', { value: '__browse__' }, 'Browse file\u2026'));

    // Restore previous selection if still available
    if (prevValue && prevValue !== '__browse__' && [...select.options].some(o => o.value === prevValue)) {
      select.value = prevValue;
    }

    document.getElementById('enhance-process').disabled = !select.value || select.value === '__browse__';
    updateOriginalPreview();
  } catch { /* ignore */ }
}

// ─── Original preview ────────────────────────────────────────────────

let _originalPlayer = null;
let _originalPeaks = null;

function updateOriginalPreview() {
  const select = document.getElementById('enhance-stem');
  const section = document.getElementById('enhance-original');
  const stemPath = select.value;

  // Destroy old player
  if (_originalPlayer) {
    _originalPlayer.ws.destroy();
    const idx = _players.indexOf(_originalPlayer);
    if (idx !== -1) _players.splice(idx, 1);
    _originalPlayer = null;
    _originalPeaks = null;
  }

  if (!stemPath) {
    section.style.display = 'none';
    return;
  }

  section.style.display = '';

  // Remove old card if present (keep the title)
  while (section.children.length > 1) section.removeChild(section.lastChild);

  const url = `/api/audio/stream?path=${encodeURIComponent(stemPath)}`;
  const label = select.options[select.selectedIndex]?.text || 'Original';
  const { card, ws } = _createPlayer(`Original: ${label}`, url, stemPath);
  section.appendChild(card);
  _originalPlayer = _players[_players.length - 1];

  // Pre-decode peaks for diff visualization later
  decodeAudioPeaks(url, 200).then(peaks => {
    _originalPeaks = peaks;
  }).catch(() => {});
}

// ─── Single-file process ────────────────────────────────────────────

async function startEnhance() {
  const stemPath = document.getElementById('enhance-stem').value;
  const preset = document.getElementById('enhance-preset').value;
  if (!stemPath || !preset) return;

  const processBtn = document.getElementById('enhance-process');
  const progressCard = document.getElementById('enhance-progress');

  processBtn.disabled = true;
  document.getElementById('enhance-progress-fill').style.width = '0%';
  document.getElementById('enhance-pct').textContent = '0%';
  document.getElementById('enhance-stage').textContent = '';
  progressCard.classList.remove('hidden');

  try {
    const { job_id } = await api('/enhance', {
      method: 'POST',
      body: JSON.stringify({ stem_path: stemPath, preset }),
    });

    pollJob(job_id, {
      onProgress(progress, stage) {
        document.getElementById('enhance-progress-fill').style.width = `${(progress * 100).toFixed(0)}%`;
        document.getElementById('enhance-pct').textContent = `${(progress * 100).toFixed(0)}%`;
        document.getElementById('enhance-stage').textContent = stage;
      },
      onDone(result) {
        progressCard.classList.add('hidden');
        processBtn.disabled = false;
        showResult(result);
        appState.emit('enhanceReady', result);
      },
      onError(msg) {
        progressCard.classList.add('hidden');
        processBtn.disabled = false;
        const resultsSection = document.getElementById('enhance-results');
        resultsSection.appendChild(
          el('div', { className: 'banner banner-error' }, `Enhancement failed: ${msg}`),
        );
      },
    });
  } catch (err) {
    progressCard.classList.add('hidden');
    processBtn.disabled = false;
  }
}

// ─── Auto-tune process ──────────────────────────────────────────────

async function startAutotune() {
  const stemPath = document.getElementById('enhance-stem').value;
  if (!stemPath) return;

  const key = document.getElementById('enhance-at-key').value;
  const scale = document.getElementById('enhance-at-scale').value;
  const strength = parseInt(document.getElementById('enhance-at-strength').value, 10) / 100;
  const humanize = parseInt(document.getElementById('enhance-at-humanize').value, 10) / 100;

  const processBtn = document.getElementById('enhance-process');
  const progressCard = document.getElementById('enhance-progress');

  processBtn.disabled = true;
  document.getElementById('enhance-progress-fill').style.width = '0%';
  document.getElementById('enhance-pct').textContent = '0%';
  document.getElementById('enhance-stage').textContent = '';
  progressCard.classList.remove('hidden');

  try {
    const { job_id } = await api('/enhance/autotune', {
      method: 'POST',
      body: JSON.stringify({
        stem_path: stemPath, key, scale,
        correction_strength: strength, humanize,
      }),
    });

    pollJob(job_id, {
      onProgress(progress, stage) {
        document.getElementById('enhance-progress-fill').style.width = `${(progress * 100).toFixed(0)}%`;
        document.getElementById('enhance-pct').textContent = `${(progress * 100).toFixed(0)}%`;
        document.getElementById('enhance-stage').textContent = stage;
      },
      onDone(result) {
        progressCard.classList.add('hidden');
        processBtn.disabled = false;
        showResult(result);
        appState.emit('enhanceReady', result);
      },
      onError(msg) {
        progressCard.classList.add('hidden');
        processBtn.disabled = false;
        const resultsSection = document.getElementById('enhance-results');
        resultsSection.appendChild(
          el('div', { className: 'banner banner-error' }, `Auto-tune failed: ${msg}`),
        );
      },
    });
  } catch (err) {
    progressCard.classList.add('hidden');
    processBtn.disabled = false;
  }
}

// ─── Single-file result display ─────────────────────────────────────

function _ensureClearAllBtn() {
  const resultsSection = document.getElementById('enhance-results');
  if (document.getElementById('enhance-clear-all')) return;

  const clearAllBtn = el('button', {
    className: 'btn btn-sm',
    id: 'enhance-clear-all',
    style: { marginBottom: '12px' },
  }, '\u2715 Clear All');

  clearAllBtn.addEventListener('click', () => {
    // Destroy all result players (skip _originalPlayer)
    for (let i = _players.length - 1; i >= 0; i--) {
      if (_originalPlayer && _players[i] === _originalPlayer) continue;
      _players[i].ws.destroy();
      _players.splice(i, 1);
    }
    clearChildren(resultsSection);
  });

  resultsSection.insertBefore(clearAllBtn, resultsSection.firstChild);
}

async function showResult(result) {
  const resultsSection = document.getElementById('enhance-results');
  const outputUrl = `/api/audio/stream?path=${encodeURIComponent(result.output_path)}`;

  // Update appState for export tab
  appState.enhancePaths = appState.enhancePaths || {};
  appState.enhancePaths[result.label] = result.output_path;

  const { card, ws } = _createPlayer(
    `Enhanced: ${result.label}`,
    outputUrl,
    result.output_path,
  );

  // Close button
  const closeBtn = el('button', {
    className: 'btn btn-sm',
    style: { marginLeft: 'auto', fontSize: '14px', lineHeight: '1', padding: '2px 6px' },
    title: 'Remove',
  }, '\u2715');
  closeBtn.addEventListener('click', () => {
    ws.destroy();
    const idx = _players.findIndex(p => p.ws === ws);
    if (idx !== -1) _players.splice(idx, 1);
    card.remove();
    const remaining = resultsSection.querySelectorAll('.stem-card');
    if (remaining.length === 0) {
      const btn = document.getElementById('enhance-clear-all');
      if (btn) btn.remove();
    }
  });
  const actions = card.querySelector('.stem-actions');
  if (actions) actions.appendChild(closeBtn);

  // Overlay diff canvas on the waveform — colors bars by change intensity
  const waveContainer = card.querySelector('.stem-waveform');
  waveContainer.style.position = 'relative';
  const diffCanvas = el('canvas', {
    className: 'enhance-diff-canvas',
    style: { position: 'absolute', inset: '0', width: '100%', height: '100%', pointerEvents: 'none', zIndex: '1' },
  });

  // Ensure Clear All button exists, then insert card after it
  _ensureClearAllBtn();
  const clearBtn = document.getElementById('enhance-clear-all');
  if (clearBtn && clearBtn.nextSibling) {
    resultsSection.insertBefore(card, clearBtn.nextSibling);
  } else {
    resultsSection.appendChild(card);
  }

  // Render diff overlay once audio is decoded
  ws.on('ready', async () => {
    try {
      waveContainer.appendChild(diffCanvas);
      const barCount = _originalPeaks ? _originalPeaks.length : 200;
      const resultPeaks = await decodeAudioPeaks(outputUrl, barCount);
      renderDiffWaveform(diffCanvas, waveContainer, resultPeaks, _originalPeaks);
    } catch (err) {
      console.error('Diff waveform error:', err);
    }
  });
}

// ─── Batch process ──────────────────────────────────────────────────

async function startBatchEnhance() {
  const preset = document.getElementById('enhance-preset').value;
  if (!preset || _batchFiles.length === 0) return;

  const processBtn = document.getElementById('enhance-process');
  const progressCard = document.getElementById('enhance-progress');

  processBtn.disabled = true;
  document.getElementById('enhance-progress-fill').style.width = '0%';
  document.getElementById('enhance-pct').textContent = '0%';
  document.getElementById('enhance-stage').textContent = '';
  progressCard.classList.remove('hidden');

  try {
    const { job_id } = await api('/enhance/batch', {
      method: 'POST',
      body: JSON.stringify({
        preset,
        files: _batchFiles.map(f => ({ filename: f.filename, path: f.path })),
      }),
    });

    pollJob(job_id, {
      onProgress(progress, stage) {
        document.getElementById('enhance-progress-fill').style.width = `${(progress * 100).toFixed(0)}%`;
        document.getElementById('enhance-pct').textContent = `${(progress * 100).toFixed(0)}%`;
        document.getElementById('enhance-stage').textContent = stage;
      },
      onDone(result) {
        progressCard.classList.add('hidden');
        processBtn.disabled = false;
        showBatchResults(result.results, result.preset);
        appState.emit('enhanceReady', result);
      },
      onError(msg) {
        progressCard.classList.add('hidden');
        processBtn.disabled = false;
        const resultsSection = document.getElementById('enhance-results');
        resultsSection.appendChild(
          el('div', { className: 'banner banner-error' }, `Batch enhancement failed: ${msg}`),
        );
      },
    });
  } catch (err) {
    progressCard.classList.add('hidden');
    processBtn.disabled = false;
  }
}

// ─── Batch result display ───────────────────────────────────────────

let _batchResults = [];  // mutable list for Save All — items removed via close button

function _updateSaveAllBtn() {
  const btn = document.getElementById('enhance-save-all');
  if (!btn) return;
  const count = _batchResults.length;
  if (count === 0) {
    btn.style.display = 'none';
  } else {
    btn.style.display = '';
    btn.textContent = `\u2193 Save All (${count} files)`;
    btn.disabled = false;
  }
}

function showBatchResults(results, preset) {
  const resultsSection = document.getElementById('enhance-results');
  clearChildren(resultsSection);

  _batchResults = results.filter(r => !r.error);
  const failed = results.filter(r => r.error);

  // Toolbar: Save All + Clear All
  const saveAllBtn = el('button', {
    className: 'btn btn-primary batch-save-all',
    id: 'enhance-save-all',
    style: { display: _batchResults.length > 0 ? '' : 'none' },
  }, `\u2193 Save All (${_batchResults.length} files)`);

  const clearAllBtn = el('button', {
    className: 'btn btn-sm',
    style: { marginLeft: '8px' },
  }, '\u2715 Clear All');

  clearAllBtn.addEventListener('click', () => {
    _batchResults = [];
    clearChildren(resultsSection);
    // Destroy all batch players
    for (let i = _players.length - 1; i >= 0; i--) {
      _players[i].ws.destroy();
      _players.splice(i, 1);
    }
  });

  saveAllBtn.addEventListener('click', async () => {
    const origText = saveAllBtn.textContent;
    saveAllBtn.disabled = true;
    saveAllBtn.textContent = 'Preparing zip...';

    try {
      const resp = await fetch('/api/enhance/batch/save-all', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          paths: _batchResults.map(r => ({ filename: r.output_name, path: r.path })),
        }),
      });
      const blob = await resp.blob();

      if (window.showSaveFilePicker) {
        const handle = await window.showSaveFilePicker({
          suggestedName: `batch-enhanced-${preset}.zip`,
          types: [{ accept: { 'application/zip': ['.zip'] } }],
        });
        const writable = await handle.createWritable();
        await writable.write(blob);
        await writable.close();
      } else {
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `batch-enhanced-${preset}.zip`;
        a.click();
        URL.revokeObjectURL(url);
      }

      saveAllBtn.textContent = 'Saved!';
      setTimeout(() => { saveAllBtn.textContent = origText; saveAllBtn.disabled = false; }, 2000);
    } catch (err) {
      saveAllBtn.textContent = origText;
      saveAllBtn.disabled = false;
    }
  });

  const toolbar = el('div', { style: { display: 'flex', alignItems: 'center', marginBottom: '12px' } },
    saveAllBtn, clearAllBtn);
  resultsSection.appendChild(toolbar);

  // Error banner
  if (failed.length > 0) {
    const errorLines = failed.map(r => `${r.filename}: ${r.error}`).join('\n');
    resultsSection.appendChild(
      el('div', { className: 'banner banner-error', style: { whiteSpace: 'pre-wrap', marginBottom: '12px' } },
        `${failed.length} file(s) failed:\n${errorLines}`),
    );
  }

  // Result cards with close button
  for (const r of _batchResults) {
    _appendBatchCard(resultsSection, r);
  }
}

function _appendBatchCard(container, r) {
  const url = `/api/audio/stream?path=${encodeURIComponent(r.path)}`;
  const { card, ws } = _createPlayer(r.output_name, url, r.path);

  // Close button — removes card and excludes from Save All
  const closeBtn = el('button', {
    className: 'btn btn-sm',
    style: { marginLeft: 'auto', fontSize: '14px', lineHeight: '1', padding: '2px 6px' },
    title: 'Remove from results',
  }, '\u2715');

  closeBtn.addEventListener('click', () => {
    // Destroy wavesurfer player
    ws.destroy();
    const idx = _players.findIndex(p => p.ws === ws);
    if (idx !== -1) _players.splice(idx, 1);
    // Remove from Save All list
    const ri = _batchResults.indexOf(r);
    if (ri !== -1) _batchResults.splice(ri, 1);
    // Remove DOM
    card.remove();
    _updateSaveAllBtn();
  });

  // Insert close button into the card header actions
  const actions = card.querySelector('.stem-actions');
  if (actions) actions.appendChild(closeBtn);

  container.appendChild(card);
}
