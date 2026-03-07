/**
 * Enhance tab — vocal cleanup and audio enhancement.
 *
 * Phase 1: UVR denoise / dereverb via audio-separator presets.
 * Shows original stem + processed result with diff waveform overlay.
 */

import { appState, api, pollJob, el, formatTime, saveFileAs } from '../app.js';
import { createWaveform } from './waveform.js';
import { decodeAudioPeaks, renderDiffWaveform } from './waveform-diff.js';

function clearChildren(elem) {
  while (elem.firstChild) elem.removeChild(elem.firstChild);
}

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

  // Section title
  const header = el('div', {
    style: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' },
  },
    el('span', { className: 'section-title' }, 'Audio Enhancement'),
  );

  // Stem selector
  const stemLabel = el('label', { className: 'field-label' }, 'Source Audio');
  const stemSelect = el('select', { id: 'enhance-stem', className: 'select' });
  const stemGroup = el('div', { className: 'field-group', style: { marginBottom: '12px' } },
    stemLabel, stemSelect,
  );

  // Preset selector
  const presetLabel = el('label', { className: 'field-label' }, 'Enhancement');
  const presetSelect = el('select', { id: 'enhance-preset', className: 'select' });
  const presetDesc = el('div', {
    id: 'enhance-preset-desc',
    className: 'text-dim',
    style: { fontSize: '12px', marginTop: '4px' },
  });
  const presetGroup = el('div', { className: 'field-group', style: { marginBottom: '12px' } },
    presetLabel, presetSelect, presetDesc,
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

  // No-stems message
  const emptyMsg = el('div', {
    id: 'enhance-empty',
    className: 'text-dim',
    style: { padding: '20px', textAlign: 'center' },
  }, 'No audio available. Run separation or upload a file first.');

  // Original stem player (shown when stem is selected)
  const originalSection = el('div', { id: 'enhance-original', style: { display: 'none', marginTop: '16px' } },
    el('div', { className: 'section-title', style: { fontSize: '13px', marginBottom: '6px' } }, 'Original'),
  );

  // Results container
  const resultsSection = el('div', { id: 'enhance-results', style: { marginTop: '16px' } });

  const controlRow = el('div', {
    style: { display: 'flex', gap: '12px', alignItems: 'flex-start' },
  },
    el('div', { style: { flex: '1' } }, stemGroup),
    el('div', { style: { flex: '1' } }, presetGroup),
    el('div', { style: { paddingTop: '22px' } }, processBtn),
  );

  panel.append(header, emptyMsg, controlRow, progressCard, originalSection, resultsSection);

  // ─── Load presets ───
  loadPresets();

  // ─── Wire events ───
  processBtn.addEventListener('click', startEnhance);

  stemSelect.addEventListener('change', () => {
    updateOriginalPreview();
    processBtn.disabled = !stemSelect.value;
  });

  presetSelect.addEventListener('change', updatePresetDescription);

  // Listen for stems and files becoming available
  appState.on('stemsReady', () => refreshStems());
  appState.on('fileLoaded', () => refreshStems());
  appState.on('enhanceReady', () => refreshStems());
  appState.on('generateReady', () => refreshStems());
  appState.on('composeReady', () => refreshStems());
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

// ─── Stems ───────────────────────────────────────────────────────────

async function refreshStems() {
  try {
    const data = await api('/enhance/stems');
    const stems = data.stems || [];
    const select = document.getElementById('enhance-stem');
    const emptyMsg = document.getElementById('enhance-empty');
    const prevValue = select.value;

    clearChildren(select);

    if (stems.length === 0) {
      emptyMsg.style.display = '';
      document.getElementById('enhance-process').disabled = true;
      return;
    }

    emptyMsg.style.display = 'none';

    // Group by source
    const groups = {};
    for (const s of stems) {
      const groupLabel = s.source === 'separation' ? 'Separated Stems'
        : s.source === 'enhanced' ? 'Enhanced'
        : s.source === 'upload' ? 'Uploads'
        : 'Other';
      if (!groups[groupLabel]) groups[groupLabel] = [];
      groups[groupLabel].push(s);
    }

    select.appendChild(el('option', { value: '' }, '-- Select audio --'));
    for (const [label, items] of Object.entries(groups)) {
      const group = el('optgroup', { label });
      for (const s of items) {
        group.appendChild(el('option', { value: s.path }, s.label));
      }
      select.appendChild(group);
    }

    // Restore previous selection if still available
    if (prevValue && [...select.options].some(o => o.value === prevValue)) {
      select.value = prevValue;
    }

    document.getElementById('enhance-process').disabled = !select.value;
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

// ─── Process ─────────────────────────────────────────────────────────

async function startEnhance() {
  const stemPath = document.getElementById('enhance-stem').value;
  const preset = document.getElementById('enhance-preset').value;
  if (!stemPath || !preset) return;

  const processBtn = document.getElementById('enhance-process');
  const progressCard = document.getElementById('enhance-progress');

  processBtn.disabled = true;
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

// ─── Result display ──────────────────────────────────────────────────

async function showResult(result) {
  const resultsSection = document.getElementById('enhance-results');
  const outputUrl = `/api/audio/stream?path=${encodeURIComponent(result.output_path)}`;

  // Update appState for export tab
  appState.enhancePaths = appState.enhancePaths || {};
  appState.enhancePaths[result.label] = result.output_path;

  // Create result card with standard player
  const { card, ws } = _createPlayer(
    `Enhanced: ${result.label}`,
    outputUrl,
    result.output_path,
  );

  // Add diff canvas overlay below the waveform
  const diffContainer = el('div', {
    className: 'enhance-diff-container',
    style: { position: 'relative', height: '40px', margin: '0 12px 8px', borderRadius: '4px', overflow: 'hidden', background: 'var(--surface-raised)' },
  });
  const diffCanvas = el('canvas', {
    className: 'enhance-diff-canvas',
    style: { position: 'absolute', inset: '0', width: '100%', height: '100%' },
  });
  const diffLabel = el('div', {
    style: { position: 'absolute', top: '2px', right: '6px', fontSize: '10px', color: 'var(--text-dim)', pointerEvents: 'none' },
  }, 'Change intensity');
  diffContainer.append(diffCanvas, diffLabel);
  card.appendChild(diffContainer);

  // Insert at top of results
  if (resultsSection.firstChild) {
    resultsSection.insertBefore(card, resultsSection.firstChild);
  } else {
    resultsSection.appendChild(card);
  }

  // Render diff waveform once audio is decoded
  try {
    const barCount = _originalPeaks ? _originalPeaks.length : 200;
    const resultPeaks = await decodeAudioPeaks(outputUrl, barCount);
    renderDiffWaveform(diffCanvas, diffContainer, resultPeaks, _originalPeaks);
  } catch (err) {
    console.error('Diff waveform error:', err);
  }
}
