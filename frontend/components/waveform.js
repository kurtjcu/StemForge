/**
 * wavesurfer.js wrapper for StemForge.
 */

import WaveSurfer from 'wavesurfer.js';

const defaults = {
  height: 50,
  barWidth: 2,
  barGap: 1,
  barRadius: 2,
  cursorWidth: 1,
  cursorColor: '#f59e0b',
  normalize: true,
};

/**
 * Create a wavesurfer instance in the given container.
 * @param {HTMLElement} container
 * @param {object} opts - override colors/height
 * @returns {WaveSurfer}
 */
export function createWaveform(container, opts = {}) {
  const color = opts.color || 'audio';

  const colors = color === 'midi'
    ? { waveColor: '#a855f7', progressColor: '#7c3aed' }
    : { waveColor: '#22c55e', progressColor: '#16a34a' };

  const ws = WaveSurfer.create({
    container,
    ...defaults,
    ...colors,
    height: opts.height || defaults.height,
    interact: opts.interact !== false,
  });

  return ws;
}
