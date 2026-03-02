/**
 * Mix tab — multi-track mixer with render.
 */

import { appState, api, pollJob, el, formatTime, saveFileAs } from '../app.js';
import { createWaveform } from './waveform.js';

function clearChildren(elem) {
  while (elem.firstChild) elem.removeChild(elem.firstChild);
}

// ─── Inline audio players (exclusive playback) ───────────────────────────

const _players = [];

function _stopOtherPlayers(except) {
  for (const p of _players) {
    if (p.ws !== except && p.ws.isPlaying()) {
      p.ws.stop();
      p.playBtn.textContent = '\u25B6 Play';
    }
  }
}

/**
 * Build a standard stem-card player.
 * Returns { card, ws }.
 */
function createMixPlayer(label, url, audioPath) {
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

// ─── Init ─────────────────────────────────────────────────────────────────

export function initMix() {
  const panel = document.getElementById('panel-mix');

  // Master player (populated after render)
  const masterSection = el('div', { id: 'mix-master-container', style: { display: 'none', marginBottom: '12px' } });

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
      // Make the uploaded audio available as an align reference in the Synth tab
      if (data.path) {
        appState.emit('fileLoaded', { path: data.path, filename: data.label || file.name });
      }
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

    // Destroy old track players (but keep master)
    for (let i = _players.length - 1; i >= 0; i--) {
      if (_players[i]._isTrack) {
        _players[i].ws.destroy();
        _players.splice(i, 1);
      }
    }

    clearChildren(trackList);

    if (tracks.length === 0) {
      emptyMsg.style.display = '';
      document.getElementById('mix-render').disabled = true;
      return;
    }

    emptyMsg.style.display = 'none';
    document.getElementById('mix-render').disabled = false;

    for (const track of tracks) {
      const container = el('div', { className: 'mix-track-card' });

      // ─── Control row ───
      const enableInput = el('input', { type: 'checkbox' });
      enableInput.checked = track.enabled;
      enableInput.addEventListener('change', () => {
        api('/mix/tracks', {
          method: 'POST',
          body: JSON.stringify({ track_id: track.track_id, enabled: enableInput.checked }),
        });
      });

      const volumeSlider = el('input', {
        type: 'range',
        className: 'volume-slider',
        min: '0',
        max: '1',
        step: '0.05',
        value: String(track.volume),
      });
      volumeSlider.addEventListener('change', () => {
        api('/mix/tracks', {
          method: 'POST',
          body: JSON.stringify({ track_id: track.track_id, volume: parseFloat(volumeSlider.value) }),
        });
      });

      const controlRow = el('div', { className: 'track-row' },
        el('label', { className: 'toggle' },
          enableInput,
          el('span', { className: 'toggle-slider' }),
        ),
        el('span', {
          className: 'track-label',
          style: track.label.startsWith('SFX:') ? { color: '#ffffff', fontWeight: '600' } : {},
        }, track.label),
        volumeSlider,
        el('span', { className: 'badge' }, track.source),
        el('button', {
          className: 'btn btn-sm btn-danger',
          onClick: async () => {
            await fetch(`/api/mix/tracks/${track.track_id}`, { method: 'DELETE' });
            refreshTracks();
          },
        }, '\u2715'),
      );

      container.appendChild(controlRow);

      // ─── Waveform player for audio tracks ───
      if (track.source === 'audio' && track.path) {
        const url = `/api/audio/stream?path=${encodeURIComponent(track.path)}`;

        const playBtn = el('button', { className: 'btn btn-sm' }, '\u25B6 Play');
        const stopBtn = el('button', { className: 'btn btn-sm' }, '\u25A0 Stop');
        const rewindBtn = el('button', { className: 'btn btn-sm' }, '\u23EA Rewind');
        const timeLabel = el('span', { className: 'stem-time' }, '0:00 / 0:00');

        const playerRow = el('div', { className: 'stem-card-header', style: { padding: '4px 12px 0', borderBottom: 'none' } },
          el('div', { className: 'stem-actions' },
            playBtn, stopBtn, rewindBtn, timeLabel,
          ),
        );

        const waveContainer = el('div', { className: 'stem-waveform', style: { padding: '0 12px 8px' } });
        container.append(playerRow, waveContainer);

        const ws = createWaveform(waveContainer, { height: 40 });
        ws.load(url);

        const player = { ws, playBtn, _isTrack: true };
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
      }

      trackList.appendChild(container);
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

  const container = document.getElementById('mix-master-container');
  container.style.display = '';

  // Destroy previous master player
  for (let i = _players.length - 1; i >= 0; i--) {
    if (_players[i]._isMaster) {
      _players[i].ws.destroy();
      _players.splice(i, 1);
    }
  }
  clearChildren(container);

  const url = `/api/audio/stream?path=${encodeURIComponent(result.mix_path)}`;
  const { card, ws } = createMixPlayer('Master Mix', url, result.mix_path);
  card.classList.add('mix-master-card');
  container.appendChild(card);

  // Tag as master so we can clean up on re-render
  const player = _players[_players.length - 1];
  player._isMaster = true;

  // Auto-play the result
  ws.once('ready', () => ws.play());
}
