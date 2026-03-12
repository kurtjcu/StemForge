/**
 * MIDI tab — stem selection, extraction, per-stem playback with waveform,
 * GM instrument selector, soundfont picker, preview/save.
 */

import { appState, api, pollJob, el, formatTime, saveFileAs } from '../app.js';
import { createWaveform } from './waveform.js';
import { transportLoad, transportStop } from './audio-player.js';

function clearChildren(elem) {
  while (elem.firstChild) elem.removeChild(elem.firstChild);
}

/** GM program names (populated from backend on init). */
let gmPrograms = [];
let stemDefaults = {};
let drumStems = {};

/** All active MIDI card players — for exclusive playback. */
const midiPlayers = [];

function stopOtherPlayers(except) {
  for (const p of midiPlayers) {
    if (p.ws !== except && p.ws.isPlaying()) {
      p.ws.stop();
      p.playBtn.textContent = '\u25B6 Play';
    }
  }
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

  // ─── SoundFont selector ───
  const sf2Group = el('div', { className: 'form-group' },
    el('label', {}, 'SoundFont'),
    el('div', { className: 'sf2-row' },
      el('input', { type: 'text', id: 'midi-sf2-path', readonly: 'true', placeholder: 'System default' }),
      el('button', { className: 'btn btn-sm', id: 'midi-sf2-browse', title: 'Browse for .sf2 file' }, 'Browse'),
      el('button', { className: 'btn btn-sm', id: 'midi-sf2-reset', title: 'Reset to system default' }, 'Reset'),
    ),
  );

  const extractBtn = el('button', { className: 'btn btn-primary', id: 'midi-start', disabled: 'true' },
    'Extract MIDI',
  );

  left.append(stemSection, keyGroup, bpmGroup, tsGroup, onsetGroup, frameGroup, sf2Group, extractBtn);

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

  // Hidden file input for SF2 browsing
  const sf2Input = el('input', { type: 'file', id: 'midi-sf2-input', accept: '.sf2,.sf3', style: { display: 'none' } });
  panel.appendChild(sf2Input);

  // ─── Wire events ───
  document.getElementById('midi-onset').addEventListener('input', (e) => {
    document.getElementById('midi-onset-val').textContent = parseFloat(e.target.value).toFixed(2);
  });
  document.getElementById('midi-frame').addEventListener('input', (e) => {
    document.getElementById('midi-frame-val').textContent = parseFloat(e.target.value).toFixed(2);
  });
  document.getElementById('midi-start').addEventListener('click', startExtraction);

  // SoundFont controls
  document.getElementById('midi-sf2-browse').addEventListener('click', () => {
    document.getElementById('midi-sf2-input').click();
  });
  document.getElementById('midi-sf2-input').addEventListener('change', handleSf2Browse);
  document.getElementById('midi-sf2-reset').addEventListener('click', resetSoundfont);

  appState.on('stemsReady', (stemPaths) => {
    populateStemCheckboxes(stemPaths);
    document.getElementById('midi-start').disabled = false;
  });

  // Load GM programs and current soundfont on init
  loadGmPrograms();
  loadCurrentSoundfont();
}

async function loadGmPrograms() {
  try {
    const data = await api('/midi/gm-programs');
    gmPrograms = data.programs || [];
    stemDefaults = data.defaults || {};
    drumStems = data.drum_stems || {};
  } catch { /* fail silently, will use defaults */ }
}

async function loadCurrentSoundfont() {
  try {
    const data = await api('/midi/soundfont');
    const input = document.getElementById('midi-sf2-path');
    if (data.path) {
      input.value = data.path;
    } else {
      input.value = '';
      input.placeholder = 'System default';
    }
  } catch { /* ignore */ }
}

async function handleSf2Browse() {
  const fileInput = document.getElementById('midi-sf2-input');
  const file = fileInput.files[0];
  if (!file) return;

  // We need the user to provide a server-side path, not upload the file.
  // The file input gives us the filename; prompt for the full path.
  const path = prompt(
    'Enter the full server path to the SoundFont file:\n\n' +
    `(Selected: ${file.name})`,
    `/usr/share/soundfonts/${file.name}`,
  );
  fileInput.value = '';
  if (!path) return;

  try {
    const res = await api('/midi/soundfont', {
      method: 'POST',
      body: JSON.stringify({ path }),
    });
    document.getElementById('midi-sf2-path').value = res.path;
  } catch (err) {
    alert(`SoundFont error: ${err.message}`);
  }
}

async function resetSoundfont() {
  try {
    const res = await api('/midi/soundfont', {
      method: 'POST',
      body: JSON.stringify({ path: '' }),
    });
    const input = document.getElementById('midi-sf2-path');
    input.value = res.path || '';
    if (!res.path) input.placeholder = 'System default';
  } catch (err) {
    alert(`Reset failed: ${err.message}`);
  }
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
  midiPlayers.length = 0;
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
  midiPlayers.length = 0;

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

  // Per-stem result cards with full playback
  for (const [label, info] of Object.entries(result.stem_info || {})) {
    buildMidiCard(label, info);
  }
}

/**
 * Build a MIDI result card with waveform, playback controls, and instrument selector.
 * Mirrors the stem cards in the Separate tab.
 */
function buildMidiCard(label, info) {
  const container = document.getElementById('midi-results');
  const card = el('div', { className: 'stem-card' });

  // ─── Instrument selector ───
  const defaultProgram = getDefaultProgram(label);
  const defaultIsDrum = isDrumStem(label);

  const instrumentSelect = el('select', { className: 'midi-instrument-select' });
  // Add drum kit option at top
  instrumentSelect.appendChild(el('option', { value: 'drum' }, 'Drum Kit'));
  for (let i = 0; i < gmPrograms.length; i++) {
    instrumentSelect.appendChild(el('option', { value: String(i) }, `${i}: ${gmPrograms[i]}`));
  }
  // Set default
  if (defaultIsDrum) {
    instrumentSelect.value = 'drum';
  } else {
    instrumentSelect.value = String(defaultProgram);
  }

  // ─── Transport buttons ───
  const playBtn = el('button', { className: 'btn btn-sm' }, '\u25B6 Play');
  const stopBtn = el('button', { className: 'btn btn-sm' }, '\u25A0 Stop');
  const rewindBtn = el('button', { className: 'btn btn-sm' }, '\u23EA Rewind');
  const timeLabel = el('span', { className: 'stem-time' }, '0:00 / 0:00');

  const saveBtn = el('button', {
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
  }, '\u2193 Save');

  const header = el('div', { className: 'stem-card-header' },
    el('span', { className: 'stem-label' }, `${label} (${info.note_count} notes)`),
    el('div', { className: 'stem-actions' },
      playBtn, stopBtn, rewindBtn, timeLabel, saveBtn,
    ),
  );

  // Instrument row
  const instrumentRow = el('div', { className: 'midi-instrument-row' },
    el('label', { className: 'text-dim' }, 'Instrument:'),
    instrumentSelect,
  );

  // Waveform container (initially empty — populated on first render)
  const waveContainer = el('div', { className: 'stem-waveform' });
  const renderHint = el('div', { className: 'midi-render-hint text-dim' }, 'Press Play to render audio preview');

  card.append(header, instrumentRow, waveContainer, renderHint);
  container.appendChild(card);

  // State for this card
  let ws = null;
  let renderedUrl = null;
  let lastProgram = instrumentSelect.value;

  /** Render MIDI to audio with current instrument, then load into waveform. */
  async function renderAndLoad(autoplay) {
    const val = instrumentSelect.value;
    const isDrum = val === 'drum';
    const program = isDrum ? 0 : parseInt(val, 10);

    playBtn.disabled = true;
    playBtn.textContent = 'Rendering...';

    try {
      const res = await api('/midi/render', {
        method: 'POST',
        body: JSON.stringify({ stem_label: label, program, is_drum: isDrum }),
      });
      renderedUrl = `/api/audio/stream?path=${encodeURIComponent(res.audio_path)}`;
      lastProgram = val;

      // Hide hint
      renderHint.classList.add('hidden');

      // Create or reload waveform
      if (!ws) {
        ws = createWaveform(waveContainer, { height: 50, color: 'midi' });
        midiPlayers.push({ ws, playBtn });

        ws.on('timeupdate', (time) => {
          const dur = ws.getDuration();
          timeLabel.textContent = `${formatTime(time)} / ${formatTime(dur)}`;
        });

        ws.on('finish', () => {
          playBtn.textContent = '\u25B6 Play';
          transportStop();
        });
      }

      ws.load(renderedUrl);

      if (autoplay) {
        ws.once('ready', () => {
          stopOtherPlayers(ws);
          ws.play();
          playBtn.textContent = '\u23F8 Pause';
          transportLoad(renderedUrl, label, false, 'MIDI');
        });
      } else {
        ws.once('ready', () => {
          playBtn.textContent = '\u25B6 Play';
        });
      }
    } catch (err) {
      alert(`Render failed: ${err.message}`);
      playBtn.textContent = '\u25B6 Play';
    } finally {
      playBtn.disabled = false;
    }
  }

  // Play button — render on first press or instrument change, then toggle play/pause
  playBtn.addEventListener('click', () => {
    const needsRender = !renderedUrl || instrumentSelect.value !== lastProgram;

    if (needsRender) {
      renderAndLoad(true);
      return;
    }

    if (ws && ws.isPlaying()) {
      ws.pause();
      playBtn.textContent = '\u25B6 Play';
    } else if (ws) {
      stopOtherPlayers(ws);
      ws.play();
      playBtn.textContent = '\u23F8 Pause';
      transportLoad(renderedUrl, label, false, 'MIDI');
    }
  });

  // Stop
  stopBtn.addEventListener('click', () => {
    if (ws) {
      ws.stop();
      transportStop();
      playBtn.textContent = '\u25B6 Play';
    }
  });

  // Rewind
  rewindBtn.addEventListener('click', () => {
    if (ws) ws.setTime(0);
  });

  // Re-render when instrument changes and audio was already rendered;
  // also sync the instrument to the corresponding Mix track.
  instrumentSelect.addEventListener('change', () => {
    const val = instrumentSelect.value;
    const isDrum = val === 'drum';
    const program = isDrum ? 0 : parseInt(val, 10);

    // Update Mix track
    const trackId = `midi-${label}`;
    api('/mix/tracks', {
      method: 'POST',
      body: JSON.stringify({ track_id: trackId, program, is_drum: isDrum }),
    }).then(() => {
      appState.emit('midiInstrumentChanged', { label, program, is_drum: isDrum });
    }).catch(() => { /* track may not exist yet */ });

    if (renderedUrl) {
      renderAndLoad(false);
    }
  });
}

/** Get the default GM program for a stem label. */
function getDefaultProgram(label) {
  const lower = label.toLowerCase();
  for (const [key, prog] of Object.entries(stemDefaults)) {
    if (lower.includes(key)) return prog;
  }
  return 0;
}

/** Check if a stem label should default to drum kit. */
function isDrumStem(label) {
  const lower = label.toLowerCase();
  for (const key of Object.keys(drumStems)) {
    if (lower.includes(key.toLowerCase())) return true;
  }
  return false;
}
