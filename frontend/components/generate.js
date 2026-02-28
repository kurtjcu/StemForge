/**
 * Generate tab — Stable Audio Open text-to-audio generation.
 */

import { appState, api, pollJob, el, formatTime, saveFileAs } from '../app.js';
import { createWaveform } from './waveform.js';
import { transportLoad } from './audio-player.js';

function clearChildren(elem) {
  while (elem.firstChild) elem.removeChild(elem.firstChild);
}

export function initGenerate() {
  const panel = document.getElementById('panel-synth');
  const layout = el('div', { className: 'two-col' });

  // ─── Left: controls ───
  const left = el('div', { className: 'col-left' });

  const promptGroup = el('div', { className: 'form-group' },
    el('label', {}, 'Prompt (required)'),
    el('textarea', { id: 'gen-prompt', rows: '3', placeholder: 'Describe the audio to generate...' }),
  );

  const durationGroup = el('div', { className: 'form-group' },
    el('label', {}, 'Duration (seconds)'),
    el('div', { className: 'slider-row' },
      el('input', { type: 'range', id: 'gen-duration', min: '5', max: '600', value: '30', step: '5' }),
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

  left.append(promptGroup, durationGroup, stepsGroup, cfgGroup, condSection, vpGroup, genBtn);

  // ─── Right: results ───
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

  right.append(progressCard, resultContainer);
  layout.append(left, right);
  panel.appendChild(layout);

  // ─── Wire events ───
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
    const audioGroup = document.getElementById('gen-cond-audio-group');
    audioGroup.classList.toggle('hidden', e.target.value !== 'audio');
  });
  document.getElementById('gen-start').addEventListener('click', startGeneration);

  // Populate conditioning sources when stems are ready
  appState.on('stemsReady', (stemPaths) => {
    const select = document.getElementById('gen-cond-audio');
    clearChildren(select);
    for (const label of Object.keys(stemPaths)) {
      select.appendChild(el('option', { value: stemPaths[label] }, label));
    }
  });
}

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
      ),
    ),
  );

  const waveContainer = el('div', { className: 'stem-waveform' });
  card.appendChild(waveContainer);
  container.appendChild(card);

  const ws = createWaveform(waveContainer, { height: 50 });
  ws.load(`/api/audio/stream?path=${encodeURIComponent(result.audio_path)}`);
}
