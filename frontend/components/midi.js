/**
 * MIDI tab — stem selection, extraction, preview, save.
 */

import { appState, api, pollJob, el } from '../app.js';
import { transportLoad } from './audio-player.js';

function clearChildren(elem) {
  while (elem.firstChild) elem.removeChild(elem.firstChild);
}

export function initMidi() {
  const panel = document.getElementById('panel-midi');
  const layout = el('div', { className: 'two-col' });

  // ─── Left: controls ───
  const left = el('div', { className: 'col-left' });

  const stemSection = el('div', { className: 'form-group' },
    el('label', {}, 'Stems to process'),
    el('div', { className: 'checkbox-group', id: 'midi-stems' },
      el('span', { className: 'text-dim' }, 'Run separation first'),
    ),
  );

  const keyGroup = el('div', { className: 'form-group' },
    el('label', {}, 'Key'),
    el('select', { id: 'midi-key' },
      el('option', { value: 'Any' }, 'Any (auto-detect)'),
      ...['C major','C minor','D major','D minor','E major','E minor',
          'F major','F minor','G major','G minor','A major','A minor',
          'B major','B minor'].map(k => el('option', { value: k }, k)),
    ),
  );

  const bpmGroup = el('div', { className: 'form-group' },
    el('label', {}, 'BPM'),
    el('input', { type: 'number', id: 'midi-bpm', value: '120', min: '20', max: '300' }),
  );

  const tsGroup = el('div', { className: 'form-group' },
    el('label', {}, 'Time Signature'),
    el('select', { id: 'midi-ts' },
      el('option', { value: '4/4' }, '4/4'),
      el('option', { value: '3/4' }, '3/4'),
      el('option', { value: '6/8' }, '6/8'),
      el('option', { value: '2/4' }, '2/4'),
    ),
  );

  const onsetGroup = el('div', { className: 'form-group' },
    el('label', {}, 'Onset threshold'),
    el('div', { className: 'slider-row' },
      el('input', { type: 'range', id: 'midi-onset', min: '0', max: '1', step: '0.05', value: '0.5' }),
      el('span', { className: 'slider-value', id: 'midi-onset-val' }, '0.50'),
    ),
  );

  const frameGroup = el('div', { className: 'form-group' },
    el('label', {}, 'Frame threshold'),
    el('div', { className: 'slider-row' },
      el('input', { type: 'range', id: 'midi-frame', min: '0', max: '1', step: '0.05', value: '0.3' }),
      el('span', { className: 'slider-value', id: 'midi-frame-val' }, '0.30'),
    ),
  );

  const extractBtn = el('button', { className: 'btn btn-primary', id: 'midi-start', disabled: 'true' },
    'Extract MIDI',
  );

  left.append(stemSection, keyGroup, bpmGroup, tsGroup, onsetGroup, frameGroup, extractBtn);

  // ─── Right: results ───
  const right = el('div', { className: 'col-right' });

  const progressCard = el('div', { className: 'card hidden', id: 'midi-progress' },
    el('div', { className: 'progress-container' },
      el('div', { className: 'progress-bar' },
        el('div', { className: 'progress-fill', id: 'midi-progress-fill' }),
      ),
      el('div', { className: 'progress-label' },
        el('span', { id: 'midi-stage' }, ''),
        el('span', { id: 'midi-pct' }, '0%'),
      ),
    ),
  );

  const resultsContainer = el('div', { id: 'midi-results' });

  right.append(progressCard, resultsContainer);
  layout.append(left, right);
  panel.appendChild(layout);

  // ─── Wire events ───
  document.getElementById('midi-onset').addEventListener('input', (e) => {
    document.getElementById('midi-onset-val').textContent = parseFloat(e.target.value).toFixed(2);
  });
  document.getElementById('midi-frame').addEventListener('input', (e) => {
    document.getElementById('midi-frame-val').textContent = parseFloat(e.target.value).toFixed(2);
  });
  document.getElementById('midi-start').addEventListener('click', startExtraction);

  appState.on('stemsReady', (stemPaths) => {
    populateStemCheckboxes(stemPaths);
    document.getElementById('midi-start').disabled = false;
  });
}

function populateStemCheckboxes(stemPaths) {
  const container = document.getElementById('midi-stems');
  clearChildren(container);
  for (const label of Object.keys(stemPaths)) {
    container.appendChild(
      el('label', {},
        el('input', { type: 'checkbox', value: label, checked: 'true' }),
        label,
      ),
    );
  }
}

async function startExtraction() {
  const stemEls = document.querySelectorAll('#midi-stems input[type="checkbox"]:checked');
  const stems = Array.from(stemEls).map(e => e.value);
  if (!stems.length) return;

  const progressCard = document.getElementById('midi-progress');
  const resultsContainer = document.getElementById('midi-results');
  progressCard.classList.remove('hidden');
  clearChildren(resultsContainer);
  document.getElementById('midi-start').disabled = true;

  try {
    const { job_id } = await api('/midi/extract', {
      method: 'POST',
      body: JSON.stringify({
        stems,
        key: document.getElementById('midi-key').value,
        bpm: parseFloat(document.getElementById('midi-bpm').value),
        time_signature: document.getElementById('midi-ts').value,
        onset_threshold: parseFloat(document.getElementById('midi-onset').value),
        frame_threshold: parseFloat(document.getElementById('midi-frame').value),
      }),
    });

    pollJob(job_id, {
      onProgress(progress, stage) {
        document.getElementById('midi-progress-fill').style.width = `${(progress * 100).toFixed(0)}%`;
        document.getElementById('midi-pct').textContent = `${(progress * 100).toFixed(0)}%`;
        document.getElementById('midi-stage').textContent = stage;
      },
      onDone(result) {
        progressCard.classList.add('hidden');
        document.getElementById('midi-start').disabled = false;
        showMidiResults(result);
      },
      onError(msg) {
        progressCard.classList.add('hidden');
        document.getElementById('midi-start').disabled = false;
        resultsContainer.appendChild(
          el('div', { className: 'banner banner-error' }, `MIDI extraction failed: ${msg}`),
        );
      },
    });
  } catch (err) {
    progressCard.classList.add('hidden');
    document.getElementById('midi-start').disabled = false;
    resultsContainer.appendChild(
      el('div', { className: 'banner banner-error' }, `Error: ${err.message}`),
    );
  }
}

function showMidiResults(result) {
  const container = document.getElementById('midi-results');

  appState.midiLabels = result.labels || [];
  appState.emit('midiReady', result);

  // Save merged button
  if (result.has_merged) {
    container.appendChild(
      el('button', {
        className: 'btn',
        onClick: async () => {
          try {
            const res = await api('/midi/save', {
              method: 'POST',
              body: JSON.stringify({ label: 'merged' }),
            });
            alert(`Saved: ${res.path}`);
          } catch (err) {
            alert(`Save failed: ${err.message}`);
          }
        },
      }, 'Save merged MIDI'),
    );
  }

  // Per-stem results
  for (const [label, info] of Object.entries(result.stem_info || {})) {
    const card = el('div', { className: 'stem-card' },
      el('div', { className: 'stem-card-header' },
        el('span', { className: 'stem-label' }, `${label} (${info.note_count} notes)`),
        el('div', { className: 'stem-actions' },
          el('button', {
            className: 'btn btn-sm',
            onClick: () => renderAndPlay(label),
          }, '\u25B6 Preview'),
          el('button', {
            className: 'btn btn-sm',
            onClick: async () => {
              try {
                const res = await api('/midi/save', {
                  method: 'POST',
                  body: JSON.stringify({ label }),
                });
                alert(`Saved: ${res.path}`);
              } catch (err) {
                alert(`Save failed: ${err.message}`);
              }
            },
          }, 'Save'),
        ),
      ),
    );
    container.appendChild(card);
  }
}

async function renderAndPlay(label) {
  try {
    const res = await api('/midi/render', {
      method: 'POST',
      body: JSON.stringify({ stem_label: label }),
    });
    transportLoad(`/api/audio/stream?path=${encodeURIComponent(res.audio_path)}`, `MIDI: ${label}`);
  } catch (err) {
    alert(`Render failed: ${err.message}`);
  }
}
