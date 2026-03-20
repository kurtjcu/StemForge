/**
 * Global transport bar — mirrors and controls the active card's audio.
 *
 * Two modes:
 * 1. Card-driven (most components): card ws plays audio, transport mirrors
 *    cursor position and proxies play/pause/stop controls to the card.
 *    Activated by passing { cardWs } in transportLoad opts.
 * 2. Transport-driven (compose): transport ws plays audio directly.
 *    Activated by passing autoplay=true without cardWs.
 */

import { createWaveform } from './waveform.js';
import { formatTime } from '../app.js';

let ws = null;
let currentLabel = '';
let _playBtn = null;

/** Active card registration (card-driven mode). */
let _activeCardWs = null;
let _cardUnsub = null;

function _syncPlayBtn() {
  if (!_playBtn) return;
  const playing = _activeCardWs
    ? _activeCardWs.isPlaying()
    : (ws ? ws.isPlaying() : false);
  _playBtn.textContent = playing ? '\u23F8' : '\u25B6';
  _playBtn.title = playing ? 'Pause' : 'Play';
}

/** Remove card-driven subscriptions. */
function _clearCardLink() {
  if (_cardUnsub) { _cardUnsub(); _cardUnsub = null; }
  _activeCardWs = null;
}

export function initTransport() {
  const container = document.getElementById('transport-waveform');
  ws = createWaveform(container, { height: 36 });
  _playBtn = document.getElementById('transport-play');

  // Transport-driven mode: update time display from transport ws
  ws.on('timeupdate', (time) => {
    if (_activeCardWs) return; // card-driven mode handles its own display
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

  // Transport play → proxy to card or self
  _playBtn.addEventListener('click', () => {
    if (_activeCardWs) {
      _activeCardWs.playPause();
      _syncPlayBtn();
    } else if (ws) {
      ws.playPause();
    }
  });

  // Transport stop → proxy to card or self
  document.getElementById('transport-stop').addEventListener('click', () => {
    if (_activeCardWs) {
      _activeCardWs.stop();
      _clearCardLink();
      _syncPlayBtn();
    } else if (ws) {
      ws.stop();
      _syncPlayBtn();
    }
  });

  // Transport rewind → proxy to card or self
  document.getElementById('transport-rewind').addEventListener('click', () => {
    if (_activeCardWs) {
      _activeCardWs.setTime(0);
    } else if (ws) {
      ws.seekTo(0);
    }
  });
}

/**
 * Load an audio URL into the transport bar.
 * @param {string} url - audio URL to load
 * @param {string} label - display label
 * @param {boolean} autoplay - start playing once loaded (transport-driven mode)
 * @param {string} source - tab/section name shown in "Now Playing (source)"
 * @param {object} [opts] - { cardWs: WaveSurfer } for card-driven mode
 */
export function transportLoad(url, label = '', autoplay = true, source = '', opts = {}) {
  if (!ws) return;

  // Clean up previous card link
  _clearCardLink();

  currentLabel = label;
  const prefix = source ? `Now Playing (${source})` : 'Now Playing';
  document.getElementById('transport-label').textContent = label ? `${prefix}: ${label}` : '';

  ws.load(url);

  if (opts.cardWs) {
    // ── Card-driven mode: mirror card's playback in the transport ──
    _activeCardWs = opts.cardWs;
    const cardWs = opts.cardWs;

    const onTime = (time) => {
      const dur = cardWs.getDuration();
      // Sync transport cursor
      if (dur > 0 && ws) ws.seekTo(time / dur);
      // Sync transport time display
      document.getElementById('transport-time').textContent =
        `${formatTime(time)} / ${formatTime(dur)}`;
    };
    const onState = () => _syncPlayBtn();
    const onFinish = () => {
      _clearCardLink();
      _syncPlayBtn();
    };

    cardWs.on('timeupdate', onTime);
    cardWs.on('play', onState);
    cardWs.on('pause', onState);
    cardWs.on('finish', onFinish);

    _cardUnsub = () => {
      cardWs.un('timeupdate', onTime);
      cardWs.un('play', onState);
      cardWs.un('pause', onState);
      cardWs.un('finish', onFinish);
    };
  } else if (autoplay) {
    // ── Transport-driven mode: transport plays its own audio ──
    ws.once('ready', () => ws.play());
  }
}

export function transportPlayPause() {
  if (_activeCardWs) {
    _activeCardWs.playPause();
    _syncPlayBtn();
  } else if (ws) {
    ws.playPause();
  }
}

export function transportPlay() {
  if (_activeCardWs) {
    if (!_activeCardWs.isPlaying()) _activeCardWs.play();
    _syncPlayBtn();
  } else if (ws && !ws.isPlaying()) {
    ws.play();
  }
}

export function transportPause() {
  if (_activeCardWs) {
    if (_activeCardWs.isPlaying()) _activeCardWs.pause();
    _syncPlayBtn();
  } else if (ws && ws.isPlaying()) {
    ws.pause();
  }
}

export function transportStop() {
  if (_activeCardWs) {
    _activeCardWs.stop();
    _clearCardLink();
  }
  if (ws) { ws.stop(); }
  _syncPlayBtn();
}

export function transportSeekTo(fraction) {
  if (_activeCardWs) _activeCardWs.seekTo(fraction);
  if (ws) ws.seekTo(fraction);
}

export function transportGetCurrentTime() {
  if (_activeCardWs) return _activeCardWs.getCurrentTime();
  return ws ? ws.getCurrentTime() : 0;
}

export function transportGetDuration() {
  if (_activeCardWs) return _activeCardWs.getDuration();
  return ws ? ws.getDuration() : 0;
}

export function transportIsPlaying() {
  if (_activeCardWs) return _activeCardWs.isPlaying();
  return ws ? ws.isPlaying() : false;
}

/**
 * Subscribe to transport time updates.
 * Fires from the transport ws (transport-driven mode only).
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
 * Fires from the transport ws (transport-driven mode only).
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
