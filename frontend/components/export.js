/**
 * Export tab — select artifacts, choose format, download.
 */

import { appState, api, pollJob, el, saveFileAs } from '../app.js';

function clearChildren(elem) {
  while (elem.firstChild) elem.removeChild(elem.firstChild);
}

export function initExport() {
  const panel = document.getElementById('panel-export');
  const layout = el('div', { className: 'two-col' });

  // ─── Left: controls ───
  const left = el('div', { className: 'col-left' });

  const itemsSection = el('div', { className: 'form-group' },
    el('label', {}, 'Available artifacts'),
    el('div', { className: 'checkbox-group', id: 'export-items' },
      el('span', { className: 'text-dim' }, 'Process audio first to see artifacts'),
    ),
  );

  const formatGroup = el('div', { className: 'form-group' },
    el('label', {}, 'Output format'),
    el('select', { id: 'export-format' },
      el('option', { value: 'wav' }, 'WAV'),
      el('option', { value: 'flac' }, 'FLAC'),
      el('option', { value: 'mp3' }, 'MP3'),
      el('option', { value: 'ogg' }, 'OGG'),
    ),
  );

  const exportBtn = el('button', { className: 'btn btn-primary', id: 'export-start', disabled: 'true' }, 'Export');
  const zipBtn = el('button', { className: 'btn', id: 'export-zip', disabled: 'true', style: { marginTop: '8px' } }, 'Download All as ZIP');

  left.append(itemsSection, formatGroup, exportBtn, zipBtn);

  // ─── Right: results ───
  const right = el('div', { className: 'col-right' });

  const progressCard = el('div', { className: 'card hidden', id: 'export-progress' },
    el('div', { className: 'progress-container' },
      el('div', { className: 'progress-bar' },
        el('div', { className: 'progress-fill', id: 'export-progress-fill' }),
      ),
      el('div', { className: 'progress-label' },
        el('span', { id: 'export-stage' }, ''),
        el('span', { id: 'export-pct' }, '0%'),
      ),
    ),
  );

  const resultsContainer = el('div', { id: 'export-results' });

  right.append(progressCard, resultsContainer);
  layout.append(left, right);
  panel.appendChild(layout);

  // ─── Wire events ───
  document.getElementById('export-start').addEventListener('click', startExport);
  document.getElementById('export-zip').addEventListener('click', downloadZip);

  // Listen for all ready events
  appState.on('fileLoaded', refreshArtifacts);
  appState.on('stemsReady', refreshArtifacts);
  appState.on('midiReady', refreshArtifacts);
  appState.on('generateReady', refreshArtifacts);
  appState.on('composeReady', refreshArtifacts);
  appState.on('mixReady', refreshArtifacts);
  appState.on('sfxReady', refreshArtifacts);
  appState.on('transformReady', refreshArtifacts);
  appState.on('enhanceReady', refreshArtifacts);
}

function refreshArtifacts() {
  const container = document.getElementById('export-items');
  clearChildren(container);

  const items = collectArtifacts();
  if (items.length === 0) {
    container.appendChild(el('span', { className: 'text-dim' }, 'No artifacts yet'));
    document.getElementById('export-start').disabled = true;
    return;
  }

  document.getElementById('export-start').disabled = false;
  document.getElementById('export-zip').disabled = false;

  for (const item of items) {
    container.appendChild(
      el('label', {},
        el('input', { type: 'checkbox', value: item.path, checked: 'true' }),
        `${item.label} (${item.type})`,
      ),
    );
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

async function startExport() {
  const checkedEls = document.querySelectorAll('#export-items input[type="checkbox"]:checked');
  const items = Array.from(checkedEls).map(el => el.value);
  const format = document.getElementById('export-format').value;

  if (!items.length) return;

  const progressCard = document.getElementById('export-progress');
  const resultsContainer = document.getElementById('export-results');
  progressCard.classList.remove('hidden');
  clearChildren(resultsContainer);

  try {
    const { job_id } = await api('/export', {
      method: 'POST',
      body: JSON.stringify({ items, format }),
    });

    pollJob(job_id, {
      onProgress(progress, stage) {
        document.getElementById('export-progress-fill').style.width = `${(progress * 100).toFixed(0)}%`;
        document.getElementById('export-pct').textContent = `${(progress * 100).toFixed(0)}%`;
        document.getElementById('export-stage').textContent = stage;
      },
      onDone(result) {
        progressCard.classList.add('hidden');
        showExportResults(result.exported || []);
      },
      onError(msg) {
        progressCard.classList.add('hidden');
        resultsContainer.appendChild(
          el('div', { className: 'banner banner-error' }, `Export failed: ${msg}`),
        );
      },
    });
  } catch (err) {
    progressCard.classList.add('hidden');
  }
}

function showExportResults(exported) {
  const container = document.getElementById('export-results');

  container.appendChild(
    el('div', { className: 'banner banner-success' }, `Exported ${exported.length} file(s)`),
  );

  for (const path of exported) {
    const name = path.split('/').pop();
    container.appendChild(
      el('div', { className: 'export-item' },
        el('span', { className: 'item-name' }, name),
        el('button', {
          className: 'btn btn-sm',
          onClick: () => saveFileAs(`/api/audio/download?path=${encodeURIComponent(path)}`, name),
        }, 'Download'),
      ),
    );
  }
}

async function downloadZip() {
  const checkedEls = document.querySelectorAll('#export-items input[type="checkbox"]:checked');
  const items = Array.from(checkedEls).map(el => el.value);
  if (!items.length) return;

  try {
    const res = await fetch('/api/export/download-zip', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ items }),
    });
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'stemforge_export.zip';
    document.body.appendChild(a);
    a.click();
    setTimeout(() => { a.remove(); URL.revokeObjectURL(url); }, 1000);
  } catch (err) {
    alert(`ZIP download failed: ${err.message}`);
  }
}
