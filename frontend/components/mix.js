/**
 * Mix tab — multi-track mixer with render.
 */

import { appState, api, pollJob, el, formatTime } from '../app.js';
import { createWaveform } from './waveform.js';
import { transportLoad } from './audio-player.js';

function clearChildren(elem) {
  while (elem.firstChild) elem.removeChild(elem.firstChild);
}

export function initMix() {
  const panel = document.getElementById('panel-mix');

  // Master waveform
  const masterSection = el('div', { className: 'card', id: 'mix-master-card', style: { display: 'none' } },
    el('div', { className: 'card-header' }, 'MASTER'),
    el('div', { className: 'stem-waveform', id: 'mix-master-waveform' }),
  );

  // Track list
  const trackHeader = el('div', {
    style: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' },
  },
    el('span', { className: 'section-title' }, 'Tracks'),
    el('div', { style: { display: 'flex', gap: '6px' } },
      el('button', { className: 'btn btn-sm', id: 'mix-add-audio' }, '+ Audio'),
      el('button', { className: 'btn btn-sm', id: 'mix-add-midi' }, '+ MIDI'),
    ),
  );

  const trackList = el('div', { id: 'mix-tracks', style: { display: 'flex', flexDirection: 'column', gap: '8px' } });

  const noTracksMsg = el('div', {
    id: 'mix-empty',
    className: 'text-dim',
    style: { padding: '20px', textAlign: 'center' },
  }, 'No tracks yet. Run separation or add files manually.');

  // Render button
  const renderBtn = el('button', {
    className: 'btn btn-primary',
    id: 'mix-render',
    style: { marginTop: '16px' },
    disabled: 'true',
  }, 'Render Mix');

  const progressCard = el('div', { className: 'card hidden', id: 'mix-progress' },
    el('div', { className: 'progress-container' },
      el('div', { className: 'progress-bar' },
        el('div', { className: 'progress-fill', id: 'mix-progress-fill' }),
      ),
      el('div', { className: 'progress-label' },
        el('span', { id: 'mix-stage' }, ''),
        el('span', { id: 'mix-pct' }, '0%'),
      ),
    ),
  );

  panel.append(masterSection, trackHeader, noTracksMsg, trackList, progressCard, renderBtn);

  // ─── Wire events ───
  document.getElementById('mix-render').addEventListener('click', startRender);

  // Add audio/midi file inputs (hidden)
  const audioInput = el('input', { type: 'file', accept: '.wav,.flac,.mp3,.ogg', style: { display: 'none' }, id: 'mix-audio-input' });
  const midiInput = el('input', { type: 'file', accept: '.mid,.midi', style: { display: 'none' }, id: 'mix-midi-input' });
  panel.append(audioInput, midiInput);

  document.getElementById('mix-add-audio').addEventListener('click', () => audioInput.click());
  document.getElementById('mix-add-midi').addEventListener('click', () => midiInput.click());

  audioInput.addEventListener('change', async () => {
    const file = audioInput.files[0];
    if (!file) return;
    try {
      const form = new FormData();
      form.append('file', file);
      const res = await fetch('/api/mix/add-audio', { method: 'POST', body: form });
      const data = await res.json();
      refreshTracks();
    } catch (err) { alert(`Error: ${err.message}`); }
  });

  midiInput.addEventListener('change', async () => {
    const file = midiInput.files[0];
    if (!file) return;
    try {
      const form = new FormData();
      form.append('file', file);
      const res = await fetch('/api/mix/add-midi', { method: 'POST', body: form });
      const data = await res.json();
      refreshTracks();
    } catch (err) { alert(`Error: ${err.message}`); }
  });

  // Auto-refresh tracks when stems/midi/generated are ready
  // (stem tracks are added server-side by the separation job)
  appState.on('stemsReady', () => refreshTracks());

  appState.on('midiReady', () => refreshTracks());
  appState.on('generateReady', () => refreshTracks());
  appState.on('composeReady', () => refreshTracks());
  appState.on('sfxReady', () => refreshTracks());
}

async function refreshTracks() {
  try {
    const data = await api('/mix/tracks');
    const tracks = data.tracks || [];
    const trackList = document.getElementById('mix-tracks');
    const emptyMsg = document.getElementById('mix-empty');

    clearChildren(trackList);

    if (tracks.length === 0) {
      emptyMsg.style.display = '';
      document.getElementById('mix-render').disabled = true;
      return;
    }

    emptyMsg.style.display = 'none';
    document.getElementById('mix-render').disabled = false;

    for (const track of tracks) {
      const row = el('div', { className: 'track-row' },
        // Enable toggle
        el('label', { className: 'toggle' },
          (() => {
            const inp = el('input', { type: 'checkbox' });
            inp.checked = track.enabled;
            inp.addEventListener('change', () => {
              api('/mix/tracks', {
                method: 'POST',
                body: JSON.stringify({ track_id: track.track_id, enabled: inp.checked }),
              });
            });
            return inp;
          })(),
          el('span', { className: 'toggle-slider' }),
        ),
        // Label — SFX tracks in white to distinguish from stems
        el('span', {
          className: 'track-label',
          style: track.label.startsWith('SFX:') ? { color: '#ffffff', fontWeight: '600' } : {},
        }, track.label),
        // Play button
        el('button', {
          className: 'btn btn-sm',
          onClick: () => {
            if (track.path) {
              transportLoad(`/api/audio/stream?path=${encodeURIComponent(track.path)}`, track.label);
            }
          },
        }, '\u25B6'),
        // Volume slider
        (() => {
          const slider = el('input', {
            type: 'range',
            className: 'volume-slider',
            min: '0',
            max: '1',
            step: '0.05',
            value: String(track.volume),
          });
          slider.addEventListener('change', () => {
            api('/mix/tracks', {
              method: 'POST',
              body: JSON.stringify({ track_id: track.track_id, volume: parseFloat(slider.value) }),
            });
          });
          return slider;
        })(),
        // Source badge
        el('span', { className: 'badge' }, track.source),
        // Remove button
        el('button', {
          className: 'btn btn-sm btn-danger',
          onClick: async () => {
            await fetch(`/api/mix/tracks/${track.track_id}`, { method: 'DELETE' });
            refreshTracks();
          },
        }, '\u2715'),
      );
      trackList.appendChild(row);
    }
  } catch { /* ignore */ }
}

async function startRender() {
  const progressCard = document.getElementById('mix-progress');
  progressCard.classList.remove('hidden');
  document.getElementById('mix-render').disabled = true;

  try {
    const { job_id } = await api('/mix/render', { method: 'POST', body: '{}' });

    pollJob(job_id, {
      onProgress(progress, stage) {
        document.getElementById('mix-progress-fill').style.width = `${(progress * 100).toFixed(0)}%`;
        document.getElementById('mix-pct').textContent = `${(progress * 100).toFixed(0)}%`;
        document.getElementById('mix-stage').textContent = stage;
      },
      onDone(result) {
        progressCard.classList.add('hidden');
        document.getElementById('mix-render').disabled = false;
        showMixResult(result);
      },
      onError(msg) {
        progressCard.classList.add('hidden');
        document.getElementById('mix-render').disabled = false;
        document.getElementById('mix-tracks').appendChild(
          el('div', { className: 'banner banner-error' }, `Render failed: ${msg}`),
        );
      },
    });
  } catch (err) {
    progressCard.classList.add('hidden');
    document.getElementById('mix-render').disabled = false;
  }
}

function showMixResult(result) {
  appState.mixPath = result.mix_path;
  appState.emit('mixReady', result.mix_path);

  const masterCard = document.getElementById('mix-master-card');
  masterCard.style.display = '';

  const waveContainer = document.getElementById('mix-master-waveform');
  clearChildren(waveContainer);

  const ws = createWaveform(waveContainer, { height: 50 });
  ws.load(`/api/audio/stream?path=${encodeURIComponent(result.mix_path)}`);

  transportLoad(`/api/audio/stream?path=${encodeURIComponent(result.mix_path)}`, 'Mix');
}
