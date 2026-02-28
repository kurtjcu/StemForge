/**
 * Global transport bar — single audio playback engine for the app.
 *
 * Uses wavesurfer.js for waveform rendering + audio playback.
 * Other components (compose result cards, separate, etc.) load audio
 * into the transport via transportLoad() and control it via the
 * exported functions. There is only one audio source at a time.
 */

import { createWaveform } from './waveform.js';
import { formatTime } from '../app.js';

let ws = null;
let currentLabel = '';
let _playBtn = null;

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
    ws.seekTo(0);
    _syncPlayBtn();
  });

  _playBtn.addEventListener('click', () => {
    if (ws) ws.playPause();
  });

  document.getElementById('transport-stop').addEventListener('click', () => {
    if (ws) { ws.stop(); _syncPlayBtn(); }
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
 */
export function transportLoad(url, label = '', autoplay = true) {
  if (!ws) return;
  currentLabel = label;
  document.getElementById('transport-label').textContent = label;
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
  if (ws) { ws.stop(); _syncPlayBtn(); }
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
