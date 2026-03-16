/**
 * Separation tab — engine/model selection, stem checkboxes, progress, result cards.
 * Supports single-file mode (default) and batch mode (single stem from multiple files).
 */

import { appState, api, pollJob, el, formatTime, saveFileAs } from '../app.js';
import { createWaveform } from './waveform.js';
import { transportLoad, transportStop } from './audio-player.js';
import { isBatchMode, getBatchFiles } from './loader.js';

function clearChildren(elem) {
  while (elem.firstChild) elem.removeChild(elem.firstChild);
}

const ACE_TRACKS = [
  'vocals', 'backing_vocals', 'drums', 'bass', 'guitar', 'keyboard',
  'strings', 'brass', 'woodwinds', 'synth', 'percussion', 'fx',
];

let models = { demucs: [], roformer: [] };

/** Set of model_ids whose license warning the user has acknowledged this session. */
const acknowledgedModels = new Set();

export function initSeparate() {
  const panel = document.getElementById('panel-separate');

  const layout = el('div', { className: 'two-col' });

  // ─── Left column: controls ───
  const left = el('div', { className: 'col-left' });

  // Engine selector
  const engineGroup = el('div', { className: 'form-group' },
    el('label', {}, 'Engine'),
    el('select', { id: 'sep-engine' },
      el('option', { value: 'demucs' }, 'Demucs'),
      el('option', { value: 'roformer' }, 'BS-Roformer'),
      el('option', { value: 'ace' }, 'ACE-Step'),
    ),
  );

  // ACE info banner (hidden by default)
  const aceBanner = el('div', { className: 'banner banner-info hidden', id: 'sep-ace-banner' },
    'AI-generative separation via AceStep (requires base model). Extracts one stem at a time.',
  );

  // Model selector
  const modelGroup = el('div', { className: 'form-group' },
    el('label', {}, 'Model'),
    el('select', { id: 'sep-model' }),
  );

  // Help me choose
  const helpBtn = el('button', { className: 'btn btn-sm', id: 'sep-help' }, 'Help me choose');
  const helpResult = el('div', { id: 'sep-help-result', className: 'hidden banner banner-info' });

  // Quality selector (Roformer only)
  const qualityGroup = el('div', { className: 'form-group hidden', id: 'sep-quality-group' },
    el('label', {}, 'Quality'),
    el('select', { id: 'sep-quality' },
      el('option', { value: '2' }, 'Balanced (default)'),
      el('option', { value: '4' }, 'High'),
      el('option', { value: '8' }, 'Maximum (slow)'),
    ),
  );

  // Stem checkboxes (single mode) / radio buttons (batch mode)
  const stemChecks = el('div', { className: 'form-group' },
    el('label', {}, 'Stems'),
    el('div', { className: 'checkbox-group', id: 'sep-stems' }),
  );

  // Separate button
  const sepBtn = el('button', { className: 'btn btn-primary', id: 'sep-start', disabled: 'true' },
    'Separate',
  );

  left.append(engineGroup, aceBanner, modelGroup, helpBtn, helpResult, qualityGroup, stemChecks, sepBtn);

  // ─── Right column: results ───
  const right = el('div', { className: 'col-right' });

  const progressCard = el('div', { className: 'card hidden', id: 'sep-progress' },
    el('div', { className: 'progress-container' },
      el('div', { className: 'progress-bar' },
        el('div', { className: 'progress-fill', id: 'sep-progress-fill' }),
      ),
      el('div', { className: 'progress-label' },
        el('span', { id: 'sep-stage' }, ''),
        el('span', { id: 'sep-pct' }, '0%'),
      ),
    ),
  );

  const resultsContainer = el('div', { id: 'sep-results' });

  right.append(progressCard, resultsContainer);
  layout.append(left, right);
  panel.appendChild(layout);

  // ─── Wire events ───
  loadModels();

  document.getElementById('sep-engine').addEventListener('change', updateModelOptions);
  document.getElementById('sep-model').addEventListener('change', () => {
    updateStemControls();
    checkLicenseWarning();
  });
  document.getElementById('sep-help').addEventListener('click', runRecommend);
  document.getElementById('sep-start').addEventListener('click', () => {
    if (isBatchMode()) startBatchSeparation();
    else startSeparation();
  });

  appState.on('fileLoaded', () => {
    document.getElementById('sep-start').disabled = false;
  });

  appState.on('batchFilesLoaded', () => {
    document.getElementById('sep-start').disabled = false;
  });

  appState.on('batchModeChanged', (batch) => {
    updateStemControls();
    const btn = document.getElementById('sep-start');
    if (batch) {
      btn.textContent = 'Separate All';
      btn.disabled = !getBatchFiles().length;
    } else {
      btn.textContent = 'Separate';
      btn.disabled = !appState.audioPath;
    }
    // Clear previous results
    clearChildren(document.getElementById('sep-results'));
  });
}

async function loadModels() {
  try {
    const data = await api('/models');
    models.demucs = data.demucs || [];
    models.roformer = data.roformer || [];
    updateModelOptions();
  } catch { /* silently fail */ }
}

function updateModelOptions() {
  const engine = document.getElementById('sep-engine').value;
  const select = document.getElementById('sep-model');
  const modelGroup = select.closest('.form-group');
  const helpBtn = document.getElementById('sep-help');
  const aceBanner = document.getElementById('sep-ace-banner');
  const qualityGroup = document.getElementById('sep-quality-group');
  clearChildren(select);

  if (engine === 'ace') {
    // ACE mode: hide model selector, help button, and quality selector; show info banner
    select.appendChild(el('option', { value: 'acestep-extract' }, 'ACE-Step Extract'));
    if (modelGroup) modelGroup.classList.add('hidden');
    if (helpBtn) helpBtn.classList.add('hidden');
    if (aceBanner) aceBanner.classList.remove('hidden');
    if (qualityGroup) qualityGroup.classList.add('hidden');
  } else {
    if (modelGroup) modelGroup.classList.remove('hidden');
    if (helpBtn) helpBtn.classList.remove('hidden');
    if (aceBanner) aceBanner.classList.add('hidden');
    if (qualityGroup) {
      if (engine === 'roformer') qualityGroup.classList.remove('hidden');
      else qualityGroup.classList.add('hidden');
    }
    const list = models[engine] || [];
    for (const m of list) {
      select.appendChild(el('option', { value: m.model_id }, m.display_name));
    }
  }

  updateStemControls();
  checkLicenseWarning();
}

/** Update stem checkboxes (single mode) or radio buttons (batch mode). */
function updateStemControls() {
  const engine = document.getElementById('sep-engine').value;
  const modelId = document.getElementById('sep-model').value;
  const container = document.getElementById('sep-stems');
  clearChildren(container);

  if (engine === 'ace') {
    // ACE mode: radio buttons (single-select)
    for (const stem of ACE_TRACKS) {
      const id = `sep-stem-${stem}`;
      const label = el('label', {},
        el('input', { type: 'radio', name: 'ace-stem', id, value: stem }),
        stem.replace('_', ' '),
      );
      container.appendChild(label);
    }
    // Select vocals by default
    const defaultRadio = document.getElementById('sep-stem-vocals');
    if (defaultRadio) defaultRadio.checked = true;
    return;
  }

  const list = models[engine] || [];
  const model = list.find(m => m.model_id === modelId);
  const stems = model?.available_stems || ['vocals', 'drums', 'bass', 'other'];

  if (isBatchMode()) {
    // Radio buttons — pick ONE stem
    for (const stem of stems) {
      const id = `sep-stem-${stem}`;
      const label = el('label', {},
        el('input', { type: 'radio', name: 'sep-stem-radio', id, value: stem }),
        stem,
      );
      container.appendChild(label);
    }
    // Auto-select first
    const first = container.querySelector('input[type="radio"]');
    if (first) first.checked = true;
  } else {
    // Checkboxes — select multiple stems
    for (const stem of stems) {
      const id = `sep-stem-${stem}`;
      const label = el('label', {},
        el('input', { type: 'checkbox', id, checked: 'true', value: stem }),
        stem,
      );
      container.appendChild(label);
    }
  }
}

/** Show or hide a license warning banner when a model with license concerns is selected. */
function checkLicenseWarning() {
  const engine = document.getElementById('sep-engine').value;
  const modelId = document.getElementById('sep-model').value;
  const list = models[engine] || [];
  const model = list.find(m => m.model_id === modelId);

  // Remove any existing warning banner
  const existing = document.getElementById('sep-license-warning');
  if (existing) existing.remove();

  if (!model?.license_warning) return;

  // Already acknowledged this session — show a brief reminder only
  if (acknowledgedModels.has(modelId)) {
    const reminder = el('div', {
      className: 'banner banner-warn', id: 'sep-license-warning',
    }, 'License warning acknowledged. Using unlicensed model weights at your own risk.');
    // Insert after model selector
    const modelGroup = document.getElementById('sep-model').closest('.form-group');
    modelGroup.after(reminder);
    return;
  }

  // Show full warning with acknowledge button
  const ackBtn = el('button', { className: 'btn btn-sm' }, 'I understand the risk — proceed');
  const banner = el('div', {
    className: 'banner banner-warn', id: 'sep-license-warning',
  },
    el('strong', {}, 'License warning: '),
    model.license_warning,
    el('div', { style: 'margin-top: 0.5rem' }, ackBtn),
  );

  ackBtn.addEventListener('click', () => {
    acknowledgedModels.add(modelId);
    checkLicenseWarning();
  });

  const modelGroup = document.getElementById('sep-model').closest('.form-group');
  modelGroup.after(banner);
}

/** Returns true if the currently selected model requires but lacks acknowledgment. */
function isLicenseBlocked() {
  const engine = document.getElementById('sep-engine').value;
  const modelId = document.getElementById('sep-model').value;
  const list = models[engine] || [];
  const model = list.find(m => m.model_id === modelId);
  return !!(model?.license_warning && !acknowledgedModels.has(modelId));
}

async function runRecommend() {
  const resultEl = document.getElementById('sep-help-result');
  resultEl.classList.remove('hidden');
  resultEl.textContent = 'Analyzing...';

  try {
    const rec = await api('/separate/recommend');
    let text = `${rec.engine}/${rec.model_id} — ${rec.reason} (${rec.confidence})`;
    if (rec.license_warning) {
      text += `\n\u26A0 ${rec.license_warning}`;
    }
    resultEl.textContent = text;

    // Auto-select recommended
    document.getElementById('sep-engine').value = rec.engine;
    updateModelOptions();
    document.getElementById('sep-model').value = rec.model_id;
    updateStemControls();
    checkLicenseWarning();
  } catch (err) {
    resultEl.textContent = `Error: ${err.message}`;
    resultEl.className = 'banner banner-error';
  }
}

// ─── Single-file separation ──────────────────────────────────────────────

async function startSeparation() {
  const engine = document.getElementById('sep-engine').value;

  if (engine === 'ace') {
    return startAceExtraction();
  }

  if (isLicenseBlocked()) return;

  const modelId = document.getElementById('sep-model').value;

  // Get checked stems
  const stemEls = document.querySelectorAll('#sep-stems input[type="checkbox"]:checked');
  const stems = Array.from(stemEls).map(el => el.value);

  const progressCard = document.getElementById('sep-progress');
  const resultsContainer = document.getElementById('sep-results');
  progressCard.classList.remove('hidden');
  clearChildren(resultsContainer);

  document.getElementById('sep-start').disabled = true;

  try {
    const numOverlap = engine === 'roformer'
      ? parseInt(document.getElementById('sep-quality').value, 10)
      : undefined;

    const { job_id } = await api('/separate', {
      method: 'POST',
      body: JSON.stringify({ engine, model_id: modelId, stems, num_overlap: numOverlap }),
    });

    pollJob(job_id, {
      onProgress(progress, stage) {
        document.getElementById('sep-progress-fill').style.width = `${(progress * 100).toFixed(0)}%`;
        document.getElementById('sep-pct').textContent = `${(progress * 100).toFixed(0)}%`;
        document.getElementById('sep-stage').textContent = stage;
      },
      onDone(result) {
        progressCard.classList.add('hidden');
        document.getElementById('sep-start').disabled = false;
        showStemResults(result.stem_paths);
      },
      onError(msg) {
        progressCard.classList.add('hidden');
        document.getElementById('sep-start').disabled = false;
        resultsContainer.appendChild(
          el('div', { className: 'banner banner-error' }, `Separation failed: ${msg}`),
        );
      },
    });
  } catch (err) {
    progressCard.classList.add('hidden');
    document.getElementById('sep-start').disabled = false;
    resultsContainer.appendChild(
      el('div', { className: 'banner banner-error' }, `Error: ${err.message}`),
    );
  }
}

async function startAceExtraction() {
  const selectedRadio = document.querySelector('#sep-stems input[type="radio"]:checked');
  if (!selectedRadio) return;
  const trackName = selectedRadio.value;

  const progressCard = document.getElementById('sep-progress');
  const resultsContainer = document.getElementById('sep-results');
  progressCard.classList.remove('hidden');
  clearChildren(resultsContainer);
  document.getElementById('sep-start').disabled = true;
  document.getElementById('sep-progress-fill').style.width = '0%';
  document.getElementById('sep-pct').textContent = '0%';
  document.getElementById('sep-stage').textContent = 'Checking AceStep...';

  try {
    // Check AceStep status
    const health = await api('/compose/health');
    if (health.acestep_status === 'disabled') {
      throw new Error('AceStep is disabled (start without --no-acestep)');
    }
    if (health.acestep_status === 'crashed') {
      throw new Error('AceStep crashed - check terminal');
    }
    if (health.acestep_status !== 'running') {
      // Try to start AceStep
      document.getElementById('sep-stage').textContent = 'Starting AceStep...';
      await fetch('/api/compose/start', { method: 'POST' });
      // Poll until running
      while (true) {
        await new Promise(r => setTimeout(r, 10000));
        const h = await api('/compose/health');
        if (h.acestep_status === 'running') break;
        if (h.acestep_status === 'crashed') throw new Error('AceStep crashed during startup');
        if (h.acestep_status === 'disabled') throw new Error('AceStep is disabled');
      }
    }

    document.getElementById('sep-stage').textContent = 'Uploading audio to AceStep...';

    // Upload the session audio to AceStep's temp dir
    let srcAudioPath = appState.audioPath || appState.audioInfo?.path;
    if (!srcAudioPath) throw new Error('No audio loaded. Upload a file first.');

    // Upload via compose upload endpoint
    const audioBlob = await fetch(`/api/audio/stream?path=${encodeURIComponent(srcAudioPath)}`).then(r => r.blob());
    const form = new FormData();
    const filename = appState.audioInfo?.filename || 'audio.wav';
    form.append('file', new File([audioBlob], filename, { type: audioBlob.type || 'audio/wav' }));
    const uploadRes = await fetch('/api/compose/upload-audio', { method: 'POST', body: form });
    if (!uploadRes.ok) throw new Error('Failed to upload audio to AceStep');
    const uploadData = await uploadRes.json();

    document.getElementById('sep-stage').textContent = `Extracting ${trackName.replace('_', ' ')}...`;
    document.getElementById('sep-progress-fill').style.width = '10%';
    document.getElementById('sep-pct').textContent = '10%';

    // Build extract payload - duration from session audio
    const duration = appState.audioInfo?.duration || 30;
    const payload = {
      style: '',
      lyrics: '',
      duration,
      task_type: 'extract',
      src_audio_path: uploadData.path,
      track_name: trackName,
      gen_model: 'base',
      lm_model: 'none',
      batch_size: 1,
    };

    const genRes = await fetch('/api/compose/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!genRes.ok) {
      const err = await genRes.json().catch(() => ({ detail: genRes.statusText }));
      throw new Error(err.detail || genRes.statusText);
    }
    const { task_id: taskId } = await genRes.json();

    document.getElementById('sep-progress-fill').style.width = '20%';
    document.getElementById('sep-pct').textContent = '20%';

    // Poll compose status
    const pollAce = setInterval(async () => {
      try {
        const res = await fetch(`/api/compose/status/${taskId}`);
        if (!res.ok) throw new Error(res.statusText);
        const data = await res.json();

        if (data.status === 'done') {
          clearInterval(pollAce);
          progressCard.classList.add('hidden');
          document.getElementById('sep-start').disabled = false;

          // Build stem paths from results
          const results = data.results || [];
          const stemPaths = {};
          for (const r of results) {
            const audioUrl = r.audio_url || '';
            const streamUrl = `/api/compose/audio?path=${encodeURIComponent(audioUrl)}`;
            stemPaths[trackName.replace('_', ' ')] = streamUrl;
          }
          showAceStemResults(stemPaths, taskId, results);
        } else if (data.status === 'error') {
          clearInterval(pollAce);
          progressCard.classList.add('hidden');
          document.getElementById('sep-start').disabled = false;
          resultsContainer.appendChild(
            el('div', { className: 'banner banner-error' }, 'ACE extraction failed. Check AceStep logs.'),
          );
        } else {
          // Still processing - update progress
          document.getElementById('sep-progress-fill').style.width = '50%';
          document.getElementById('sep-pct').textContent = '50%';
        }
      } catch (err) {
        clearInterval(pollAce);
        progressCard.classList.add('hidden');
        document.getElementById('sep-start').disabled = false;
        resultsContainer.appendChild(
          el('div', { className: 'banner banner-error' }, `Polling error: ${err.message}`),
        );
      }
    }, 10000);

  } catch (err) {
    progressCard.classList.add('hidden');
    document.getElementById('sep-start').disabled = false;
    resultsContainer.appendChild(
      el('div', { className: 'banner banner-error' }, `ACE Extract error: ${err.message}`),
    );
  }
}

/** Show ACE extraction results as stem cards with streaming from compose audio proxy. */
function showAceStemResults(stemPaths, taskId, results) {
  const container = document.getElementById('sep-results');
  stemPlayers.length = 0;

  // Emit stemsReady with paths that downstream tabs can use
  appState.stemPaths = { ...appState.stemPaths, ...stemPaths };
  appState.emit('stemsReady', appState.stemPaths);

  for (const [label, streamUrl] of Object.entries(stemPaths)) {
    const card = el('div', { className: 'stem-card' });

    // Transport buttons
    const playBtn = el('button', { className: 'btn btn-sm' }, '\u25B6 Play');
    const stopBtn = el('button', { className: 'btn btn-sm' }, '\u25A0 Stop');
    const rewindBtn = el('button', { className: 'btn btn-sm' }, '\u23EA Rewind');
    const timeLabel = el('span', { className: 'stem-time' }, '0:00 / 0:00');

    const saveBtn = el('button', {
      className: 'btn btn-sm',
      onClick: () => {
        const dlUrl = `/api/compose/download/${taskId}/0/audio`;
        saveFileAs(dlUrl, `ace-extract-${label.replace(' ', '_')}.mp3`);
      },
    }, '\u2193 Save');

    const header = el('div', { className: 'stem-card-header' },
      el('span', { className: 'stem-label' }, label),
      el('div', { className: 'stem-actions' },
        playBtn, stopBtn, rewindBtn, timeLabel, saveBtn,
      ),
    );

    const waveContainer = el('div', { className: 'stem-waveform' });
    card.append(header, waveContainer);
    container.appendChild(card);

    // Wavesurfer
    const ws = createWaveform(waveContainer, { height: 50 });
    ws.load(streamUrl);

    stemPlayers.push({ ws, playBtn });

    playBtn.addEventListener('click', () => {
      if (ws.isPlaying()) {
        ws.pause();
        playBtn.textContent = '\u25B6 Play';
      } else {
        stopOtherPlayers(ws);
        ws.play();
        playBtn.textContent = '\u23F8 Pause';
        transportLoad(streamUrl, label, false, 'Separate');
      }
    });

    stopBtn.addEventListener('click', () => {
      ws.stop();
      transportStop();
      playBtn.textContent = '\u25B6 Play';
    });

    rewindBtn.addEventListener('click', () => {
      ws.setTime(0);
    });

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

/** All active stem players — used for exclusive playback. */
const stemPlayers = [];

/** Stop all other stem players except the given one. */
function stopOtherPlayers(except) {
  for (const p of stemPlayers) {
    if (p.ws !== except && p.ws.isPlaying()) {
      p.ws.stop();
      p.playBtn.textContent = '\u25B6 Play';
    }
  }
}

function showStemResults(stemPaths) {
  const container = document.getElementById('sep-results');
  stemPlayers.length = 0;

  appState.stemPaths = stemPaths;
  appState.emit('stemsReady', stemPaths);

  for (const [label, path] of Object.entries(stemPaths)) {
    const card = el('div', { className: 'stem-card' });
    const url = `/api/audio/stream?path=${encodeURIComponent(path)}`;

    // ─── Transport buttons ───
    const playBtn = el('button', { className: 'btn btn-sm' }, '\u25B6 Play');
    const stopBtn = el('button', { className: 'btn btn-sm' }, '\u25A0 Stop');
    const rewindBtn = el('button', { className: 'btn btn-sm' }, '\u23EA Rewind');
    const timeLabel = el('span', { className: 'stem-time' }, '0:00 / 0:00');

    const saveBtn = el('button', {
      className: 'btn btn-sm',
      onClick: () => {
        const name = path.split('/').pop() || `${label}.wav`;
        saveFileAs(`/api/audio/download?path=${encodeURIComponent(path)}`, name);
      },
    }, '\u2193 Save');

    const header = el('div', { className: 'stem-card-header' },
      el('span', { className: 'stem-label' }, label),
      el('div', { className: 'stem-actions' },
        playBtn, stopBtn, rewindBtn, timeLabel, saveBtn,
      ),
    );

    const waveContainer = el('div', { className: 'stem-waveform' });
    card.append(header, waveContainer);
    container.appendChild(card);

    // ─── Wavesurfer (inline player) ───
    const ws = createWaveform(waveContainer, { height: 50 });
    ws.load(url);

    stemPlayers.push({ ws, playBtn });

    // Play / Pause toggle
    playBtn.addEventListener('click', () => {
      if (ws.isPlaying()) {
        ws.pause();
        playBtn.textContent = '\u25B6 Play';
      } else {
        stopOtherPlayers(ws);
        ws.play();
        playBtn.textContent = '\u23F8 Pause';
        // Feed global transport for cross-tab "Now Playing"
        transportLoad(url, label, false, 'Separate \u203A Batch');
      }
    });

    // Stop
    stopBtn.addEventListener('click', () => {
      ws.stop();
      transportStop();
      playBtn.textContent = '\u25B6 Play';
    });

    // Rewind
    rewindBtn.addEventListener('click', () => {
      ws.setTime(0);
    });

    // Time display
    ws.on('timeupdate', (time) => {
      const dur = ws.getDuration();
      timeLabel.textContent = `${formatTime(time)} / ${formatTime(dur)}`;
    });

    // Reset button text when playback finishes
    ws.on('finish', () => {
      playBtn.textContent = '\u25B6 Play';
      transportStop();
    });
  }
}

// ─── Batch separation ────────────────────────────────────────────────────

async function startBatchSeparation() {
  if (isLicenseBlocked()) return;

  const engine = document.getElementById('sep-engine').value;
  const modelId = document.getElementById('sep-model').value;

  // Get selected stem (radio button)
  const stemRadio = document.querySelector('#sep-stems input[type="radio"]:checked');
  if (!stemRadio) return;
  const stem = stemRadio.value;

  const batchFiles = getBatchFiles();
  if (!batchFiles.length) return;

  const progressCard = document.getElementById('sep-progress');
  const resultsContainer = document.getElementById('sep-results');
  progressCard.classList.remove('hidden');
  clearChildren(resultsContainer);

  document.getElementById('sep-start').disabled = true;

  try {
    const numOverlap = engine === 'roformer'
      ? parseInt(document.getElementById('sep-quality').value, 10)
      : undefined;

    const { job_id } = await api('/separate/batch', {
      method: 'POST',
      body: JSON.stringify({
        engine,
        model_id: modelId,
        stem,
        files: batchFiles.map(f => ({ filename: f.filename, path: f.path })),
        num_overlap: numOverlap,
      }),
    });

    pollJob(job_id, {
      onProgress(progress, stage) {
        document.getElementById('sep-progress-fill').style.width = `${(progress * 100).toFixed(0)}%`;
        document.getElementById('sep-pct').textContent = `${(progress * 100).toFixed(0)}%`;
        document.getElementById('sep-stage').textContent = stage;
      },
      onDone(result) {
        progressCard.classList.add('hidden');
        document.getElementById('sep-start').disabled = false;
        showBatchResults(result.results, result.stem);
      },
      onError(msg) {
        progressCard.classList.add('hidden');
        document.getElementById('sep-start').disabled = false;
        resultsContainer.appendChild(
          el('div', { className: 'banner banner-error' }, `Batch separation failed: ${msg}`),
        );
      },
    });
  } catch (err) {
    progressCard.classList.add('hidden');
    document.getElementById('sep-start').disabled = false;
    resultsContainer.appendChild(
      el('div', { className: 'banner banner-error' }, `Error: ${err.message}`),
    );
  }
}

function showBatchResults(results, stem) {
  const container = document.getElementById('sep-results');
  stemPlayers.length = 0;

  const successful = results.filter(r => !r.error);
  const failed = results.filter(r => r.error);

  // ─── Save All button ───
  if (successful.length > 1) {
    const saveAllBtn = el('button', {
      className: 'btn btn-primary batch-save-all',
      onClick: (e) => saveBatchAll(successful, e.currentTarget),
    }, `\u2193 Save All (${successful.length} files)`);
    container.appendChild(saveAllBtn);
  }

  // ─── Error summary ───
  if (failed.length) {
    const errList = failed.map(r => `${r.filename}: ${r.error}`).join('\n');
    container.appendChild(
      el('div', { className: 'banner banner-error' },
        `${failed.length} file${failed.length > 1 ? 's' : ''} failed:\n${errList}`),
    );
  }

  // ─── Result cards ───
  for (const r of successful) {
    const card = el('div', { className: 'stem-card' });
    const url = `/api/audio/stream?path=${encodeURIComponent(r.path)}`;

    const playBtn = el('button', { className: 'btn btn-sm' }, '\u25B6 Play');
    const stopBtn = el('button', { className: 'btn btn-sm' }, '\u25A0 Stop');
    const rewindBtn = el('button', { className: 'btn btn-sm' }, '\u23EA Rewind');
    const timeLabel = el('span', { className: 'stem-time' }, '0:00 / 0:00');

    const saveBtn = el('button', {
      className: 'btn btn-sm',
      onClick: () => saveFileAs(
        `/api/audio/download?path=${encodeURIComponent(r.path)}`,
        r.output_name,
      ),
    }, '\u2193 Save');

    const header = el('div', { className: 'stem-card-header' },
      el('span', { className: 'stem-label' }, r.output_name),
      el('div', { className: 'stem-actions' },
        playBtn, stopBtn, rewindBtn, timeLabel, saveBtn,
      ),
    );

    const waveContainer = el('div', { className: 'stem-waveform' });
    card.append(header, waveContainer);
    container.appendChild(card);

    const ws = createWaveform(waveContainer, { height: 50 });
    ws.load(url);

    stemPlayers.push({ ws, playBtn });

    playBtn.addEventListener('click', () => {
      if (ws.isPlaying()) {
        ws.pause();
        playBtn.textContent = '\u25B6 Play';
      } else {
        stopOtherPlayers(ws);
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
      timeLabel.textContent = `${formatTime(time)} / ${formatTime(ws.getDuration())}`;
    });

    ws.on('finish', () => {
      playBtn.textContent = '\u25B6 Play';
    });
  }
}

async function saveBatchAll(results, btn) {
  const payload = results.map(r => ({ filename: r.output_name, path: r.path }));
  const origText = btn.textContent;
  btn.disabled = true;
  btn.textContent = 'Preparing zip...';

  try {
    const res = await fetch('/api/separate/batch/save-all', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ paths: payload }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);

    const blob = await res.blob();
    if (blob.size === 0) throw new Error('Zip file is empty — files may have been cleaned up');

    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'batch-stems.zip';
    a.style.display = 'none';
    document.body.appendChild(a);
    a.click();
    setTimeout(() => { a.remove(); URL.revokeObjectURL(url); }, 1000);
    btn.textContent = 'Saved!';
    setTimeout(() => { btn.textContent = origText; btn.disabled = false; }, 2000);
  } catch (err) {
    btn.textContent = origText;
    btn.disabled = false;
    alert(`Save All failed: ${err.message}`);
  }
}
