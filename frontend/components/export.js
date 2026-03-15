/**
 * Export tab — preview artifacts, choose format, export with auto-download.
 */

import { appState, api, pollJob, el, formatTime, saveFileAs } from '../app.js';
import { createWaveform } from './waveform.js';
import { transportLoad, transportStop } from './audio-player.js';

function clearChildren(elem) {
  while (elem.firstChild) elem.removeChild(elem.firstChild);
}

const _LOSSY_FORMATS = new Set(['mp3', 'ogg', 'm4a']);

/** Active waveform players for exclusive playback. */
let _players = [];

function _stopOtherPlayers(except) {
  for (const p of _players) {
    if (p.ws !== except && p.ws.isPlaying()) {
      p.ws.stop();
      p.playBtn.textContent = '\u25B6 Play';
    }
  }
}

export function initExport() {
  const panel = document.getElementById('panel-export');

  // ─── Top bar: format, bitrate, export button ───
  const formatSelect = el('select', { id: 'export-format' },
    el('option', { value: 'wav' }, 'WAV (lossless)'),
    el('option', { value: 'flac' }, 'FLAC (lossless)'),
    el('option', { value: 'aiff' }, 'AIFF (lossless)'),
    el('option', { value: 'mp3' }, 'MP3'),
    el('option', { value: 'ogg' }, 'OGG Opus'),
    el('option', { value: 'm4a' }, 'M4A (AAC)'),
  );

  const bitrateSlider = el('input', {
    type: 'range', id: 'export-bitrate', min: '64', max: '320', step: '32', value: '192',
  });
  const bitrateLabel = el('span', { id: 'export-bitrate-value' }, '192 kbps');
  const bitrateGroup = el('span', { className: 'export-bitrate-inline hidden', id: 'export-bitrate-group' },
    bitrateLabel, bitrateSlider,
  );

  const exportBtn = el('button', { className: 'btn btn-primary', id: 'export-start', disabled: 'true' }, 'Export Selected');

  const topBar = el('div', { className: 'export-top-bar' },
    el('label', {}, 'Format: ', formatSelect),
    bitrateGroup,
    exportBtn,
  );

  // ─── Progress bar (hidden by default) ───
  const progressBar = el('div', { className: 'export-progress-bar hidden', id: 'export-progress' },
    el('div', { className: 'progress-fill', id: 'export-progress-fill' }),
  );

  // ─── Artifact preview area ───
  const previewArea = el('div', { id: 'export-previews', className: 'export-previews' },
    el('span', { className: 'text-dim' }, 'Load a file to get started'),
  );

  panel.append(topBar, progressBar, previewArea);

  // ─── Wire events ───
  exportBtn.addEventListener('click', startExport);

  formatSelect.addEventListener('change', () => {
    const fmt = formatSelect.value;
    const bg = document.getElementById('export-bitrate-group');
    const slider = document.getElementById('export-bitrate');
    if (_LOSSY_FORMATS.has(fmt)) {
      bg.classList.remove('hidden');
      const defaultBr = fmt === 'ogg' ? 128 : 192;
      slider.value = defaultBr;
      _updateBitrateLabel();
    } else {
      bg.classList.add('hidden');
    }
  });

  bitrateSlider.addEventListener('input', _updateBitrateLabel);

  // Listen for all ready events
  appState.on('fileLoaded', refreshPreviews);
  appState.on('stemsReady', refreshPreviews);
  appState.on('midiReady', refreshPreviews);
  appState.on('generateReady', refreshPreviews);
  appState.on('composeReady', refreshPreviews);
  appState.on('mixReady', refreshPreviews);
  appState.on('sfxReady', refreshPreviews);
  appState.on('transformReady', refreshPreviews);
  appState.on('enhanceReady', refreshPreviews);
}

function _updateBitrateLabel() {
  const v = document.getElementById('export-bitrate').value;
  document.getElementById('export-bitrate-value').textContent = v + ' kbps';
}

// ─── Preview players ──────────────────────────────────────────────────

function refreshPreviews() {
  const container = document.getElementById('export-previews');

  // Destroy old players
  for (const p of _players) { try { p.ws.destroy(); } catch {} }
  _players = [];
  clearChildren(container);

  const items = collectArtifacts();
  if (items.length === 0) {
    container.appendChild(el('span', { className: 'text-dim' }, 'No artifacts yet'));
    document.getElementById('export-start').disabled = true;
    return;
  }

  document.getElementById('export-start').disabled = false;

  for (const item of items) {
    const streamUrl = `/api/audio/stream?path=${encodeURIComponent(item.path)}`;

    const checkbox = el('input', { type: 'checkbox', checked: 'true', 'data-path': item.path });
    const playBtn = el('button', { className: 'btn btn-sm' }, '\u25B6 Play');
    const stopBtn = el('button', { className: 'btn btn-sm' }, '\u25A0');
    const rewindBtn = el('button', { className: 'btn btn-sm' }, '\u23EA');
    const timeLabel = el('span', { className: 'stem-time' }, '0:00 / 0:00');

    const header = el('div', { className: 'stem-card-header' },
      el('label', { className: 'export-check-label' }, checkbox,
        el('span', { className: 'stem-label' }, `${item.label}  `, el('span', { className: 'text-dim' }, `(${item.type})`)),
      ),
      el('div', { className: 'stem-actions' },
        playBtn, stopBtn, rewindBtn, timeLabel,
      ),
    );

    const waveContainer = el('div', { className: 'stem-waveform' });
    const card = el('div', { className: 'stem-card' }, header, waveContainer);
    container.appendChild(card);

    const ws = createWaveform(waveContainer, { height: 50 });
    ws.load(streamUrl);
    _players.push({ ws, playBtn });

    playBtn.addEventListener('click', () => {
      if (ws.isPlaying()) {
        ws.pause();
        playBtn.textContent = '\u25B6 Play';
      } else {
        _stopOtherPlayers(ws);
        ws.play();
        playBtn.textContent = '\u23F8 Pause';
        transportLoad(streamUrl, item.label, false, 'Export');
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

    ws.on('finish', () => {
      playBtn.textContent = '\u25B6 Play';
      transportStop();
    });
  }
}

function collectArtifacts() {
  const items = [];

  // Original upload
  if (appState.audioPath) {
    const name = appState.audioInfo?.filename || appState.audioPath.split('/').pop();
    items.push({ label: name, path: appState.audioPath, type: 'original' });
  }

  // Stems
  for (const [label, path] of Object.entries(appState.stemPaths || {})) {
    items.push({ label, path, type: 'stem' });
  }

  // Generated audio (Synth tab)
  if (appState.musicgenPath) {
    items.push({ label: 'Generated', path: appState.musicgenPath, type: 'generated' });
  }

  // Composed audio (Compose tab)
  for (const entry of appState.composePaths || []) {
    if (entry.path) {
      items.push({ label: entry.title || 'Composed', path: entry.path, type: 'composed' });
    }
  }

  // SFX stems
  for (const [label, path] of Object.entries(appState.sfxPaths || {})) {
    items.push({ label, path, type: 'sfx' });
  }

  // Voice transforms
  for (const [label, path] of Object.entries(appState.voicePaths || {})) {
    items.push({ label, path, type: 'voice' });
  }

  // Enhanced stems
  for (const [label, path] of Object.entries(appState.enhancePaths || {})) {
    items.push({ label, path, type: 'enhanced' });
  }

  // Mix
  if (appState.mixPath) {
    items.push({ label: 'Mix', path: appState.mixPath, type: 'mix' });
  }

  return items;
}

// ─── Export ───────────────────────────────────────────────────────────

async function startExport() {
  const checkedEls = document.querySelectorAll('#export-previews input[type="checkbox"]:checked');
  const items = Array.from(checkedEls).map(cb => cb.dataset.path);
  const format = document.getElementById('export-format').value;

  if (!items.length) return;

  const body = { items, format };
  if (_LOSSY_FORMATS.has(format)) {
    body.bitrate = parseInt(document.getElementById('export-bitrate').value, 10);
  }

  const progressBar = document.getElementById('export-progress');
  const fill = document.getElementById('export-progress-fill');
  progressBar.classList.remove('hidden');
  fill.style.width = '0%';

  try {
    const { job_id } = await api('/export', {
      method: 'POST',
      body: JSON.stringify(body),
    });

    pollJob(job_id, {
      onProgress(progress) {
        fill.style.width = `${(progress * 100).toFixed(0)}%`;
      },
      async onDone(result) {
        progressBar.classList.add('hidden');
        const exported = result.exported || [];
        // Auto-download: single file → save dialog, multiple → zip
        if (exported.length === 1) {
          const name = exported[0].split('/').pop();
          await saveFileAs(`/api/audio/download?path=${encodeURIComponent(exported[0])}`, name);
        } else if (exported.length > 1) {
          await _downloadZip(exported);
        }
      },
      onError(msg) {
        progressBar.classList.add('hidden');
        alert(`Export failed: ${msg}`);
      },
    });
  } catch {
    progressBar.classList.add('hidden');
  }
}

async function _downloadZip(paths) {
  const res = await fetch('/api/export/download-zip', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ items: paths }),
  });
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'stemforge_export.zip';
  document.body.appendChild(a);
  a.click();
  setTimeout(() => { a.remove(); URL.revokeObjectURL(url); }, 1000);
}
