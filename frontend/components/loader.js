/**
 * File loader — drag-and-drop + browse button.
 * Lives inside the Separate tab panel.
 * Supports single-file mode (default) and batch mode (multiple files).
 */

import { appState, apiUpload, el, formatTime } from '../app.js';
import { transportLoad } from './audio-player.js';

function clearChildren(elem) {
  while (elem.firstChild) elem.removeChild(elem.firstChild);
}

/** Batch mode state — shared with separate.js via appState */
let _batchMode = false;

export function isBatchMode() { return _batchMode; }
export function getBatchFiles() { return appState._batchFiles || []; }

export function initLoader() {
  const panel = document.getElementById('panel-separate');

  // ─── Batch toggle ───
  const batchToggle = el('div', { className: 'batch-toggle', id: 'batch-toggle' },
    el('label', {},
      el('input', { type: 'checkbox', id: 'batch-mode-cb' }),
      ' Batch mode',
    ),
  );

  const dropZone = el('div', { className: 'drop-zone', id: 'drop-zone' },
    el('span', { className: 'drop-icon' }, '\u{1F3B5}'),
    el('span', { className: 'drop-text' }, 'Drop an audio file here or click to browse'),
    el('span', { className: 'drop-hint' }, 'WAV, FLAC, MP3, OGG, AIFF — or video: MP4, MKV, WEBM, AVI, MOV'),
  );

  const fileInput = el('input', {
    type: 'file',
    accept: '.wav,.flac,.mp3,.ogg,.aiff,.aif,.mp4,.mkv,.webm,.avi,.mov,.m4v,.flv',
    style: { display: 'none' },
    id: 'file-input',
  });

  const fileInfo = el('div', { className: 'file-info hidden', id: 'file-info' });

  // Insert at the top of Separate panel
  panel.prepend(fileInfo);
  panel.prepend(dropZone);
  panel.prepend(fileInput);
  panel.prepend(batchToggle);

  // ─── Batch toggle handler ───
  document.getElementById('batch-mode-cb').addEventListener('change', (e) => {
    _batchMode = e.target.checked;
    fileInput.multiple = _batchMode;
    appState._batchFiles = [];

    // Reset UI
    clearChildren(fileInfo);
    fileInfo.classList.add('hidden');
    clearChildren(dropZone);

    if (_batchMode) {
      dropZone.append(
        el('span', { className: 'drop-icon' }, '\u{1F4E6}'),
        el('span', { className: 'drop-text' }, 'Drop multiple audio files or click to browse'),
        el('span', { className: 'drop-hint' }, 'All files will be processed with the same stem selection'),
      );
    } else {
      dropZone.append(
        el('span', { className: 'drop-icon' }, '\u{1F3B5}'),
        el('span', { className: 'drop-text' }, 'Drop an audio file here or click to browse'),
        el('span', { className: 'drop-hint' }, 'WAV, FLAC, MP3, OGG, AIFF — or video: MP4, MKV, WEBM, AVI, MOV'),
      );
    }

    appState.emit('batchModeChanged', _batchMode);
  });

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

    if (_batchMode) {
      const files = Array.from(e.dataTransfer.files);
      if (files.length) handleBatchFiles(files);
    } else {
      const file = e.dataTransfer.files[0];
      if (file) handleFile(file);
    }
  });

  // File input change
  fileInput.addEventListener('change', () => {
    if (_batchMode) {
      const files = Array.from(fileInput.files);
      if (files.length) handleBatchFiles(files);
    } else {
      const file = fileInput.files[0];
      if (file) handleFile(file);
    }
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
    transportLoad(`/api/audio/stream?path=${encodeURIComponent(info.path)}`, info.filename, false, 'Upload');

    appState.emit('fileLoaded', info);
  } catch (err) {
    clearChildren(dropZone);
    dropZone.append(
      el('span', { className: 'drop-text', style: { color: 'var(--error)' } }, `Error: ${err.message}`),
      el('span', { className: 'drop-hint' }, 'Try again'),
    );
  }
}

async function handleBatchFiles(fileList) {
  const dropZone = document.getElementById('drop-zone');
  const fileInfoEl = document.getElementById('file-info');

  clearChildren(dropZone);
  dropZone.appendChild(el('span', { className: 'drop-text' }, `Uploading ${fileList.length} files...`));

  try {
    const form = new FormData();
    for (const f of fileList) form.append('files', f);

    const res = await fetch('/api/upload-batch', { method: 'POST', body: form });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || `HTTP ${res.status}`);
    }
    const data = await res.json();
    const uploaded = data.files.filter(f => !f.error);
    const errors = data.files.filter(f => f.error);

    appState._batchFiles = uploaded;

    // Show batch file list
    fileInfoEl.classList.remove('hidden');
    clearChildren(fileInfoEl);

    const list = el('div', { className: 'batch-file-list' });
    for (const f of uploaded) {
      list.appendChild(el('div', { className: 'batch-file-item' },
        el('span', { className: 'fi-label' }, '\u2713 '),
        el('span', {}, `${f.filename} (${formatTime(f.duration)})`),
      ));
    }
    for (const f of errors) {
      list.appendChild(el('div', { className: 'batch-file-item batch-file-error' },
        el('span', { className: 'fi-label' }, '\u2717 '),
        el('span', {}, `${f.filename}: ${f.error}`),
      ));
    }
    fileInfoEl.appendChild(list);

    clearChildren(dropZone);
    dropZone.append(
      el('span', { className: 'drop-text' }, `\u2713 ${uploaded.length} file${uploaded.length !== 1 ? 's' : ''} ready`),
      el('span', { className: 'drop-hint' }, 'Drop more files to replace'),
    );

    appState.emit('batchFilesLoaded', uploaded);
  } catch (err) {
    clearChildren(dropZone);
    dropZone.append(
      el('span', { className: 'drop-text', style: { color: 'var(--error)' } }, `Error: ${err.message}`),
      el('span', { className: 'drop-hint' }, 'Try again'),
    );
  }
}
