/**
 * Canvas-based piano roll visualization for MIDI data.
 */

const COLORS = {
  vocals:  '#22c55e',
  drums:   '#ef4444',
  bass:    '#3b82f6',
  other:   '#a855f7',
  guitar:  '#f59e0b',
  piano:   '#ec4899',
  default: '#8b8b9e',
};

/**
 * Draw a piano roll on a canvas element.
 * @param {HTMLCanvasElement} canvas
 * @param {Array<{start: number, end: number, pitch: number, stem: string}>} notes
 * @param {number} duration - total duration in seconds
 */
export function drawPianoRoll(canvas, notes, duration) {
  const ctx = canvas.getContext('2d');
  const w = canvas.width = canvas.offsetWidth * (window.devicePixelRatio || 1);
  const h = canvas.height = canvas.offsetHeight * (window.devicePixelRatio || 1);

  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = '#15151c';
  ctx.fillRect(0, 0, w, h);

  if (!notes.length) return;

  // Compute pitch range
  let minPitch = 127, maxPitch = 0;
  for (const n of notes) {
    if (n.pitch < minPitch) minPitch = n.pitch;
    if (n.pitch > maxPitch) maxPitch = n.pitch;
  }
  minPitch = Math.max(0, minPitch - 2);
  maxPitch = Math.min(127, maxPitch + 2);
  const pitchRange = maxPitch - minPitch || 1;

  // Draw grid lines
  ctx.strokeStyle = '#2a2a3a';
  ctx.lineWidth = 0.5;
  for (let p = minPitch; p <= maxPitch; p++) {
    const y = h - ((p - minPitch) / pitchRange) * h;
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.lineTo(w, y);
    ctx.stroke();
  }

  // Draw notes
  const noteHeight = Math.max(2, h / pitchRange * 0.8);
  for (const n of notes) {
    const x = (n.start / duration) * w;
    const endX = (n.end / duration) * w;
    const y = h - ((n.pitch - minPitch) / pitchRange) * h - noteHeight / 2;
    const color = COLORS[n.stem] || COLORS.default;

    ctx.fillStyle = color;
    ctx.fillRect(x, y, Math.max(1, endX - x), noteHeight);
  }
}
