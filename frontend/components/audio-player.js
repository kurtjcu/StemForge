/**
 * Global transport bar — sole audio playback engine for the app.
 *
 * Uses wavesurfer.js for waveform rendering + audio playback.
 * Card wavesurfers in other components are visual-only — they never play audio.
 * All playback goes through the transport via transportLoad() and the exported
 * control functions. There is only one audio source at a time.
 */

import { createWaveform } from './waveform.js';
import { formatTime } from '../app.js';

let ws = null;
let currentLabel = '';
let _playBtn = null;

/** Current source tracking — identifies which card owns the transport. */
let _currentSourceId = null;
let _currentUrl = null;

/** Waveform color palettes keyed by source type. */
const COLORS = {
  audio: { waveColor: '#22c55e', progressColor: '#16a34a' },
  midi:  { waveColor: '#a855f7', progressColor: '#7c3aed' },
  sfx:   { waveColor: '#ffffff', progressColor: '#d1d5db' },
};

function _syncPlayBtn() {
  if (!_playBtn || !ws) return;
  const playing = ws.isPlaying();
  _playBtn.textContent = playing ? '\u23F8' : '\u25B6';
  _playBtn.title = playing ? 'Pause' : 'Play';
}

export function initTransport() {
  const container = document.getElementById('transport-waveform');
  ws = createWaveform(container, { height: 36 });
  _playBtn = document.getElementById('transport-play');

  ws.on('timeupdate', (time) => {
    const dur = ws.getDuration();
    document.getElementById('transport-time').textContent =
      `${formatTime(time)} / ${formatTime(dur)}`;
  });

  ws.on('play', _syncPlayBtn);
  ws.on('pause', _syncPlayBtn);
  ws.on('finish', () => {
    _currentSourceId = null;
    ws.seekTo(0);
    _syncPlayBtn();
  });

  _playBtn.addEventListener('click', () => {
    if (ws) ws.playPause();
  });

  document.getElementById('transport-stop').addEventListener('click', () => {
    if (ws) { ws.stop(); _currentSourceId = null; _syncPlayBtn(); }
  });

  document.getElementById('transport-rewind').addEventListener('click', () => {
    if (ws) {
      ws.seekTo(0);
      // If paused, stay paused; if playing, keep playing from start
    }
  });
}

/**
 * Load an audio URL into the transport bar.
 * @param {string} url - audio URL to load
 * @param {string} label - display label
 * @param {boolean} autoplay - start playing once loaded (default: true)
 * @param {string} source - tab/section name shown in "Now Playing (source)"
 * @param {object} [opts] - { color: 'audio'|'midi'|'sfx', sourceId: string }
 */
export function transportLoad(url, label = '', autoplay = true, source = '', opts = {}) {
  if (!ws) return;
  currentLabel = label;
  _currentSourceId = opts.sourceId || null;
  _currentUrl = url;

  const prefix = source ? `Now Playing (${source})` : 'Now Playing';
  document.getElementById('transport-label').textContent = label ? `${prefix}: ${label}` : '';

  // Apply waveform colors based on source type
  const palette = COLORS[opts.color] || COLORS.audio;
  ws.setOptions({ waveColor: palette.waveColor, progressColor: palette.progressColor });

  ws.load(url);
  if (autoplay) {
    ws.once('ready', () => ws.play());
  }
}

export function transportPlayPause() {
  if (ws) ws.playPause();
}

export function transportPlay() {
  if (ws && !ws.isPlaying()) ws.play();
}

export function transportPause() {
  if (ws && ws.isPlaying()) ws.pause();
}

export function transportStop() {
  if (ws) { ws.stop(); _currentSourceId = null; _syncPlayBtn(); }
}

export function transportSeekTo(fraction) {
  if (ws) ws.seekTo(fraction);
}

export function transportGetCurrentTime() {
  return ws ? ws.getCurrentTime() : 0;
}

export function transportGetDuration() {
  return ws ? ws.getDuration() : 0;
}

export function transportIsPlaying() {
  return ws ? ws.isPlaying() : false;
}

/** Get the sourceId of the currently loaded card (null if none). */
export function transportGetSourceId() {
  return _currentSourceId;
}

/** Get the URL currently loaded in the transport. */
export function transportGetUrl() {
  return _currentUrl;
}

/**
 * Subscribe to transport time updates.
 * @param {function} cb - called with (currentTime, duration) on each update
 * @returns {function} unsubscribe function
 */
export function transportOnTimeUpdate(cb) {
  if (!ws) return () => {};
  const handler = (time) => cb(time, ws.getDuration());
  ws.on('timeupdate', handler);
  return () => ws.un('timeupdate', handler);
}

/**
 * Subscribe to transport play/pause state changes.
 * @param {function} cb - called with (isPlaying) on state change
 * @returns {function} unsubscribe function
 */
export function transportOnStateChange(cb) {
  if (!ws) return () => {};
  const onPlay = () => cb(true);
  const onPause = () => cb(false);
  const onFinish = () => cb(false);
  ws.on('play', onPlay);
  ws.on('pause', onPause);
  ws.on('finish', onFinish);
  return () => { ws.un('play', onPlay); ws.un('pause', onPause); ws.un('finish', onFinish); };
}
