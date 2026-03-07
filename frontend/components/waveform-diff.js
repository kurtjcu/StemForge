/**
 * Shared waveform analysis + diff visualization utilities.
 *
 * Extracted from compose.js so both compose and enhance tabs can
 * render before/after diff waveforms with per-bar color coding.
 */

// ── Color helpers ─────────────────────────────────────────────────────

export function getComputedColor(varName) {
  return getComputedStyle(document.documentElement).getPropertyValue(varName).trim();
}

export function hexToRgb(hex) {
  if (hex.startsWith('#')) {
    const n = parseInt(hex.slice(1), 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }
  const m = hex.match(/(\d+)/g);
  return m ? [+m[0], +m[1], +m[2]] : [107, 107, 132];
}

// ── Audio peak extraction ─────────────────────────────────────────────

/**
 * Decode audio from a URL and compute per-bar peak amplitudes.
 * @param {string} audioUrl
 * @param {number} barCount
 * @returns {Promise<Float32Array>}
 */
export async function decodeAudioPeaks(audioUrl, barCount) {
  const resp = await fetch(audioUrl);
  if (!resp.ok) throw new Error(resp.statusText);
  const arrayBuf = await resp.arrayBuffer();
  const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  const audioBuf = await audioCtx.decodeAudioData(arrayBuf);
  audioCtx.close();

  const channels = audioBuf.numberOfChannels;
  const length = audioBuf.length;
  const mono = new Float32Array(length);
  for (let ch = 0; ch < channels; ch++) {
    const data = audioBuf.getChannelData(ch);
    for (let i = 0; i < length; i++) mono[i] += data[i] / channels;
  }

  if (barCount < 1) barCount = 1;
  const samplesPerBar = Math.floor(length / barCount);
  const peaks = new Float32Array(barCount);
  for (let i = 0; i < barCount; i++) {
    let peak = 0;
    const offset = i * samplesPerBar;
    for (let j = 0; j < samplesPerBar; j++) {
      const abs = Math.abs(mono[offset + j] || 0);
      if (abs > peak) peak = abs;
    }
    peaks[i] = peak;
  }
  return peaks;
}

// ── Canvas bar drawing ────────────────────────────────────────────────

/**
 * Draw bars on a canvas with per-bar color via colorFn(barIndex).
 * @param {HTMLCanvasElement} canvasEl
 * @param {HTMLElement} containerEl
 * @param {Float32Array} peaks
 * @param {function(number): string} colorFn - returns CSS color for bar i
 */
export function drawAnalyzeWaveform(canvasEl, containerEl, peaks, colorFn) {
  const dpr = window.devicePixelRatio || 1;
  const rect = containerEl.getBoundingClientRect();
  canvasEl.width = rect.width * dpr;
  canvasEl.height = rect.height * dpr;
  canvasEl.style.width = rect.width + 'px';
  canvasEl.style.height = rect.height + 'px';
  const ctx = canvasEl.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

  const w = rect.width;
  const h = rect.height;
  const barCount = peaks.length;
  if (barCount === 0) return;

  const barWidth = w / barCount;
  const midY = h / 2;
  const maxBarH = h * 0.85;

  ctx.clearRect(0, 0, w, h);
  for (let i = 0; i < barCount; i++) {
    const x = i * barWidth;
    const barH = Math.max(1, peaks[i] * maxBarH);
    ctx.fillStyle = colorFn(i);
    ctx.fillRect(x, midY - barH / 2, Math.max(1, barWidth - 0.5), barH);
  }
}

// ── Diff waveform rendering ───────────────────────────────────────────

/**
 * Render a diff waveform: bars colored by magnitude delta from source.
 * Bars with little change stay --text-muted; bars with large change glow --accent.
 *
 * @param {HTMLCanvasElement} canvasEl
 * @param {HTMLElement} containerEl
 * @param {Float32Array} resultPeaks
 * @param {Float32Array|null} sourcePeaks
 */
export function renderDiffWaveform(canvasEl, containerEl, resultPeaks, sourcePeaks) {
  const barCount = resultPeaks.length;

  const diffs = new Float32Array(barCount);
  let maxDiff = 0;
  for (let i = 0; i < barCount; i++) {
    const srcPeak = sourcePeaks ? (sourcePeaks[i] || 0) : 0;
    diffs[i] = Math.abs(resultPeaks[i] - srcPeak);
    if (diffs[i] > maxDiff) maxDiff = diffs[i];
  }

  const mutedHex = getComputedColor('--text-muted');
  const accentHex = getComputedColor('--accent');
  const mRgb = hexToRgb(mutedHex);
  const aRgb = hexToRgb(accentHex);

  drawAnalyzeWaveform(canvasEl, containerEl, resultPeaks, (i) => {
    if (maxDiff === 0) return mutedHex;
    const t = diffs[i] / maxDiff;
    const e = t * t; // quadratic easing — only strong diffs pop
    const r = Math.round(mRgb[0] + (aRgb[0] - mRgb[0]) * e);
    const g = Math.round(mRgb[1] + (aRgb[1] - mRgb[1]) * e);
    const b = Math.round(mRgb[2] + (aRgb[2] - mRgb[2]) * e);
    return `rgb(${r},${g},${b})`;
  });
}
