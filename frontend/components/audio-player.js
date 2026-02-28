/**
 * Global transport bar — plays any audio file loaded into it.
 */

import { createWaveform } from './waveform.js';
import { formatTime } from '../app.js';

let ws = null;
let currentLabel = '';

export function initTransport() {
  const container = document.getElementById('transport-waveform');
  ws = createWaveform(container, { height: 36 });

  ws.on('timeupdate', (time) => {
    const dur = ws.getDuration();
    document.getElementById('transport-time').textContent =
      `${formatTime(time)} / ${formatTime(dur)}`;
  });

  document.getElementById('transport-play').addEventListener('click', () => {
    if (ws) ws.playPause();
  });

  document.getElementById('transport-stop').addEventListener('click', () => {
    if (ws) { ws.stop(); }
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

export function transportPlay() {
  if (ws && !ws.isPlaying()) ws.play();
}

export function transportStop() {
  if (ws) ws.stop();
}
