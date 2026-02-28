/**
 * File loader — drag-and-drop + browse button.
 * Lives inside the Separate tab panel.
 */

import { appState, apiUpload, el, formatTime } from '../app.js';
import { transportLoad } from './audio-player.js';

function clearChildren(elem) {
  while (elem.firstChild) elem.removeChild(elem.firstChild);
}

export function initLoader() {
  const panel = document.getElementById('panel-separate');

  const dropZone = el('div', { className: 'drop-zone', id: 'drop-zone' },
    el('span', { className: 'drop-icon' }, '\u{1F3B5}'),
    el('span', { className: 'drop-text' }, 'Drop an audio file here or click to browse'),
    el('span', { className: 'drop-hint' }, 'WAV, FLAC, MP3, OGG, AIFF'),
  );

  const fileInput = el('input', {
    type: 'file',
    accept: '.wav,.flac,.mp3,.ogg,.aiff,.aif',
    style: { display: 'none' },
    id: 'file-input',
  });

  const fileInfo = el('div', { className: 'file-info hidden', id: 'file-info' });

  // Insert at the top of Separate panel
  panel.prepend(fileInfo);
  panel.prepend(dropZone);
  panel.prepend(fileInput);

  // Click to browse
  dropZone.addEventListener('click', () => fileInput.click());

  // Drag events
  dropZone.addEventListener('dragover', (e) => {
    e.preventDefault();
    dropZone.classList.add('dragover');
  });
  dropZone.addEventListener('dragleave', () => {
    dropZone.classList.remove('dragover');
  });
  dropZone.addEventListener('drop', (e) => {
    e.preventDefault();
    dropZone.classList.remove('dragover');
    const file = e.dataTransfer.files[0];
    if (file) handleFile(file);
  });

  // File input change
  fileInput.addEventListener('change', () => {
    const file = fileInput.files[0];
    if (file) handleFile(file);
  });
}

async function handleFile(file) {
  const dropZone = document.getElementById('drop-zone');
  const fileInfoEl = document.getElementById('file-info');

  clearChildren(dropZone);
  dropZone.appendChild(el('span', { className: 'drop-text' }, 'Uploading...'));

  try {
    const info = await apiUpload('/upload', file);
    appState.audioPath = info.path;
    appState.audioInfo = info;

    // Show file info
    fileInfoEl.classList.remove('hidden');
    clearChildren(fileInfoEl);
    fileInfoEl.append(
      el('span', { className: 'fi-item' },
        el('span', { className: 'fi-label' }, 'File: '),
        el('span', {}, info.filename),
      ),
      el('span', { className: 'fi-item' },
        el('span', { className: 'fi-label' }, 'Duration: '),
        el('span', {}, formatTime(info.duration)),
      ),
      el('span', { className: 'fi-item' },
        el('span', { className: 'fi-label' }, 'Rate: '),
        el('span', {}, `${info.sample_rate} Hz`),
      ),
      el('span', { className: 'fi-item' },
        el('span', { className: 'fi-label' }, 'Ch: '),
        el('span', {}, `${info.channels}`),
      ),
    );

    // Update drop zone
    clearChildren(dropZone);
    dropZone.append(
      el('span', { className: 'drop-text' }, `\u2713 ${info.filename}`),
      el('span', { className: 'drop-hint' }, 'Drop another file to replace'),
    );

    // Load in transport bar (stopped at position 0)
    transportLoad(`/api/audio/stream?path=${encodeURIComponent(info.path)}`, info.filename, false);

    appState.emit('fileLoaded', info);
  } catch (err) {
    clearChildren(dropZone);
    dropZone.append(
      el('span', { className: 'drop-text', style: { color: 'var(--error)' } }, `Error: ${err.message}`),
      el('span', { className: 'drop-hint' }, 'Try again'),
    );
  }
}
