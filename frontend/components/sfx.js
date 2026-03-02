/**
 * SFX tab -- clip-based stem builder.
 *
 * Create a blank audio canvas, place Synth/stem WAV clips at timestamps
 * with per-clip volume/fade, preview the composite waveform, and send
 * the rendered WAV to the Mix tab.
 */

import { appState, api, el, formatTime, saveFileAs } from '../app.js';
import { createWaveform } from './waveform.js';
import { transportLoad } from './audio-player.js';

function clearChildren(elem) {
  while (elem.firstChild) elem.removeChild(elem.firstChild);
}

// Module state
let _currentSfxId = null;

export function initSfx() {
  const panel = document.getElementById('panel-sfx');
  const layout = el('div', { className: 'two-col' });

  // ─── Left: controls ───
  const left = el('div', { className: 'col-left' });

  // -- Create section --
  const createCard = el('div', { className: 'card' },
    el('div', { className: 'card-header' }, 'CREATE SFX STEM'),
    el('div', { className: 'form-group' },
      el('label', {}, 'Name'),
      el('input', { type: 'text', id: 'sfx-name', value: 'Untitled SFX', placeholder: 'SFX stem name' }),
    ),
    el('div', { className: 'form-group' },
      el('label', {}, 'Mode'),
      el('select', { id: 'sfx-mode' },
        el('option', { value: 'manual' }, 'Manual duration'),
        el('option', { value: 'reference' }, 'Match reference stem'),
      ),
    ),
    el('div', { className: 'form-group', id: 'sfx-duration-group' },
      el('label', {}, 'Duration (seconds)'),
      el('div', { className: 'slider-row' },
        el('input', { type: 'range', id: 'sfx-duration', min: '1', max: '120', value: '10', step: '1' }),
        el('span', { className: 'slider-value', id: 'sfx-duration-val' }, '10s'),
      ),
    ),
    el('div', { className: 'form-group hidden', id: 'sfx-ref-group' },
      el('label', {}, 'Reference stem'),
      el('select', { id: 'sfx-ref-stem' },
        el('option', { value: '' }, '-- select stem --'),
      ),
    ),
    el('button', { className: 'btn btn-primary', id: 'sfx-create-btn' }, 'Create Canvas'),
  );

  // -- SFX List --
  const listCard = el('div', { className: 'card' },
    el('div', { className: 'card-header' }, 'SFX STEMS'),
    el('div', { id: 'sfx-list' },
      el('span', { className: 'text-dim' }, 'No SFX stems yet'),
    ),
  );

  // -- Placement controls (shown when an SFX is selected) --
  const placementCard = el('div', { className: 'card hidden', id: 'sfx-placement-card' },
    el('div', { className: 'card-header' }, 'ADD CLIP'),
    el('div', { className: 'form-group' },
      el('label', {}, 'Clip'),
      el('select', { id: 'sfx-clip-select' },
        el('option', { value: '' }, '-- loading clips --'),
      ),
    ),
    el('div', { className: 'form-group' },
      el('label', {}, 'Start time (ms)'),
      el('input', { type: 'number', id: 'sfx-clip-start', value: '0', min: '0', step: '100' }),
    ),
    el('div', { className: 'form-group' },
      el('label', {}, 'Volume'),
      el('div', { className: 'slider-row' },
        el('input', { type: 'range', id: 'sfx-clip-volume', min: '0', max: '200', value: '100', step: '5' }),
        el('span', { className: 'slider-value', id: 'sfx-clip-volume-val' }, '100%'),
      ),
    ),
    el('div', { className: 'form-group' },
      el('label', {}, 'Fade in (ms)'),
      el('input', { type: 'number', id: 'sfx-clip-fade-in', value: '0', min: '0', step: '50' }),
    ),
    el('div', { className: 'form-group' },
      el('label', {}, 'Fade out (ms)'),
      el('input', { type: 'number', id: 'sfx-clip-fade-out', value: '0', min: '0', step: '50' }),
    ),
    el('div', { className: 'form-group' },
      el('label', {}, 'Fade curve'),
      el('select', { id: 'sfx-clip-fade-curve' },
        el('option', { value: 'linear' }, 'Linear'),
        el('option', { value: 'cosine' }, 'Cosine'),
      ),
    ),
    el('button', { className: 'btn btn-primary', id: 'sfx-add-clip-btn', style: { marginTop: '4px' } }, 'Add Clip'),
  );

  left.append(createCard, listCard, placementCard);

  // ─── Right: canvas & placements ───
  const right = el('div', { className: 'col-right' });

  const canvasCard = el('div', { className: 'card hidden', id: 'sfx-canvas-card' },
    el('div', { className: 'stem-card-header' },
      el('span', { className: 'stem-label', id: 'sfx-canvas-title' }, ''),
      el('div', { className: 'stem-actions' },
        el('button', { className: 'btn btn-sm', id: 'sfx-play-btn' }, '\u25B6 Play'),
        el('button', { className: 'btn btn-sm', id: 'sfx-save-btn' }, '\u2193 Save'),
        el('button', { className: 'btn btn-sm btn-primary', id: 'sfx-send-mix-btn' }, 'Send to Mix'),
      ),
    ),
    el('div', { className: 'stem-waveform', id: 'sfx-canvas-waveform', style: { height: '80px' } }),
    el('div', { className: 'sfx-canvas-info', id: 'sfx-canvas-info' }),
  );

  // Settings row
  const settingsCard = el('div', { className: 'card hidden', id: 'sfx-settings-card' },
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
        el('button', { className: 'btn btn-sm btn-danger', id: 'sfx-delete-btn' }, 'Delete'),
      ),
    ),
  );

  // Placement list
  const placementsCard = el('div', { className: 'card hidden', id: 'sfx-placements-list-card' },
    el('div', { className: 'card-header' }, 'PLACEMENTS'),
    el('div', { id: 'sfx-placements-list' }),
  );

  right.append(canvasCard, settingsCard, placementsCard);
  layout.append(left, right);
  panel.appendChild(layout);

  // ─── Wire events ───
  document.getElementById('sfx-duration').addEventListener('input', (e) => {
    document.getElementById('sfx-duration-val').textContent = `${e.target.value}s`;
  });

  document.getElementById('sfx-clip-volume').addEventListener('input', (e) => {
    document.getElementById('sfx-clip-volume-val').textContent = `${e.target.value}%`;
  });

  document.getElementById('sfx-mode').addEventListener('change', (e) => {
    document.getElementById('sfx-duration-group').classList.toggle('hidden', e.target.value === 'reference');
    document.getElementById('sfx-ref-group').classList.toggle('hidden', e.target.value !== 'reference');
  });

  document.getElementById('sfx-create-btn').addEventListener('click', createSfx);
  document.getElementById('sfx-add-clip-btn').addEventListener('click', addPlacement);
  document.getElementById('sfx-play-btn').addEventListener('click', playSfx);
  document.getElementById('sfx-save-btn').addEventListener('click', saveSfx);
  document.getElementById('sfx-send-mix-btn').addEventListener('click', sendToMix);
  document.getElementById('sfx-delete-btn').addEventListener('click', deleteSfx);
  document.getElementById('sfx-limiter').addEventListener('change', toggleLimiter);

  // Subscribe to events
  appState.on('stemsReady', refreshRefStems);
  appState.on('generateReady', refreshClips);
  appState.on('stemsReady', refreshClips);

  // Initial load
  refreshSfxList();
  refreshClips();
}

// ─── API Actions ──────────────────────────────────────────────────────────

async function createSfx() {
  const name = document.getElementById('sfx-name').value.trim() || 'Untitled SFX';
  const mode = document.getElementById('sfx-mode').value;
  const durationMs = parseInt(document.getElementById('sfx-duration').value) * 1000;
  const refPath = document.getElementById('sfx-ref-stem').value || null;

  if (mode === 'reference' && !refPath) {
    alert('Please select a reference stem');
    return;
  }

  try {
    const result = await api('/sfx/create', {
      method: 'POST',
      body: JSON.stringify({
        name,
        mode,
        duration_ms: durationMs,
        reference_stem_path: refPath,
      }),
    });

    _currentSfxId = result.id;
    await refreshSfxList();
    await loadSfx(result.id);
  } catch (err) {
    alert(`Failed to create SFX: ${err.message}`);
  }
}

async function addPlacement() {
  if (!_currentSfxId) return;

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
  } catch (err) {
    alert(`Failed to update placement: ${err.message}`);
  }
}

async function removePlacement(placementId) {
  if (!_currentSfxId) return;
  try {
    await api(`/sfx/${_currentSfxId}/placements/${placementId}`, {
      method: 'DELETE',
    });
    await loadSfx(_currentSfxId);
  } catch (err) {
    alert(`Failed to remove placement: ${err.message}`);
  }
}

function playSfx() {
  if (!_currentSfxId) return;
  transportLoad(`/api/sfx/${_currentSfxId}/stream`, 'SFX Preview');
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

async function sendToMix() {
  if (!_currentSfxId) return;
  try {
    const result = await api(`/sfx/${_currentSfxId}/send-to-mix`, { method: 'POST' });
    appState.emit('sfxReady', { id: _currentSfxId, track_id: result.track_id });

    const info = document.getElementById('sfx-canvas-info');
    info.textContent = `Sent to Mix as "${result.label}"`;
    info.className = 'sfx-canvas-info sfx-info-success';
    setTimeout(() => { info.textContent = ''; info.className = 'sfx-canvas-info'; }, 3000);
  } catch (err) {
    alert(`Failed to send to Mix: ${err.message}`);
  }
}

async function deleteSfx() {
  if (!_currentSfxId) return;
  if (!confirm('Delete this SFX stem and all its placements?')) return;

  try {
    await api(`/sfx/${_currentSfxId}`, { method: 'DELETE' });
    _currentSfxId = null;
    hideEditor();
    await refreshSfxList();
  } catch (err) {
    alert(`Delete failed: ${err.message}`);
  }
}

async function toggleLimiter() {
  if (!_currentSfxId) return;
  const limiter = document.getElementById('sfx-limiter').checked;
  try {
    await api(`/sfx/${_currentSfxId}`, {
      method: 'PATCH',
      body: JSON.stringify({ apply_limiter: limiter }),
    });
    await loadSfx(_currentSfxId);
  } catch (err) {
    alert(`Failed to update limiter: ${err.message}`);
  }
}

// ─── Data Loading ─────────────────────────────────────────────────────────

async function loadSfx(sfxId) {
  try {
    const data = await api(`/sfx/${sfxId}`);
    _currentSfxId = sfxId;
    showEditor(data);
  } catch (err) {
    alert(`Failed to load SFX: ${err.message}`);
  }
}

async function refreshSfxList() {
  try {
    const data = await api('/sfx');
    const container = document.getElementById('sfx-list');
    clearChildren(container);

    if (!data.sfx_stems || data.sfx_stems.length === 0) {
      container.appendChild(el('span', { className: 'text-dim' }, 'No SFX stems yet'));
      return;
    }

    for (const sfx of data.sfx_stems) {
      const row = el('div', { className: 'sfx-list-item' },
        el('span', { className: 'sfx-list-name' }, sfx.name),
        el('span', { className: 'text-dim', style: { fontSize: '11px' } },
          `${formatTime(sfx.duration_ms / 1000)} | ${sfx.placement_count} clip(s)`,
        ),
        el('button', {
          className: 'btn btn-sm',
          onClick: () => loadSfx(sfx.id),
        }, 'Edit'),
      );
      if (sfx.id === _currentSfxId) row.classList.add('sfx-list-active');
      container.appendChild(row);
    }
  } catch {
    // silent — list will show stale data
  }
}

async function refreshClips() {
  try {
    const data = await api('/sfx/available-clips');
    const select = document.getElementById('sfx-clip-select');
    clearChildren(select);
    select.appendChild(el('option', { value: '' }, '-- select clip --'));

    for (const clip of data.clips || []) {
      select.appendChild(
        el('option', { value: clip.path }, `[${clip.source}] ${clip.name}`),
      );
    }
  } catch {
    // silent
  }
}

function refreshRefStems(stemPaths) {
  const select = document.getElementById('sfx-ref-stem');
  clearChildren(select);
  select.appendChild(el('option', { value: '' }, '-- select stem --'));
  for (const [label, path] of Object.entries(stemPaths || appState.stemPaths || {})) {
    select.appendChild(el('option', { value: path }, label));
  }
}

// ─── UI State ─────────────────────────────────────────────────────────────

let _canvasWs = null;

function showEditor(data) {
  const { manifest, rendered_path, waveform_peaks } = data;

  // Show editor cards
  document.getElementById('sfx-canvas-card').classList.remove('hidden');
  document.getElementById('sfx-settings-card').classList.remove('hidden');
  document.getElementById('sfx-placements-list-card').classList.remove('hidden');
  document.getElementById('sfx-placement-card').classList.remove('hidden');

  // Title
  document.getElementById('sfx-canvas-title').textContent =
    `${manifest.name} (${formatTime(manifest.duration_ms / 1000)})`;

  // Limiter toggle
  document.getElementById('sfx-limiter').checked = manifest.apply_limiter || false;

  // Canvas waveform
  const waveContainer = document.getElementById('sfx-canvas-waveform');
  if (_canvasWs) {
    _canvasWs.destroy();
    _canvasWs = null;
  }

  if (rendered_path) {
    _canvasWs = createWaveform(waveContainer, { height: 80 });
    _canvasWs.load(`/api/sfx/${manifest.id}/stream`);
  }

  // Placements list
  renderPlacements(manifest.placements || []);

  // Highlight in list
  refreshSfxList();
}

function hideEditor() {
  document.getElementById('sfx-canvas-card').classList.add('hidden');
  document.getElementById('sfx-settings-card').classList.add('hidden');
  document.getElementById('sfx-placements-list-card').classList.add('hidden');
  document.getElementById('sfx-placement-card').classList.add('hidden');
  if (_canvasWs) {
    _canvasWs.destroy();
    _canvasWs = null;
  }
}

function renderPlacements(placements) {
  const container = document.getElementById('sfx-placements-list');
  clearChildren(container);

  if (placements.length === 0) {
    container.appendChild(el('span', { className: 'text-dim' }, 'No clips placed yet'));
    return;
  }

  for (const p of placements) {
    const clipName = (p.clip_path || '').split('/').pop();
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
    container.appendChild(row);
  }
}

function buildEditButton(placement) {
  const btn = el('button', { className: 'btn btn-sm' }, 'Edit');
  btn.addEventListener('click', () => {
    // Populate add-clip form with current values for editing
    document.getElementById('sfx-clip-start').value = placement.start_ms;
    document.getElementById('sfx-clip-volume').value = Math.round(placement.volume * 100);
    document.getElementById('sfx-clip-volume-val').textContent = `${Math.round(placement.volume * 100)}%`;
    document.getElementById('sfx-clip-fade-in').value = placement.fade_in_ms;
    document.getElementById('sfx-clip-fade-out').value = placement.fade_out_ms;
    document.getElementById('sfx-clip-fade-curve').value = placement.fade_curve || 'linear';

    // Replace the Add button with an Update button temporarily
    const addBtn = document.getElementById('sfx-add-clip-btn');
    const updateBtn = el('button', {
      className: 'btn btn-primary',
      id: 'sfx-update-clip-btn',
      style: { marginTop: '4px' },
    }, 'Update Clip');

    const cancelBtn = el('button', {
      className: 'btn',
      style: { marginTop: '4px', marginLeft: '8px' },
    }, 'Cancel');

    const wrapper = el('div', { style: { display: 'flex', gap: '8px' } }, updateBtn, cancelBtn);

    addBtn.classList.add('hidden');
    addBtn.parentNode.insertBefore(wrapper, addBtn.nextSibling);

    updateBtn.addEventListener('click', async () => {
      const updates = {
        start_ms: parseInt(document.getElementById('sfx-clip-start').value) || 0,
        volume: parseInt(document.getElementById('sfx-clip-volume').value) / 100,
        fade_in_ms: parseInt(document.getElementById('sfx-clip-fade-in').value) || 0,
        fade_out_ms: parseInt(document.getElementById('sfx-clip-fade-out').value) || 0,
        fade_curve: document.getElementById('sfx-clip-fade-curve').value,
      };
      wrapper.remove();
      addBtn.classList.remove('hidden');
      await updatePlacement(placement.id, updates);
    });

    cancelBtn.addEventListener('click', () => {
      wrapper.remove();
      addBtn.classList.remove('hidden');
    });
  });
  return btn;
}
