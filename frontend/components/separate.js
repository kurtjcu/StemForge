/**
 * Separation tab — engine/model selection, stem checkboxes, progress, result cards.
 */

import { appState, api, pollJob, el, formatTime, saveFileAs } from '../app.js';
import { createWaveform } from './waveform.js';

function clearChildren(elem) {
  while (elem.firstChild) elem.removeChild(elem.firstChild);
}

const ACE_TRACKS = [
  'vocals', 'backing_vocals', 'drums', 'bass', 'guitar', 'keyboard',
  'strings', 'brass', 'woodwinds', 'synth', 'percussion', 'fx',
];

let models = { demucs: [], roformer: [] };

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

  // Stem checkboxes
  const stemChecks = el('div', { className: 'form-group' },
    el('label', {}, 'Stems'),
    el('div', { className: 'checkbox-group', id: 'sep-stems' }),
  );

  // Separate button
  const sepBtn = el('button', { className: 'btn btn-primary', id: 'sep-start', disabled: 'true' },
    'Separate',
  );

  left.append(engineGroup, aceBanner, modelGroup, helpBtn, helpResult, stemChecks, sepBtn);

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
  document.getElementById('sep-model').addEventListener('change', updateStemCheckboxes);
  document.getElementById('sep-help').addEventListener('click', runRecommend);
  document.getElementById('sep-start').addEventListener('click', startSeparation);

  appState.on('fileLoaded', () => {
    document.getElementById('sep-start').disabled = false;
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
  clearChildren(select);

  if (engine === 'ace') {
    // ACE mode: hide model selector and help button, show info banner
    select.appendChild(el('option', { value: 'acestep-extract' }, 'ACE-Step Extract'));
    if (modelGroup) modelGroup.classList.add('hidden');
    if (helpBtn) helpBtn.classList.add('hidden');
    if (aceBanner) aceBanner.classList.remove('hidden');
  } else {
    if (modelGroup) modelGroup.classList.remove('hidden');
    if (helpBtn) helpBtn.classList.remove('hidden');
    if (aceBanner) aceBanner.classList.add('hidden');
    const list = models[engine] || [];
    for (const m of list) {
      select.appendChild(el('option', { value: m.model_id }, m.display_name));
    }
  }

  updateStemCheckboxes();
}

function updateStemCheckboxes() {
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

  for (const stem of stems) {
    const id = `sep-stem-${stem}`;
    const label = el('label', {},
      el('input', { type: 'checkbox', id, checked: 'true', value: stem }),
      stem,
    );
    container.appendChild(label);
  }
}

async function runRecommend() {
  const resultEl = document.getElementById('sep-help-result');
  resultEl.classList.remove('hidden');
  resultEl.textContent = 'Analyzing...';

  try {
    const rec = await api('/separate/recommend');
    resultEl.textContent = `${rec.engine}/${rec.model_id} — ${rec.reason} (${rec.confidence})`;

    // Auto-select recommended
    document.getElementById('sep-engine').value = rec.engine;
    updateModelOptions();
    document.getElementById('sep-model').value = rec.model_id;
    updateStemCheckboxes();
  } catch (err) {
    resultEl.textContent = `Error: ${err.message}`;
    resultEl.className = 'banner banner-error';
  }
}

async function startSeparation() {
  const engine = document.getElementById('sep-engine').value;

  if (engine === 'ace') {
    return startAceExtraction();
  }

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
    const { job_id } = await api('/separate', {
      method: 'POST',
      body: JSON.stringify({ engine, model_id: modelId, stems }),
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
        await new Promise(r => setTimeout(r, 3000));
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
    }, 2000);

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
      }
    });

    stopBtn.addEventListener('click', () => {
      ws.stop();
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
      }
    });

    // Stop
    stopBtn.addEventListener('click', () => {
      ws.stop();
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
    });
  }
}
