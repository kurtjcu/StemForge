/**
 * Mix tab — multi-track mixer with render.
 */

import { appState, api, pollJob, el, formatTime, saveFileAs } from '../app.js';
import { createWaveform } from './waveform.js';
import { transportLoad, transportStop } from './audio-player.js';

/** GM program names — loaded from backend on init. */
let _gmPrograms = [];
let _gmDefaults = {};
let _gmDrumStems = {};

function clearChildren(elem) {
  while (elem.firstChild) elem.removeChild(elem.firstChild);
}

// ─── Inline audio players (exclusive playback) ───────────────────────────

const _players = [];
let _playingAll = false;   // when true, suppress exclusive playback

function _stopOtherPlayers(except) {
  if (_playingAll) return;   // multi-track preview active — don't stop siblings
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
      transportLoad(url, label, false, 'Mix');
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
  ws.on('finish', () => { playBtn.textContent = '\u25B6 Play'; transportStop(); });

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

  // Preview + Render buttons
  const previewBtn = el('button', {
    className: 'btn',
    id: 'mix-preview',
    style: { marginTop: '16px' },
    disabled: 'true',
  }, '\u25B6 Preview');

  const previewStopBtn = el('button', {
    className: 'btn',
    id: 'mix-preview-stop',
    style: { marginTop: '16px', display: 'none' },
  }, '\u25A0 Stop');

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

  const actionRow = el('div', {
    style: { display: 'flex', gap: '8px', alignItems: 'center' },
  }, previewBtn, previewStopBtn, renderBtn);

  panel.append(masterSection, trackHeader, noTracksMsg, trackList, progressCard, actionRow);

  // ─── Wire events ───
  document.getElementById('mix-render').addEventListener('click', startRender);
  document.getElementById('mix-preview').addEventListener('click', togglePreview);
  document.getElementById('mix-preview-stop').addEventListener('click', stopPreview);

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
  appState.on('transformReady', () => refreshTracks());
  appState.on('enhanceReady', () => refreshTracks());
  appState.on('midiInstrumentChanged', () => refreshTracks());

  // Load GM programs for instrument selectors
  loadGmPrograms();
}

async function loadGmPrograms() {
  try {
    const data = await api('/midi/gm-programs');
    _gmPrograms = data.programs || [];
    _gmDefaults = data.defaults || {};
    _gmDrumStems = data.drum_stems || {};
  } catch { /* fail silently */ }
}

async function refreshTracks() {
  try {
    // Stop any active preview since track players are being rebuilt
    if (_playingAll) stopPreview();

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
      document.getElementById('mix-preview').disabled = true;
      return;
    }

    emptyMsg.style.display = 'none';
    document.getElementById('mix-render').disabled = false;
    document.getElementById('mix-preview').disabled = false;

    for (const track of tracks) {
      const container = el('div', { className: 'mix-track-card' });

      // ─── Control row ───
      const enableInput = el('input', { type: 'checkbox' });
      enableInput.checked = track.enabled;
      enableInput.addEventListener('change', async () => {
        try {
          await api('/mix/tracks', {
            method: 'POST',
            body: JSON.stringify({ track_id: track.track_id, enabled: enableInput.checked }),
          });
        } catch (err) {
          console.error('Failed to save track state:', err);
        }
      });

      const volumeSlider = el('input', {
        type: 'range',
        className: 'volume-slider',
        min: '0',
        max: '1',
        step: '0.05',
        value: String(track.volume),
      });
      const volumeLabel = el('span', { className: 'volume-label' }, `${Math.round(track.volume * 100)}%`);
      volumeSlider.addEventListener('input', () => {
        const vol = parseFloat(volumeSlider.value);
        volumeLabel.textContent = `${Math.round(vol * 100)}%`;
        // Update live playback volume on the track's wavesurfer player
        const trackPlayer = _players.find(p => p._trackId === track.track_id);
        if (trackPlayer) trackPlayer.ws.setVolume(vol);
      });
      volumeSlider.addEventListener('change', async () => {
        try {
          await api('/mix/tracks', {
            method: 'POST',
            body: JSON.stringify({ track_id: track.track_id, volume: parseFloat(volumeSlider.value) }),
          });
        } catch (err) {
          console.error('Failed to save volume:', err);
          volumeLabel.textContent = 'Error';
        }
      });

      // Determine badge class based on source type
      const badgeClass = track.source === 'midi' ? 'badge badge-midi'
        : track.source === 'synth' ? 'badge badge-synth'
        : 'badge badge-audio';

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
        volumeLabel,
        el('span', { className: badgeClass }, track.source),
        el('button', {
          className: 'btn btn-sm btn-danger',
          onClick: async () => {
            await fetch(`/api/mix/tracks/${track.track_id}`, { method: 'DELETE' });
            refreshTracks();
          },
        }, '\u2715'),
      );

      container.appendChild(controlRow);

      // ─── Instrument selector for MIDI tracks ───
      if (track.source === 'midi' && _gmPrograms.length) {
        const instrumentSelect = el('select', { className: 'midi-instrument-select' });
        instrumentSelect.appendChild(el('option', { value: 'drum' }, 'Drum Kit'));
        for (let i = 0; i < _gmPrograms.length; i++) {
          instrumentSelect.appendChild(el('option', { value: String(i) }, `${i}: ${_gmPrograms[i]}`));
        }
        // Set current value from track state
        if (track.is_drum) {
          instrumentSelect.value = 'drum';
        } else {
          instrumentSelect.value = String(track.program);
        }

        const instrumentRow = el('div', { className: 'midi-instrument-row', style: { padding: '4px 12px' } },
          el('label', { className: 'text-dim' }, 'Instrument:'),
          instrumentSelect,
        );
        container.appendChild(instrumentRow);

        instrumentSelect.addEventListener('change', async () => {
          const val = instrumentSelect.value;
          const isDrum = val === 'drum';
          const program = isDrum ? 0 : parseInt(val, 10);
          try {
            await api('/mix/tracks', {
              method: 'POST',
              body: JSON.stringify({ track_id: track.track_id, program, is_drum: isDrum }),
            });
          } catch (err) { console.error('Failed to update instrument:', err); }
        });
      }

      // ─── Waveform player for audio/synth tracks ───
      if ((track.source === 'audio' || track.source === 'synth') && track.path) {
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

        const player = { ws, playBtn, enableInput, _isTrack: true, _trackId: track.track_id, _trackLabel: track.label };
        _players.push(player);

        // Set initial playback volume from track state
        ws.setVolume(track.volume);

        playBtn.addEventListener('click', () => {
          if (ws.isPlaying()) {
            ws.pause();
            playBtn.textContent = '\u25B6 Play';
          } else {
            _stopOtherPlayers(ws);
            ws.play();
            playBtn.textContent = '\u23F8 Pause';
            transportLoad(url, track.label, false, 'Mix');
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
        ws.on('finish', () => { playBtn.textContent = '\u25B6 Play'; transportStop(); });
      }

      // ─── Waveform player for MIDI tracks (render on demand) ───
      if (track.source === 'midi') {
        const playBtn = el('button', { className: 'btn btn-sm' }, '\u25B6 Play');
        const stopBtn = el('button', { className: 'btn btn-sm' }, '\u25A0 Stop');
        const rewindBtn = el('button', { className: 'btn btn-sm' }, '\u23EA Rewind');
        const timeLabel = el('span', { className: 'stem-time' }, '0:00 / 0:00');

        const playerRow = el('div', { className: 'stem-card-header', style: { padding: '4px 12px 0', borderBottom: 'none' } },
          el('div', { className: 'stem-actions' },
            playBtn, stopBtn, rewindBtn, timeLabel,
          ),
        );

        const waveContainer = el('div', { className: 'stem-waveform midi-waveform', style: { padding: '0 12px 8px' } });
        const renderHint = el('div', { className: 'midi-render-hint text-dim' }, 'Press Play to render preview');
        container.append(playerRow, waveContainer, renderHint);

        let ws = null;
        let renderedUrl = null;

        const player = { ws: null, playBtn, enableInput, _isTrack: true, _trackId: track.track_id, _trackLabel: track.label };
        _players.push(player);

        function ensureWaveform() {
          if (ws) return;
          ws = createWaveform(waveContainer, { height: 40, color: 'midi' });
          player.ws = ws;
          ws.setVolume(track.volume);

          ws.on('timeupdate', (time) => {
            const dur = ws.getDuration();
            timeLabel.textContent = `${formatTime(time)} / ${formatTime(dur)}`;
          });
          ws.on('finish', () => { playBtn.textContent = '\u25B6 Play'; });
          ws.on('error', () => {
            playBtn.textContent = '\u25B6 Play';
            playBtn.disabled = false;
          });
        }

        async function renderMidiTrack(autoplay) {
          playBtn.disabled = true;
          playBtn.textContent = 'Rendering...';
          try {
            const resp = await fetch(`/api/mix/render-track/${track.track_id}`, { method: 'POST' });
            if (!resp.ok) {
              const body = await resp.json().catch(() => ({}));
              throw new Error(body.detail || `HTTP ${resp.status}`);
            }
            const res = await resp.json();
            renderedUrl = `/api/audio/stream?path=${encodeURIComponent(res.audio_path)}`;
            renderHint.classList.add('hidden');

            ensureWaveform();
            ws.load(renderedUrl);

            if (autoplay) {
              ws.once('ready', () => {
                playBtn.disabled = false;
                _stopOtherPlayers(ws);
                ws.play();
                playBtn.textContent = '\u23F8 Pause';
                transportLoad(renderedUrl, track.label, false, 'Mix');
              });
            } else {
              ws.once('ready', () => {
                playBtn.disabled = false;
                playBtn.textContent = '\u25B6 Play';
              });
            }
          } catch (err) {
            console.error('MIDI render failed:', err);
            playBtn.textContent = '\u25B6 Play';
            playBtn.disabled = false;
          }
        }

        playBtn.addEventListener('click', () => {
          if (!renderedUrl) {
            renderMidiTrack(true);
            return;
          }
          if (ws && ws.isPlaying()) {
            ws.pause();
            playBtn.textContent = '\u25B6 Play';
          } else if (ws) {
            _stopOtherPlayers(ws);
            ws.play();
            playBtn.textContent = '\u23F8 Pause';
            transportLoad(renderedUrl, track.label, false, 'Mix');
          }
        });

        stopBtn.addEventListener('click', () => {
          if (ws) { ws.stop(); transportStop(); playBtn.textContent = '\u25B6 Play'; }
        });

        rewindBtn.addEventListener('click', () => {
          if (ws) ws.setTime(0);
        });
      }

      trackList.appendChild(container);
    }
  } catch { /* ignore */ }
}

// ─── Multi-track preview ──────────────────────────────────────────────────

function _getEnabledTrackPlayers() {
  return _players.filter(p => p._isTrack && p.enableInput && p.enableInput.checked);
}

function togglePreview() {
  const btn = document.getElementById('mix-preview');
  const stopBtn = document.getElementById('mix-preview-stop');

  if (_playingAll) {
    // Pause all
    _playingAll = false;
    for (const p of _getEnabledTrackPlayers()) {
      if (p.ws.isPlaying()) {
        p.ws.pause();
        p.playBtn.textContent = '\u25B6 Play';
      }
    }
    btn.textContent = '\u25B6 Preview';
    stopBtn.style.display = 'none';
    return;
  }

  const enabled = _getEnabledTrackPlayers();
  if (enabled.length === 0) { alert('No enabled audio tracks to preview'); return; }

  // Stop any solo players first
  for (const p of _players) {
    if (p.ws.isPlaying()) {
      p.ws.stop();
      p.playBtn.textContent = '\u25B6 Play';
    }
  }

  _playingAll = true;
  btn.textContent = '\u23F8 Pause';
  stopBtn.style.display = '';

  // Sync all enabled track players to start from the beginning
  const trackNames = [];
  for (const p of enabled) {
    p.ws.setTime(0);
    p.ws.play();
    p.playBtn.textContent = '\u23F8 Pause';
    if (p._trackLabel) trackNames.push(p._trackLabel);
  }
  transportLoad('', trackNames.length ? `${trackNames.length} tracks` : 'Mix Preview', false, 'Mix \u203A Preview');

  // When the longest track finishes, end the preview
  let finishCount = 0;
  for (const p of enabled) {
    p.ws.once('finish', () => {
      p.playBtn.textContent = '\u25B6 Play';
      finishCount++;
      if (finishCount >= enabled.length) stopPreview();
    });
  }
}

function stopPreview() {
  _playingAll = false;
  for (const p of _players) {
    if (p._isTrack && p.ws.isPlaying()) {
      p.ws.stop();
      p.playBtn.textContent = '\u25B6 Play';
    }
  }
  transportStop();
  const btn = document.getElementById('mix-preview');
  const stopBtn = document.getElementById('mix-preview-stop');
  btn.textContent = '\u25B6 Preview';
  stopBtn.style.display = 'none';
}

// ─── Render ───────────────────────────────────────────────────────────────

async function startRender() {
  if (_playingAll) stopPreview();

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

  // Auto-play the result and load into transport
  ws.once('ready', () => {
    ws.play();
    transportLoad(url, 'Master Mix', false, 'Mix');
  });
}
