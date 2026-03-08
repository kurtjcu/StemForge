/**
 * Compose tab — AceStep music generation UI.
 *
 * Adapted from ACE-Step Wrangler's frontend (index.html + app.js) into
 * StemForge's ES module pattern. All DOM built programmatically via el().
 */

import { appState, api, pollJob, el, formatTime, saveFileAs } from '../app.js';
import { createWaveform } from './waveform.js';
import { transportLoad, transportStop, transportIsPlaying, transportPlayPause } from './audio-player.js';
import {
  getComputedColor as _getComputedColor,
  hexToRgb as _hexToRgb,
  decodeAudioPeaks as _decodeAudioPeaks,
  drawAnalyzeWaveform as _drawAnalyzeWaveform,
  renderDiffWaveform as _renderDiffWaveform,
} from './waveform-diff.js';

function clearChildren(elem) {
  while (elem.firstChild) elem.removeChild(elem.firstChild);
}

// ─── Module state ────────────────────────────────────────────────────

let _mode = 'create';          // 'create' | 'rework' | 'lego' | 'complete' | 'voice' | 'train'
let _createTab = 'my-lyrics';  // 'my-lyrics' | 'ai-lyrics' | 'instrumental'
let _approach = 'cover';       // 'cover' | 'repaint'
let _uploadedPath = null;
let _uploadedDuration = null;
let _legoUploadedPath = null;
let _legoUploadedDuration = null;
let _completeUploadedPath = null;
let _completeUploadedDuration = null;
let _selectedLegoTrack = 'vocals';
let _selectedCompleteTracks = [];

// Voice mode state
let _voiceSourcePath = null;
let _voiceSourceDuration = null;
let _voiceSourcePeaks = null;
let _voiceModels = [];
let _voiceJobId = null;

// Seed recall
let _lastSeed = null;

const ACE_TRACKS = [
  'vocals', 'backing_vocals', 'drums', 'bass', 'guitar', 'keyboard',
  'strings', 'brass', 'woodwinds', 'synth', 'percussion', 'fx',
];

// Per-bar peaks cached from source uploads (for diff visualization)
let _legoSourcePeaks = null;
let _completeSourcePeaks = null;
let _autoOn = false;
let _aceStepRunning = false;
let _pollTimer = null;
let _elapsedTimer = null;

// Batch size limits by VRAM tier
const _BATCH_LIMITS = {
  '16': { heavy: 1, normal: 2 },
  '24': { heavy: 2, normal: 4 },
  '32': { heavy: 4, normal: 8 },
};

// Lyric adherence → guidance_scale, quality → inference steps
const _LYRIC_STEPS = [3.0, 7.0, 12.0];
const _QUALITY_STEPS = [15, 60, 120];

// ─── Helpers ─────────────────────────────────────────────────────────

function _id(id) { return document.getElementById(id); }

function _updateSlider(slider) {
  const val = Number(slider.value);
  const min = Number(slider.min);
  const max = Number(slider.max);
  slider.style.setProperty('--fill', ((val - min) / (max - min)) * 100 + '%');
}

// ─── Analyze waveform utilities (imported from waveform-diff.js) ────

async function _renderSourceWaveform(audioUrl, containerId, canvasId, peaksSetter) {
  const container = _id(containerId);
  const canvas = _id(canvasId);
  if (!container || !canvas) return;
  container.closest('.analyze-wf-section')?.classList.remove('hidden');

  const dpr = window.devicePixelRatio || 1;
  const rect = container.getBoundingClientRect();
  const barCount = Math.max(1, Math.floor((rect.width * dpr) / (2 * dpr)));

  try {
    const peaks = await _decodeAudioPeaks(audioUrl, barCount);
    peaksSetter(peaks);
    const mutedColor = _getComputedColor('--text-muted');
    _drawAnalyzeWaveform(canvas, container, peaks, () => mutedColor);
  } catch (err) {
    console.error('Source waveform error:', err);
  }
}

async function _renderResultWaveform(resultAudioUrl, containerId, canvasId, sourcePeaks) {
  const container = _id(containerId);
  const canvas = _id(canvasId);
  if (!container || !canvas) return;
  container.closest('.analyze-wf-section')?.classList.remove('hidden');

  const barCount = sourcePeaks ? sourcePeaks.length : 200;

  try {
    const resultPeaks = await _decodeAudioPeaks(resultAudioUrl, barCount);
    _renderDiffWaveform(canvas, container, resultPeaks, sourcePeaks);
  } catch (err) {
    console.error('Result waveform error:', err);
  }
}

function _modeLabel() {
  if (_mode === 'rework') return _approach === 'cover' ? '\u25B6 Reimagine' : '\u25B6 Fix & Blend';
  if (_mode === 'lego') return '\u25B6 Replace Track';
  if (_mode === 'complete') return '\u25B6 Complete';
  if (_mode === 'voice') return '\u25B6 Transform Voice';
  return '\u25B6 Generate';
}

function _formatDuration(secs) {
  const m = Math.floor(secs / 60);
  const s = Math.round(secs % 60);
  return m > 0 ? `${m}m ${String(s).padStart(2, '0')}s` : `${s}s`;
}

// ─── Init ────────────────────────────────────────────────────────────

export function initCompose() {
  const panel = _id('panel-compose');

  // Check AceStep health first
  checkHealth(panel);
}

async function checkHealth(panel) {
  try {
    const health = await api('/compose/health');
    if (health.acestep_status === 'disabled') {
      panel.appendChild(
        el('div', { className: 'compose-unavailable' },
          el('div', { className: 'compose-unavailable-icon' }, '\u266A'),
          el('p', {}, 'AceStep is not enabled. Start StemForge without --no-acestep to use Compose.'),
        ),
      );
      return;
    }
    if (health.acestep_status === 'crashed') {
      panel.appendChild(
        el('div', { className: 'compose-unavailable' },
          el('div', { className: 'compose-unavailable-icon' }, '\u26A0'),
          el('p', {}, 'AceStep encountered an error. Check the terminal for details.'),
        ),
      );
      return;
    }
    // "ready" and "running" both proceed to build the UI normally.
    // "ready" means AceStep will start on first generate.
    _aceStepRunning = (health.acestep_status === 'running');
  } catch {
    // Server not yet ready — build UI anyway, endpoints will check health
  }

  buildUI(panel);
}

// ─── Build Full UI ───────────────────────────────────────────────────

function buildUI(panel) {
  // Mode selector (Create / Rework / Lego / Complete) + create tabs
  const modeBar = el('div', { className: 'compose-mode-bar' },
    el('div', { className: 'compose-mode-selector' },
      el('button', { className: 'compose-mode-btn active', 'data-mode': 'create', onClick: () => switchMode('create') }, 'Create'),
      el('button', { className: 'compose-mode-btn', 'data-mode': 'rework', onClick: () => switchMode('rework') }, 'Rework'),
      el('button', { className: 'compose-mode-btn', 'data-mode': 'lego', onClick: () => switchMode('lego') }, 'Lego'),
      el('button', { className: 'compose-mode-btn', 'data-mode': 'complete', onClick: () => switchMode('complete') }, 'Complete'),
      el('button', { className: 'compose-mode-btn', 'data-mode': 'voice', onClick: () => switchMode('voice') }, 'Voice'),
      el('button', { className: 'compose-mode-btn', 'data-mode': 'train', onClick: () => switchMode('train') }, 'Train'),
    ),
    el('div', { className: 'compose-create-tabs', id: 'compose-create-tabs' },
      el('button', { className: 'compose-create-tab active', 'data-tab': 'my-lyrics', onClick: () => switchCreateTab('my-lyrics') }, 'My Lyrics'),
      el('button', { className: 'compose-create-tab', 'data-tab': 'ai-lyrics', onClick: () => switchCreateTab('ai-lyrics') }, 'AI Lyrics'),
      el('button', { className: 'compose-create-tab', 'data-tab': 'instrumental', onClick: () => switchCreateTab('instrumental') }, 'Instrumental'),
    ),
  );

  // 3-column layout
  const mainGrid = el('div', { className: 'compose-main' });

  // Left column
  const leftCol = buildLeftColumn();
  // Center column (lyrics)
  const centerCol = buildCenterColumn();
  // Right column (controls)
  const rightCol = buildRightColumn();

  mainGrid.append(leftCol, centerCol, rightCol);

  // Train panels live in a separate grid (hidden by default)
  const trainGrid = el('div', { className: 'compose-main compose-train-grid hidden', id: 'compose-train-grid' });
  const trainLeft = buildTrainLeftPanel();
  const trainCenter = buildTrainCenterPanel();
  const trainRight = buildTrainRightPanel();
  trainGrid.append(trainLeft, trainCenter, trainRight);

  // Output panel
  const output = buildOutputPanel();

  panel.append(modeBar, mainGrid, trainGrid, output);

  // Init slider fills
  panel.querySelectorAll('.compose-slider').forEach(s => {
    _updateSlider(s);
    s.addEventListener('input', () => _updateSlider(s));
  });

  // Sync advanced sliders from friendly defaults
  syncAdvancedFromFriendly();

  // Populate voice stem selector when stems become available
  appState.on('stemsReady', () => _populateVoiceStemSelect());
  // Also populate if stems already exist
  _populateVoiceStemSelect();
}

// ─── Left Column (Style / Rework) ───────────────────────────────────

function buildLeftColumn() {
  const col = el('div', { className: 'compose-col compose-col-left' });

  // CREATE MODE panel
  const createPanel = el('div', { className: 'compose-panel-inner', id: 'compose-create-panel' });

  // Genre tags
  const genres = ['Electronic', 'Hip-Hop', 'Jazz', 'Rock', 'Classical', 'Ambient', 'Pop', 'R&B',
    'Folk', 'Metal', 'Latin', 'Blues', 'Country', 'Reggae', 'Soul', 'Funk'];
  const genreGrid = el('div', { className: 'compose-tag-grid' });
  for (const g of genres) {
    genreGrid.appendChild(el('button', { className: 'compose-tag', onClick: (e) => {
      e.target.classList.toggle('active');
      updateStylePreview();
    }}, g));
  }

  // Mood tags
  const moods = ['Uplifting', 'Melancholic', 'Energetic', 'Chill', 'Dark', 'Dreamy', 'Intense', 'Romantic', 'Nostalgic', 'Aggressive'];
  const moodGrid = el('div', { className: 'compose-tag-grid' });
  for (const m of moods) {
    moodGrid.appendChild(el('button', { className: 'compose-tag', onClick: (e) => {
      e.target.classList.toggle('active');
      updateStylePreview();
    }}, m));
  }

  // Tag status
  const tagStatus = el('div', { className: 'compose-tags-status hidden', id: 'compose-tags-status' },
    el('span', { id: 'compose-tags-count' }, '0 selected'),
    el('button', { className: 'compose-ghost-btn', onClick: () => {
      document.querySelectorAll('#compose-create-panel .compose-tag.active').forEach(t => t.classList.remove('active'));
      updateStylePreview();
    }}, 'Clear all'),
  );

  // Song parameters
  const songParams = el('div', { className: 'compose-song-params' },
    el('span', { className: 'compose-label-sm' }, 'Song Parameters'),
    el('div', { className: 'compose-key-row' },
      el('select', { id: 'compose-key-root', className: 'compose-select compose-select-narrow', onChange: updateStylePreview },
        el('option', { value: '' }, 'Key'),
        ...['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B'].map(
          k => el('option', { value: k }, k)),
      ),
      el('select', { id: 'compose-key-mode', className: 'compose-select', onChange: updateStylePreview },
        el('option', { value: 'major' }, 'Major'),
        el('option', { value: 'minor' }, 'Minor'),
      ),
    ),
    el('div', { className: 'compose-param-grid' },
      el('input', { type: 'number', id: 'compose-bpm', className: 'compose-number', min: '40', max: '300', placeholder: 'BPM', onInput: updateStylePreview }),
      el('select', { id: 'compose-time-sig', className: 'compose-select', onChange: updateStylePreview },
        el('option', { value: '4/4', selected: 'true' }, '4/4'),
        el('option', { value: '3/4' }, '3/4'),
        el('option', { value: '6/8' }, '6/8'),
        el('option', { value: '5/4' }, '5/4'),
        el('option', { value: '7/8' }, '7/8'),
      ),
    ),
  );

  // Custom description
  const customDesc = el('div', { className: 'compose-field-group' },
    el('label', { className: 'compose-field-label' }, 'Custom description'),
    el('textarea', { id: 'compose-style-text', className: 'compose-textarea', rows: '3',
      placeholder: 'Describe your sound\u2026 e.g. dreamy lo-fi with warm bass and vinyl crackle',
      onInput: updateStylePreview }),
  );

  // Style preview
  const preview = el('div', { className: 'compose-style-preview' },
    el('span', { className: 'compose-label-sm' }, 'Style prompt'),
    el('span', { className: 'compose-preview-text', id: 'compose-preview-text' }, 'Nothing set \u2014 add tags or a description'),
  );

  createPanel.append(
    el('span', { className: 'compose-label-sm' }, 'Genre'), genreGrid,
    el('span', { className: 'compose-label-sm' }, 'Mood'), moodGrid,
    tagStatus, songParams,
    el('div', { className: 'compose-divider' }),
    customDesc, preview,
  );

  // REWORK MODE panel
  const reworkPanel = el('div', { className: 'compose-panel-inner hidden', id: 'compose-rework-panel' });

  // Upload zone
  const uploadZone = el('div', { className: 'compose-upload-zone', id: 'compose-upload-zone' },
    el('div', { id: 'compose-upload-prompt' },
      el('span', {}, '\u266B Drop audio here or '),
      el('button', { className: 'compose-ghost-btn', onClick: browseAudio }, 'Browse'),
    ),
    el('div', { className: 'hidden', id: 'compose-upload-loaded' },
      el('div', { style: { display: 'flex', justifyContent: 'space-between', alignItems: 'center' } },
        el('span', { id: 'compose-upload-filename', style: { fontWeight: '600', fontSize: '13px' } }),
        el('span', { id: 'compose-upload-duration', style: { fontSize: '12px', color: 'var(--text-dim)' } }),
      ),
      el('button', { className: 'compose-ghost-btn', onClick: removeUploadedAudio }, 'Remove'),
    ),
  );

  // Approach selector
  const approachBtns = el('div', { className: 'compose-approach-grid' },
    el('button', { className: 'compose-approach-btn active', 'data-approach': 'cover',
      onClick: () => switchApproach('cover') }, 'Reimagine (full song)'),
    el('button', { className: 'compose-approach-btn', 'data-approach': 'repaint',
      onClick: () => switchApproach('repaint') }, 'Fix & Blend (selection only)'),
  );

  // Cover strength
  const coverGroup = el('div', { id: 'compose-cover-group' },
    el('div', { style: { display: 'flex', justifyContent: 'space-between' } },
      el('label', { className: 'compose-field-label' }, 'Reimagine strength'),
      el('span', { id: 'compose-cover-value', className: 'compose-value' }, '50%'),
    ),
    (() => {
      const s = el('input', { type: 'range', className: 'compose-slider', id: 'compose-cover-strength',
        min: '0', max: '100', value: '50', step: '1' });
      s.addEventListener('input', () => {
        _id('compose-cover-value').textContent = s.value + '%';
      });
      return s;
    })(),
  );

  // Region inputs (for repaint)
  const regionGroup = el('div', { className: 'hidden', id: 'compose-region-group' },
    el('span', { className: 'compose-label-sm' }, 'Region to fix'),
    el('div', { style: { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '6px' } },
      el('div', {},
        el('label', { className: 'compose-field-label' }, 'Start (s)'),
        el('input', { type: 'number', id: 'compose-region-start', className: 'compose-number', min: '0', step: '0.1', value: '0' }),
      ),
      el('div', {},
        el('label', { className: 'compose-field-label' }, 'End (s)'),
        el('input', { type: 'number', id: 'compose-region-end', className: 'compose-number', min: '0', step: '0.1', value: '0' }),
      ),
    ),
  );

  // Style direction (rework)
  const reworkDirection = el('div', { className: 'compose-field-group' },
    el('label', { className: 'compose-field-label' }, 'Style direction'),
    el('textarea', { id: 'compose-rework-direction', className: 'compose-textarea', rows: '3',
      placeholder: 'Describe the desired result\u2026 e.g. make it more jazzy with brass' }),
  );

  reworkPanel.append(
    uploadZone,
    el('div', { className: 'compose-divider' }),
    el('span', { className: 'compose-label-sm' }, 'Approach'),
    approachBtns, coverGroup, regionGroup,
    el('div', { className: 'compose-divider' }),
    reworkDirection,
  );

  // Wire upload zone drag/drop
  setupUploadDragDrop(uploadZone);

  // LEGO MODE panel
  const legoPanel = el('div', { className: 'compose-panel-inner hidden', id: 'compose-lego-panel' });

  // Lego upload zone
  const legoUploadZone = el('div', { className: 'compose-upload-zone', id: 'compose-lego-upload-zone' },
    el('div', { id: 'compose-lego-upload-prompt' },
      el('span', {}, '\u266B Drop audio here or '),
      el('button', { className: 'compose-ghost-btn', onClick: browseLegoAudio }, 'Browse'),
    ),
    el('div', { className: 'hidden', id: 'compose-lego-upload-loaded' },
      el('div', { style: { display: 'flex', justifyContent: 'space-between', alignItems: 'center' } },
        el('span', { id: 'compose-lego-upload-filename', style: { fontWeight: '600', fontSize: '13px' } }),
        el('span', { id: 'compose-lego-upload-duration', style: { fontSize: '12px', color: 'var(--text-dim)' } }),
      ),
      el('button', { className: 'compose-ghost-btn', onClick: removeLegoUploadedAudio }, 'Remove'),
    ),
  );

  // Lego track selector
  const legoTrackSelect = el('select', { id: 'compose-lego-track', className: 'compose-select',
    onChange: () => { _selectedLegoTrack = _id('compose-lego-track').value; } });
  for (const t of ACE_TRACKS) {
    legoTrackSelect.appendChild(el('option', { value: t }, t.replace('_', ' ')));
  }

  // Lego vocal hint
  const legoVocalHint = el('div', { className: 'banner banner-info', id: 'compose-lego-vocal-hint',
    style: { fontSize: '12px' } },
    'Vocal tracks generate AI vocal elements (melodic, not sung lyrics).',
  );

  // Lego style direction
  const legoDirection = el('div', { className: 'compose-field-group' },
    el('label', { className: 'compose-field-label' }, 'Style description'),
    el('textarea', { id: 'compose-lego-direction', className: 'compose-textarea', rows: '3',
      placeholder: 'Describe the replacement track\u2026 e.g. funky slap bass with groove' }),
  );

  // Lego waveform sections (source + result diff)
  const legoSourceWf = el('div', { className: 'analyze-wf-section hidden', id: 'lego-wf-source-section' },
    el('span', { className: 'analyze-wf-label' }, 'Source'),
    el('div', { className: 'analyze-wf-container', id: 'lego-wf-source' },
      el('canvas', { className: 'analyze-wf-canvas', id: 'lego-wf-source-canvas' }),
    ),
  );
  const legoResultWf = el('div', { className: 'analyze-wf-section hidden', id: 'lego-wf-result-section' },
    el('span', { className: 'analyze-wf-label' }, 'Result'),
    el('div', { className: 'analyze-wf-container', id: 'lego-wf-result' },
      el('canvas', { className: 'analyze-wf-canvas', id: 'lego-wf-result-canvas' }),
    ),
  );

  legoPanel.append(
    legoUploadZone,
    el('div', { className: 'compose-divider' }),
    el('div', { className: 'compose-field-group' },
      el('label', { className: 'compose-field-label' }, 'Track to replace'),
      legoTrackSelect,
    ),
    legoVocalHint,
    el('div', { className: 'compose-divider' }),
    legoDirection,
    el('div', { className: 'banner banner-info', style: { fontSize: '12px', marginTop: '8px' } },
      'Requires base model. Duration locked to source audio.'),
    legoSourceWf,
    legoResultWf,
  );

  setupUploadDragDrop(legoUploadZone, handleLegoAudioUpload);

  // Track select change: show/hide vocal hint
  legoTrackSelect.addEventListener('change', () => {
    _selectedLegoTrack = legoTrackSelect.value;
    legoVocalHint.classList.toggle('hidden', !legoTrackSelect.value.includes('vocal'));
  });

  // COMPLETE MODE panel
  const completePanel = el('div', { className: 'compose-panel-inner hidden', id: 'compose-complete-panel' });

  // Complete upload zone
  const completeUploadZone = el('div', { className: 'compose-upload-zone', id: 'compose-complete-upload-zone' },
    el('div', { id: 'compose-complete-upload-prompt' },
      el('span', {}, '\u266B Drop audio here or '),
      el('button', { className: 'compose-ghost-btn', onClick: browseCompleteAudio }, 'Browse'),
    ),
    el('div', { className: 'hidden', id: 'compose-complete-upload-loaded' },
      el('div', { style: { display: 'flex', justifyContent: 'space-between', alignItems: 'center' } },
        el('span', { id: 'compose-complete-upload-filename', style: { fontWeight: '600', fontSize: '13px' } }),
        el('span', { id: 'compose-complete-upload-duration', style: { fontSize: '12px', color: 'var(--text-dim)' } }),
      ),
      el('button', { className: 'compose-ghost-btn', onClick: removeCompleteUploadedAudio }, 'Remove'),
    ),
  );

  // Complete track class multi-select grid
  const completeTrackGrid = el('div', { className: 'compose-tag-grid', id: 'compose-complete-tracks' });
  for (const t of ACE_TRACKS) {
    completeTrackGrid.appendChild(el('button', {
      className: 'compose-tag',
      'data-track': t,
      onClick: (e) => {
        e.target.classList.toggle('active');
        _selectedCompleteTracks = [...document.querySelectorAll('#compose-complete-tracks .compose-tag.active')]
          .map(b => b.dataset.track);
      },
    }, t.replace('_', ' ')));
  }

  // Complete style direction
  const completeDirection = el('div', { className: 'compose-field-group' },
    el('label', { className: 'compose-field-label' }, 'Style description'),
    el('textarea', { id: 'compose-complete-direction', className: 'compose-textarea', rows: '3',
      placeholder: 'Describe the sound\u2026 e.g. orchestral with warm strings and brass' }),
  );

  // Complete waveform sections (source + result diff)
  const completeSourceWf = el('div', { className: 'analyze-wf-section hidden', id: 'complete-wf-source-section' },
    el('span', { className: 'analyze-wf-label' }, 'Source'),
    el('div', { className: 'analyze-wf-container', id: 'complete-wf-source' },
      el('canvas', { className: 'analyze-wf-canvas', id: 'complete-wf-source-canvas' }),
    ),
  );
  const completeResultWf = el('div', { className: 'analyze-wf-section hidden', id: 'complete-wf-result-section' },
    el('span', { className: 'analyze-wf-label' }, 'Result'),
    el('div', { className: 'analyze-wf-container', id: 'complete-wf-result' },
      el('canvas', { className: 'analyze-wf-canvas', id: 'complete-wf-result-canvas' }),
    ),
  );

  completePanel.append(
    completeUploadZone,
    el('div', { className: 'compose-divider' }),
    el('div', { className: 'compose-field-group' },
      el('label', { className: 'compose-field-label' }, 'Tracks to generate'),
      completeTrackGrid,
    ),
    el('div', { className: 'compose-divider' }),
    completeDirection,
    el('div', { className: 'banner banner-info', style: { fontSize: '12px', marginTop: '8px' } },
      'Requires base model. Duration locked to source audio.'),
    completeSourceWf,
    completeResultWf,
  );

  setupUploadDragDrop(completeUploadZone, handleCompleteAudioUpload);

  // VOICE MODE panel
  const voicePanel = el('div', { className: 'compose-panel-inner hidden', id: 'compose-voice-panel' });

  // Voice source selector — from separated stems
  const voiceStemSelect = el('select', { id: 'compose-voice-stem', className: 'compose-select',
    onChange: () => selectVoiceStem() });
  voiceStemSelect.appendChild(el('option', { value: '' }, 'Select a stem...'));

  const voiceFileBtn = el('button', { className: 'compose-ghost-btn', onClick: browseVoiceAudio }, 'Load file (works best with a clean voice stem)');

  const voiceSourceInfo = el('div', { className: 'hidden', id: 'compose-voice-source-info' },
    el('div', { style: { display: 'flex', justifyContent: 'space-between', alignItems: 'center' } },
      el('span', { id: 'compose-voice-source-name', style: { fontWeight: '600', fontSize: '13px' } }),
      el('span', { id: 'compose-voice-source-duration', style: { fontSize: '12px', color: 'var(--text-dim)' } }),
    ),
    el('button', { className: 'compose-ghost-btn', onClick: removeVoiceSource }, 'Remove'),
  );

  // Voice model selector
  const voiceModelSelect = el('select', { id: 'compose-voice-model', className: 'compose-select' });
  voiceModelSelect.appendChild(el('option', { value: '' }, 'Loading models...'));
  const voiceModelStatus = el('span', { id: 'compose-voice-model-status', className: 'compose-hint', style: { fontSize: '11px' } });

  // Pitch slider (-24 to +24)
  const voicePitchSlider = el('input', { type: 'range', className: 'compose-slider voice-pitch-slider',
    id: 'compose-voice-pitch', min: '-24', max: '24', value: '0', step: '1' });
  voicePitchSlider.addEventListener('input', () => {
    const v = Number(voicePitchSlider.value);
    _id('compose-voice-pitch-value').textContent = (v > 0 ? '+' : '') + v + ' st';
  });

  // F0 method
  const voiceF0Select = el('select', { id: 'compose-voice-f0', className: 'compose-select' },
    el('option', { value: 'rmvpe', selected: 'true' }, 'RMVPE (default)'),
    el('option', { value: 'crepe' }, 'CREPE'),
    el('option', { value: 'fcpe' }, 'FCPE'),
  );

  // Index rate (voice character)
  const voiceIndexSlider = el('input', { type: 'range', className: 'compose-slider',
    id: 'compose-voice-index', min: '0', max: '1', value: '0.3', step: '0.05' });
  voiceIndexSlider.addEventListener('input', () => {
    _id('compose-voice-index-value').textContent = Number(voiceIndexSlider.value).toFixed(2);
  });

  // Protect (consonant protection)
  const voiceProtectSlider = el('input', { type: 'range', className: 'compose-slider',
    id: 'compose-voice-protect', min: '0', max: '0.5', value: '0.33', step: '0.01' });
  voiceProtectSlider.addEventListener('input', () => {
    _id('compose-voice-protect-value').textContent = Number(voiceProtectSlider.value).toFixed(2);
  });

  // Voice result waveform (diff visualization)
  const voiceResultWf = el('div', { className: 'analyze-wf-section hidden', id: 'voice-wf-result-section' },
    el('span', { className: 'analyze-wf-label' }, 'Result'),
    el('div', { className: 'analyze-wf-container', id: 'voice-wf-result' },
      el('canvas', { className: 'analyze-wf-canvas', id: 'voice-wf-result-canvas' }),
    ),
  );

  // Source player card (wavesurfer)
  const voiceSourcePlayer = el('div', { className: 'hidden', id: 'compose-voice-source-player' });

  voicePanel.append(
    el('div', { className: 'compose-field-group' },
      el('label', { className: 'compose-field-label' }, 'Source audio'),
      voiceStemSelect,
      voiceFileBtn,
      voiceSourceInfo,
    ),
    voiceSourcePlayer,
    el('div', { className: 'compose-divider' }),
    el('div', { className: 'compose-field-group' },
      el('label', { className: 'compose-field-label' }, 'Voice model'),
      voiceModelSelect,
      voiceModelStatus,
      el('div', { className: 'voice-model-actions', style: { display: 'flex', gap: '6px', marginTop: '6px' } },
        el('button', { className: 'compose-ghost-btn', onClick: showVoiceModelImport }, 'Find more voices'),
        el('button', { className: 'compose-ghost-btn', onClick: browseVoiceModel }, 'Upload .pth'),
      ),
      el('div', { className: 'hidden', id: 'voice-model-import-row', style: { marginTop: '6px' } },
        el('div', { style: { display: 'flex', gap: '4px', marginBottom: '4px' } },
          el('input', { type: 'text', id: 'voice-model-search-input', className: 'compose-input',
            placeholder: 'Search voices (e.g. ariana, drake, morgan)',
            style: { fontSize: '12px', flex: '1' },
            onKeydown: (e) => { if (e.key === 'Enter') doVoiceModelSearch(); } }),
          el('button', { className: 'btn btn-sm', onClick: doVoiceModelSearch }, 'Search'),
        ),
        el('div', { id: 'voice-model-search-results', style: { maxHeight: '180px', overflowY: 'auto' } }),
        el('span', { id: 'voice-model-import-status', className: 'compose-hint', style: { fontSize: '11px' } }),
        el('button', { className: 'compose-ghost-btn', style: { marginTop: '4px', fontSize: '11px' },
          onClick: () => _id('voice-model-import-row')?.classList.add('hidden') }, 'Close'),
      ),
    ),
    el('div', { className: 'compose-divider' }),
    el('div', { className: 'compose-control-group' },
      el('div', { style: { display: 'flex', justifyContent: 'space-between', alignItems: 'center' } },
        el('label', { className: 'compose-field-label' }, 'Pitch shift'),
        el('span', { id: 'compose-voice-pitch-value', className: 'compose-value' }, '0 st'),
      ),
      voicePitchSlider,
    ),
    el('div', { className: 'compose-control-group' },
      el('label', { className: 'compose-field-label' }, 'F0 method'),
      voiceF0Select,
    ),
    el('div', { className: 'compose-control-group' },
      el('div', { style: { display: 'flex', justifyContent: 'space-between', alignItems: 'center' } },
        el('label', { className: 'compose-field-label' }, 'Voice character'),
        el('span', { id: 'compose-voice-index-value', className: 'compose-value' }, '0.30'),
      ),
      voiceIndexSlider,
    ),
    el('div', { className: 'compose-control-group' },
      el('div', { style: { display: 'flex', justifyContent: 'space-between', alignItems: 'center' } },
        el('label', { className: 'compose-field-label' }, 'Consonant protection'),
        el('span', { id: 'compose-voice-protect-value', className: 'compose-value' }, '0.33'),
      ),
      voiceProtectSlider,
    ),
    el('div', { className: 'compose-divider' }),
    el('button', { className: 'compose-generate-btn', id: 'compose-voice-transform-btn',
      onClick: handleVoiceGenerate }, '\u25B6 Transform Voice'),
    el('div', { className: 'compose-hint', id: 'compose-voice-hint' }),
    el('div', { id: 'compose-voice-result-container' }),
    voiceResultWf,
  );

  col.append(createPanel, reworkPanel, legoPanel, completePanel, voicePanel);
  return col;
}

// ─── Center Column (Lyrics) ──────────────────────────────────────────

function buildCenterColumn() {
  const col = el('div', { className: 'compose-col compose-col-center' });

  // My Lyrics tab
  const myLyrics = el('div', { className: 'compose-tab-content', id: 'compose-tab-my-lyrics' },
    el('div', { style: { display: 'flex', gap: '6px', alignItems: 'center', flexWrap: 'wrap' } },
      buildLanguageSelect('compose-lyrics-lang'),
      el('button', { className: 'compose-ghost-btn', onClick: loadLyricsFile }, 'Load file'),
      el('button', { className: 'compose-ghost-btn', onClick: () => {
        _id('compose-lyrics-text').value = '';
        updateLyricsCount();
      }}, 'Clear'),
    ),
    el('textarea', { id: 'compose-lyrics-text', className: 'compose-textarea compose-lyrics-area',
      placeholder: 'Write your lyrics here.\n\n[Verse 1]\nLines go here\n\n[Chorus]\nLines go here',
      spellcheck: 'true', onInput: () => { updateLyricsCount(); checkLyricsWarning(); } }),
    el('div', { className: 'compose-lyrics-meta' },
      el('span', { id: 'compose-lyrics-count', className: 'compose-lyrics-count' }, '0 lines \u00b7 0 chars'),
      el('span', { id: 'compose-lyrics-warning', className: 'compose-lyrics-warning hidden' }),
    ),
    el('div', { id: 'compose-my-lyrics-results', className: 'compose-results-area hidden' }),
  );

  // AI Lyrics tab
  const aiLyrics = el('div', { className: 'compose-tab-content hidden', id: 'compose-tab-ai-lyrics' },
    el('div', { style: { display: 'flex', gap: '6px', alignItems: 'center', flexWrap: 'wrap' } },
      buildLanguageSelect('compose-ai-lang'),
      el('span', { style: { fontSize: '11px', color: 'var(--text-dim)' } },
        'Language, BPM, key, and duration are sent as guidance.'),
    ),
    el('div', { className: 'compose-field-group' },
      el('label', { className: 'compose-field-label' }, 'Song description'),
      el('textarea', { id: 'compose-ai-description', className: 'compose-textarea', rows: '2',
        placeholder: 'Describe the mood, topic, style \u2014 e.g. "upbeat summer anthem about road trips"',
        spellcheck: 'true' }),
    ),
    el('div', { className: 'compose-field-group', style: { flex: '1', minHeight: '0' } },
      el('label', { className: 'compose-field-label' }, 'Generated lyrics'),
      el('textarea', { id: 'compose-ai-lyrics-display', className: 'compose-textarea compose-lyrics-area',
        readonly: 'true', placeholder: 'Lyrics will appear here after generation\u2026' }),
    ),
    el('div', { id: 'compose-ai-lyrics-results', className: 'compose-results-area hidden' }),
  );

  // Instrumental tab
  const instrumental = el('div', { className: 'compose-tab-content hidden', id: 'compose-tab-instrumental' },
    el('p', { style: { padding: '14px 0', fontSize: '13px', color: 'var(--text-dim)', textAlign: 'center', flex: '1' } },
      'No lyrics \u2014 AceStep will generate an instrumental track from your style settings.'),
    el('div', { id: 'compose-instrumental-results', className: 'compose-results-area hidden' }),
  );

  col.append(myLyrics, aiLyrics, instrumental);
  return col;
}

function buildLanguageSelect(id) {
  const langs = [
    ['en', 'EN'], ['zh', 'ZH'], ['ja', 'JA'], ['ko', 'KO'], ['es', 'ES'], ['fr', 'FR'],
    ['de', 'DE'], ['pt', 'PT'], ['it', 'IT'], ['ru', 'RU'], ['ar', 'AR'], ['hi', 'HI'],
  ];
  return el('select', { id, className: 'compose-select compose-select-narrow' },
    ...langs.map(([v, l]) => el('option', { value: v }, l)),
  );
}

// ─── Right Column (Controls) ────────────────────────────────────────

function buildRightColumn() {
  const col = el('div', { className: 'compose-col compose-col-right' });

  // Duration
  const durSlider = el('input', { type: 'range', className: 'compose-slider', id: 'compose-duration',
    min: '10', max: '600', value: '30', step: '5' });
  durSlider.addEventListener('input', () => {
    const v = Number(durSlider.value);
    const m = Math.floor(v / 60);
    const s = v % 60;
    _id('compose-duration-value').textContent = m > 0 ? (s > 0 ? `${m}m ${s}s` : `${m}m`) : `${v}s`;
    checkLyricsWarning();
  });

  const autoBtn = el('button', { className: 'compose-auto-btn', id: 'compose-auto-btn',
    onClick: toggleAutoDuration }, 'Auto');

  const durationGroup = el('div', { className: 'compose-control-group' },
    el('div', { style: { display: 'flex', justifyContent: 'space-between', alignItems: 'center' } },
      el('label', { className: 'compose-field-label' }, 'Duration'),
      el('div', { style: { display: 'flex', gap: '8px', alignItems: 'center' } },
        autoBtn,
        el('span', { id: 'compose-duration-value', className: 'compose-value' }, '30s'),
      ),
    ),
    durSlider,
  );

  // Lyric adherence
  const laSlider = el('input', { type: 'range', className: 'compose-slider', id: 'compose-lyric-adherence',
    min: '0', max: '2', value: '1', step: '1' });
  laSlider.addEventListener('input', () => {
    _id('compose-la-value').textContent = ['Little', 'Some', 'Strong'][Number(laSlider.value)];
    syncAdvancedFromFriendly();
  });

  // Creativity
  const crSlider = el('input', { type: 'range', className: 'compose-slider', id: 'compose-creativity',
    min: '0', max: '100', value: '50', step: '1' });
  crSlider.addEventListener('input', () => {
    _id('compose-cr-value').textContent = crSlider.value + '%';
  });

  // Quality
  const qSlider = el('input', { type: 'range', className: 'compose-slider', id: 'compose-quality',
    min: '0', max: '2', value: '1', step: '1' });
  qSlider.addEventListener('input', () => {
    _id('compose-q-value').textContent = ['Raw', 'Balanced', 'Polished'][Number(qSlider.value)];
    syncAdvancedFromFriendly();
  });

  // Generate button
  const genBtn = el('button', { className: 'compose-generate-btn', id: 'compose-generate-btn',
    onClick: handleGenerate }, _aceStepRunning ? '\u25B6 Generate' : '\u23FB Initialize');
  const genHint = el('div', { className: 'compose-hint', id: 'compose-hint' });

  // Advanced panel
  const advanced = buildAdvancedPanel();

  // Project save/load
  const projStatus = el('span', { id: 'compose-project-status', className: 'compose-project-status' });
  const projFileInput = el('input', { type: 'file', id: 'compose-project-file', accept: '.json', className: 'hidden' });
  const projSaveBtn = el('button', { className: 'compose-ghost-btn', type: 'button' }, 'Save Project');
  const projLoadBtn = el('button', { className: 'compose-ghost-btn', type: 'button' }, 'Load Project');
  projSaveBtn.addEventListener('click', _saveProject);
  projLoadBtn.addEventListener('click', () => projFileInput.click());
  projFileInput.addEventListener('change', _loadProject);
  const projectRow = el('div', { className: 'compose-project-row' }, projSaveBtn, projLoadBtn, projFileInput, projStatus);

  col.append(
    durationGroup,
    el('div', { className: 'compose-divider' }),
    buildSliderGroup('Lyrical influence', 'compose-la-value', 'Some', laSlider),
    buildSliderGroup('Creativity', 'compose-cr-value', '50%', crSlider),
    buildSliderGroup('Quality', 'compose-q-value', 'Balanced', qSlider),
    genBtn, genHint,
    advanced,
    el('div', { className: 'compose-divider' }),
    projectRow,
  );
  return col;
}

function buildSliderGroup(label, valueId, defaultVal, slider) {
  return el('div', { className: 'compose-control-group' },
    el('div', { style: { display: 'flex', justifyContent: 'space-between', alignItems: 'center' } },
      el('label', { className: 'compose-field-label' }, label),
      el('span', { id: valueId, className: 'compose-value' }, defaultVal),
    ),
    slider,
  );
}

function buildAdvancedPanel() {
  const details = el('details', { className: 'compose-advanced' });
  const summary = el('summary', { className: 'compose-advanced-toggle' }, 'Advanced');
  const content = el('div', { className: 'compose-advanced-content' });

  // Gen model
  content.appendChild(el('div', { className: 'compose-control-group' },
    el('label', { className: 'compose-field-label' }, 'Generation model'),
    el('select', { id: 'compose-gen-model', className: 'compose-select', onChange: updateBatchLimit },
      el('option', { value: 'turbo', selected: 'true' }, 'Turbo (default)'),
      el('option', { value: 'sft' }, 'High Quality'),
      el('option', { value: 'base' }, 'Base'),
    ),
  ));

  // LM model
  content.appendChild(el('div', { className: 'compose-control-group' },
    el('label', { className: 'compose-field-label' }, 'Planning intelligence'),
    el('select', { id: 'compose-lm-model', className: 'compose-select', onChange: updateBatchLimit },
      el('option', { value: 'none' }, 'None'),
      el('option', { value: '0.6b' }, 'Small'),
      el('option', { value: '1.7b', selected: 'true' }, 'Medium (default)'),
      el('option', { value: '4b' }, 'Large'),
    ),
  ));

  content.appendChild(el('div', { className: 'compose-divider' }));

  // VRAM tier
  content.appendChild(el('div', { className: 'compose-control-group' },
    el('label', { className: 'compose-field-label' }, 'VRAM tier'),
    el('select', { id: 'compose-vram-tier', className: 'compose-select', onChange: updateBatchLimit },
      el('option', { value: '16', selected: 'true' }, '\u226416GB (default)'),
      el('option', { value: '24' }, '24GB'),
      el('option', { value: '32' }, '32GB+'),
    ),
  ));

  // Batch size
  content.appendChild(el('div', { className: 'compose-control-group' },
    el('label', { className: 'compose-field-label' }, 'Batch size'),
    el('input', { type: 'number', id: 'compose-batch-size', className: 'compose-number', value: '1', min: '1', max: '2' }),
    el('p', { id: 'compose-batch-note', className: 'compose-batch-note hidden' }),
  ));

  // Audio format
  content.appendChild(el('div', { className: 'compose-control-group' },
    el('label', { className: 'compose-field-label' }, 'Audio format'),
    el('select', { id: 'compose-audio-format', className: 'compose-select' },
      el('option', { value: 'mp3', selected: 'true' }, 'MP3 (default)'),
      el('option', { value: 'wav' }, 'WAV'),
      el('option', { value: 'flac' }, 'FLAC'),
    ),
  ));

  content.appendChild(el('div', { className: 'compose-divider' }));

  // Seed
  const seedLastBtn = el('button', {
    className: 'compose-ghost-btn compose-seed-btn', id: 'compose-seed-last',
    type: 'button', disabled: 'true', title: 'Use last seed',
  }, 'Last');
  seedLastBtn.addEventListener('click', () => {
    if (_lastSeed != null) _id('compose-seed').value = _lastSeed;
  });
  const seedRandomBtn = el('button', {
    className: 'compose-ghost-btn compose-seed-btn', type: 'button', title: 'Set to random',
  }, 'Random');
  seedRandomBtn.addEventListener('click', () => { _id('compose-seed').value = ''; });

  content.appendChild(el('div', { className: 'compose-control-group' },
    el('label', { className: 'compose-field-label' }, 'Seed'),
    el('div', { className: 'compose-seed-row' },
      el('input', { type: 'number', id: 'compose-seed', className: 'compose-number', placeholder: 'Random', min: '0', max: '2147483647' }),
      seedLastBtn, seedRandomBtn,
    ),
  ));

  // Scheduler
  content.appendChild(el('div', { className: 'compose-control-group' },
    el('label', { className: 'compose-field-label' }, 'Scheduler'),
    el('select', { id: 'compose-scheduler', className: 'compose-select' },
      el('option', { value: 'euler' }, 'Euler'),
      el('option', { value: 'dpm' }, 'DPM++'),
      el('option', { value: 'ddim' }, 'DDIM'),
    ),
  ));

  // Inference steps
  const isSlider = el('input', { type: 'range', className: 'compose-slider', id: 'compose-inf-steps',
    min: '10', max: '150', value: '60', step: '5' });
  isSlider.addEventListener('input', () => {
    _id('compose-inf-steps-value').textContent = isSlider.value;
  });
  content.appendChild(el('div', { className: 'compose-control-group' },
    el('div', { style: { display: 'flex', justifyContent: 'space-between' } },
      el('label', { className: 'compose-field-label' }, 'Inference steps'),
      el('span', { id: 'compose-inf-steps-value', className: 'compose-value' }, '60'),
    ),
    isSlider,
  ));

  // Guidance scale (lyric)
  const glSlider = el('input', { type: 'range', className: 'compose-slider', id: 'compose-guidance-lyric',
    min: '1', max: '15', value: '7', step: '0.5' });
  glSlider.addEventListener('input', () => {
    _id('compose-gl-value').textContent = Number(glSlider.value).toFixed(1);
  });
  content.appendChild(el('div', { className: 'compose-control-group' },
    el('div', { style: { display: 'flex', justifyContent: 'space-between' } },
      el('label', { className: 'compose-field-label' }, 'Guidance scale (lyric)'),
      el('span', { id: 'compose-gl-value', className: 'compose-value' }, '7.0'),
    ),
    glSlider,
  ));

  // Guidance scale (audio)
  const gaSlider = el('input', { type: 'range', className: 'compose-slider', id: 'compose-guidance-audio',
    min: '1', max: '15', value: '4', step: '0.5' });
  gaSlider.addEventListener('input', () => {
    _id('compose-ga-value').textContent = Number(gaSlider.value).toFixed(1);
  });
  content.appendChild(el('div', { className: 'compose-control-group' },
    el('div', { style: { display: 'flex', justifyContent: 'space-between' } },
      el('label', { className: 'compose-field-label' }, 'Guidance scale (audio)'),
      el('span', { id: 'compose-ga-value', className: 'compose-value' }, '4.0'),
    ),
    gaSlider,
  ));

  content.appendChild(el('div', { className: 'compose-divider' }));

  // ─── Style Adapter (LoRA) ───
  const loraStatus = el('div', { id: 'compose-lora-status', className: 'compose-lora-status' }, 'No adapter loaded');
  const loraBrowser = el('select', { id: 'compose-lora-browser', className: 'compose-select' },
    el('option', { value: '' }, 'Select adapter\u2026'),
  );
  const loraLoadBtn = el('button', { className: 'compose-ghost-btn', id: 'compose-lora-load', type: 'button' }, 'Load');
  const loraUnloadBtn = el('button', { className: 'compose-ghost-btn hidden', id: 'compose-lora-unload', type: 'button' }, 'Unload');

  const loraScaleSlider = el('input', { type: 'range', className: 'compose-slider', id: 'compose-lora-scale',
    min: '0', max: '100', value: '100', step: '5' });
  const loraScaleValue = el('span', { id: 'compose-lora-scale-value', className: 'compose-value' }, '100%');
  loraScaleSlider.addEventListener('input', () => {
    loraScaleValue.textContent = loraScaleSlider.value + '%';
  });
  let _loraScaleTimer = null;
  loraScaleSlider.addEventListener('change', () => {
    clearTimeout(_loraScaleTimer);
    _loraScaleTimer = setTimeout(async () => {
      try {
        await api('/compose/lora/scale', { method: 'POST', body: JSON.stringify({ scale: Number(loraScaleSlider.value) / 100 }) });
      } catch (e) { console.error('LoRA scale error:', e); }
    }, 300);
  });

  const loraActiveControls = el('div', { id: 'compose-lora-active', className: 'compose-lora-active hidden' },
    el('div', { style: { display: 'flex', justifyContent: 'space-between' } },
      el('label', { className: 'compose-field-label' }, 'Style influence'),
      loraScaleValue,
    ),
    loraScaleSlider,
    el('div', { className: 'compose-slider-bounds' }, el('span', {}, 'Subtle'), el('span', {}, 'Full')),
  );

  loraLoadBtn.addEventListener('click', async () => {
    const path = loraBrowser.value;
    if (!path) return;
    loraLoadBtn.disabled = true;
    loraStatus.textContent = 'Loading\u2026';
    loraStatus.className = 'compose-lora-status';
    try {
      const result = await api('/compose/lora/load', { method: 'POST', body: JSON.stringify({ lora_path: path }) });
      const name = result.adapter_name || path.split('/').pop();
      loraStatus.textContent = name + ' loaded';
      loraStatus.className = 'compose-lora-status loaded';
      loraLoadBtn.classList.add('hidden');
      loraUnloadBtn.classList.remove('hidden');
      loraActiveControls.classList.remove('hidden');
    } catch (e) {
      loraStatus.textContent = 'Load failed: ' + e.message;
      loraStatus.className = 'compose-lora-status error';
    }
    loraLoadBtn.disabled = false;
  });

  loraUnloadBtn.addEventListener('click', async () => {
    try {
      await api('/compose/lora/unload', { method: 'POST' });
    } catch {}
    loraStatus.textContent = 'No adapter loaded';
    loraStatus.className = 'compose-lora-status';
    loraLoadBtn.classList.remove('hidden');
    loraUnloadBtn.classList.add('hidden');
    loraActiveControls.classList.add('hidden');
    loraScaleSlider.value = 100;
    loraScaleValue.textContent = '100%';
  });

  content.appendChild(el('div', { className: 'compose-control-group' },
    el('span', { className: 'compose-field-label' }, 'Style Adapter'),
    loraStatus,
    el('div', { className: 'compose-lora-controls' }, loraBrowser, loraLoadBtn, loraUnloadBtn),
    loraActiveControls,
  ));

  details.append(summary, content);

  // Refresh adapter list and status on open
  details.addEventListener('toggle', () => {
    if (details.open) { _refreshLoraBrowser(); _refreshLoraStatus(); }
  });

  return details;
}

// ─── LoRA helpers ────────────────────────────────────────────────────

async function _refreshLoraBrowser() {
  try {
    const data = await api('/compose/lora/browse');
    const select = _id('compose-lora-browser');
    if (!select) return;
    const prev = select.value;
    while (select.options.length > 1) select.remove(1);
    for (const a of data.adapters || []) {
      const label = `${a.name} (${a.type}, ${a.size_mb}MB)`;
      select.appendChild(el('option', { value: a.path }, label));
    }
    if (prev) {
      for (const opt of select.options) {
        if (opt.value === prev) { select.value = prev; break; }
      }
    }
  } catch {}
}

async function _refreshLoraStatus() {
  try {
    const data = await api('/compose/lora/status');
    const status = _id('compose-lora-status');
    const loadBtn = _id('compose-lora-load');
    const unloadBtn = _id('compose-lora-unload');
    const active = _id('compose-lora-active');
    const scaleSlider = _id('compose-lora-scale');
    const scaleVal = _id('compose-lora-scale-value');
    if (!status) return;

    if (data.lora_loaded) {
      const name = data.adapter_name || 'Adapter';
      status.textContent = name + ' loaded';
      status.className = 'compose-lora-status loaded';
      loadBtn.classList.add('hidden');
      unloadBtn.classList.remove('hidden');
      active.classList.remove('hidden');
      if (data.lora_scale != null) {
        const pct = Math.round(data.lora_scale * 100);
        scaleSlider.value = pct;
        scaleVal.textContent = pct + '%';
      }
    } else {
      status.textContent = 'No adapter loaded';
      status.className = 'compose-lora-status';
      loadBtn.classList.remove('hidden');
      unloadBtn.classList.add('hidden');
      active.classList.add('hidden');
    }
  } catch {}
}

// ─── Project Save/Load ──────────────────────────────────────────────

function _gatherProject() {
  const activeTags = [...document.querySelectorAll('#panel-compose .compose-tag.active')].map(t => t.textContent.trim());
  return {
    _version: 1,
    _saved: new Date().toISOString(),
    mode: _mode,
    createTab: _createTab,
    approach: _approach,
    lyrics: (_id('compose-lyrics-text') || {}).value || '',
    style: (_id('compose-style-text') || {}).value || '',
    tags: activeTags,
    bpm: (_id('compose-bpm') || {}).value || '',
    keyRoot: (_id('compose-key-root') || {}).value || '',
    keyMode: (_id('compose-key-mode') || {}).value || 'major',
    timeSig: (_id('compose-time-sig') || {}).value || '4/4',
    duration: (_id('compose-duration') || {}).value || '30',
    lyricAdherence: (_id('compose-lyric-adherence') || {}).value || '1',
    creativity: (_id('compose-creativity') || {}).value || '50',
    quality: (_id('compose-quality') || {}).value || '1',
    genModel: (_id('compose-gen-model') || {}).value || 'turbo',
    lmModel: (_id('compose-lm-model') || {}).value || '1.7b',
    batchSize: (_id('compose-batch-size') || {}).value || '1',
    vramTier: (_id('compose-vram-tier') || {}).value || '16',
    scheduler: (_id('compose-scheduler') || {}).value || 'euler',
    audioFormat: (_id('compose-audio-format') || {}).value || 'mp3',
    guidanceLyric: (_id('compose-guidance-lyric') || {}).value || '7',
    guidanceAudio: (_id('compose-guidance-audio') || {}).value || '4',
    inferenceSteps: (_id('compose-inf-steps') || {}).value || '60',
    seed: (_id('compose-seed') || {}).value || '',
    loraPath: (_id('compose-lora-browser') || {}).value || '',
    loraScale: (_id('compose-lora-scale') || {}).value || '100',
    aiDescription: (_id('compose-ai-description') || {}).value || '',
    aiLanguage: (_id('compose-ai-lang') || {}).value || 'en',
    reworkDirection: (_id('compose-rework-direction') || {}).value || '',
    lastSeed: _lastSeed,
  };
}

function _setSliderValue(id, value) {
  const el = _id(id);
  if (el && value != null) {
    el.value = value;
    el.dispatchEvent(new Event('input'));
  }
}

function _applyProject(proj) {
  // Lyrics & style
  const lyrics = _id('compose-lyrics-text');
  if (lyrics) lyrics.value = proj.lyrics || '';
  const style = _id('compose-style-text');
  if (style) style.value = proj.style || '';

  // Tags
  document.querySelectorAll('#panel-compose .compose-tag').forEach(t => {
    t.classList.toggle('active', (proj.tags || []).includes(t.textContent.trim()));
  });

  // Song params
  const bpm = _id('compose-bpm'); if (bpm) bpm.value = proj.bpm || '';
  const keyRoot = _id('compose-key-root'); if (keyRoot) keyRoot.value = proj.keyRoot || '';
  const keyMode = _id('compose-key-mode'); if (keyMode) keyMode.value = proj.keyMode || 'major';
  const timeSig = _id('compose-time-sig'); if (timeSig) timeSig.value = proj.timeSig || '4/4';

  // Main sliders
  _setSliderValue('compose-duration', proj.duration);
  _setSliderValue('compose-lyric-adherence', proj.lyricAdherence);
  _setSliderValue('compose-creativity', proj.creativity);
  _setSliderValue('compose-quality', proj.quality);

  // Advanced — model selects
  const genModel = _id('compose-gen-model'); if (genModel && proj.genModel) genModel.value = proj.genModel;
  const lmModel = _id('compose-lm-model'); if (lmModel && proj.lmModel) lmModel.value = proj.lmModel;
  const batchSize = _id('compose-batch-size'); if (batchSize && proj.batchSize) batchSize.value = proj.batchSize;
  const vramTier = _id('compose-vram-tier'); if (vramTier && proj.vramTier) vramTier.value = proj.vramTier;
  const scheduler = _id('compose-scheduler'); if (scheduler && proj.scheduler) scheduler.value = proj.scheduler;
  const audioFmt = _id('compose-audio-format'); if (audioFmt && proj.audioFormat) audioFmt.value = proj.audioFormat;

  // Advanced — raw sliders
  _setSliderValue('compose-guidance-lyric', proj.guidanceLyric);
  _setSliderValue('compose-guidance-audio', proj.guidanceAudio);
  _setSliderValue('compose-inf-steps', proj.inferenceSteps);

  // Seed
  const seedEl = _id('compose-seed'); if (seedEl) seedEl.value = proj.seed || '';

  // LoRA
  if (proj.loraPath) {
    const browser = _id('compose-lora-browser');
    if (browser) {
      for (const opt of browser.options) {
        if (opt.value === proj.loraPath) { browser.value = proj.loraPath; break; }
      }
    }
  }
  _setSliderValue('compose-lora-scale', proj.loraScale);

  // AI lyrics
  const aiDesc = _id('compose-ai-description'); if (aiDesc && proj.aiDescription != null) aiDesc.value = proj.aiDescription;
  const aiLang = _id('compose-ai-lang'); if (aiLang && proj.aiLanguage) aiLang.value = proj.aiLanguage;

  // Rework
  const reworkDir = _id('compose-rework-direction'); if (reworkDir && proj.reworkDirection != null) reworkDir.value = proj.reworkDirection;

  // Last seed recall
  if (proj.lastSeed != null) {
    _lastSeed = proj.lastSeed;
    const btn = _id('compose-seed-last');
    if (btn) { btn.disabled = false; btn.title = 'Use last seed: ' + proj.lastSeed; }
  }
}

function _saveProject() {
  const proj = _gatherProject();
  const name = (proj.style || proj.tags?.[0] || 'song-project').replace(/[^a-zA-Z0-9_-]/g, '_').slice(0, 40);
  const blob = new Blob([JSON.stringify(proj, null, 2)], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `${name}.json`;
  a.click();
  URL.revokeObjectURL(a.href);
  const status = _id('compose-project-status');
  if (status) { status.textContent = 'Saved'; setTimeout(() => { status.textContent = ''; }, 2000); }
}

async function _loadProject() {
  const input = _id('compose-project-file');
  const file = input?.files?.[0];
  if (input) input.value = '';
  if (!file) return;
  const status = _id('compose-project-status');
  try {
    const text = await file.text();
    const proj = JSON.parse(text);
    if (!proj._version) throw new Error('Not a valid project file');
    _applyProject(proj);
    if (status) { status.textContent = 'Loaded: ' + file.name; setTimeout(() => { status.textContent = ''; }, 3000); }
  } catch {
    if (status) { status.textContent = 'Invalid project file'; setTimeout(() => { status.textContent = ''; }, 3000); }
  }
}

// ─── Training UI ────────────────────────────────────────────────────

// Training state
let _trainFiles = [];
let _trainScanned = false;
let _trainLabeled = false;
let _trainPreprocessed = false;
let _trainPollTimer = null;

function buildTrainLeftPanel() {
  const col = el('div', { className: 'compose-col compose-train-col-left', id: 'compose-train-left' });

  col.appendChild(el('h3', { className: 'compose-section-title' }, 'Training Dataset'));

  // Upload zone
  const fileInput = el('input', { type: 'file', id: 'compose-train-file-input', accept: 'audio/*', multiple: 'true', className: 'hidden' });
  const browseBtn = el('button', { className: 'compose-ghost-btn', type: 'button' }, 'Browse audio files');
  browseBtn.addEventListener('click', () => fileInput.click());

  const uploadZone = el('div', { className: 'compose-train-upload', id: 'compose-train-upload' },
    el('p', { className: 'text-dim' }, 'Drop audio files here or'),
    browseBtn, fileInput,
  );

  // Drag and drop
  uploadZone.addEventListener('dragover', (e) => { e.preventDefault(); uploadZone.classList.add('dragover'); });
  uploadZone.addEventListener('dragleave', () => uploadZone.classList.remove('dragover'));
  uploadZone.addEventListener('drop', async (e) => {
    e.preventDefault();
    uploadZone.classList.remove('dragover');
    const files = [...e.dataTransfer.files].filter(f => f.type.startsWith('audio/'));
    if (files.length) await _uploadTrainFiles(files);
  });

  fileInput.addEventListener('change', async () => {
    if (fileInput.files.length) await _uploadTrainFiles([...fileInput.files]);
    fileInput.value = '';
  });

  // File list
  const fileList = el('div', { className: 'compose-train-file-list hidden', id: 'compose-train-file-list' });
  const clearBtn = el('button', { className: 'compose-ghost-btn', type: 'button' }, 'Clear');
  clearBtn.addEventListener('click', _clearTrainFiles);

  // Stems mode
  const stemsMode = el('label', { className: 'compose-train-stems-label' },
    el('input', { type: 'checkbox', id: 'compose-train-stems-mode' }),
    ' Training data is stems (vocal only)',
  );

  // Pipeline buttons
  const scanBtn = el('button', { className: 'compose-ghost-btn', id: 'compose-train-scan', disabled: 'true' }, '1. Scan');
  const labelBtn = el('button', { className: 'compose-ghost-btn', id: 'compose-train-label', disabled: 'true' }, '2. Auto-label');
  const preprocessBtn = el('button', { className: 'compose-ghost-btn', id: 'compose-train-preprocess', disabled: 'true' }, '3. Preprocess');
  scanBtn.addEventListener('click', _trainScan);
  labelBtn.addEventListener('click', _trainLabel);
  preprocessBtn.addEventListener('click', _trainPreprocess);

  const pipelineBtns = el('div', { className: 'compose-train-pipeline' }, scanBtn, labelBtn, preprocessBtn);
  const pipelineStatus = el('div', { className: 'compose-train-pipeline-status', id: 'compose-train-pipeline-status' });

  // Label progress
  const labelProgress = el('div', { className: 'compose-train-label-progress hidden', id: 'compose-train-label-progress' },
    el('div', { className: 'progress-bar' }, el('div', { className: 'progress-fill', id: 'compose-train-label-fill' })),
    el('span', { id: 'compose-train-label-pct' }, '0%'),
  );

  // Label model selector
  const labelModelSel = el('select', { id: 'compose-train-label-model', className: 'compose-select', style: { marginTop: '8px' } },
    el('option', { value: '' }, 'Default (startup model)'),
    el('option', { value: 'acestep-5Hz-lm-0.6B' }, 'Small (0.6B)'),
    el('option', { value: 'acestep-5Hz-lm-1.7B' }, 'Medium (1.7B)'),
    el('option', { value: 'acestep-5Hz-lm-4B' }, 'Large (4B) \u2014 32GB+ VRAM'),
  );

  col.append(uploadZone, fileList, el('div', { style: { display: 'flex', gap: '8px', marginTop: '8px' } }, clearBtn),
    stemsMode,
    el('div', { className: 'compose-control-group', style: { marginTop: '8px' } },
      el('label', { className: 'compose-field-label' }, 'Labeling model'), labelModelSel),
    pipelineBtns, pipelineStatus, labelProgress);

  return col;
}

function buildTrainCenterPanel() {
  const col = el('div', { className: 'compose-col compose-train-col-center', id: 'compose-train-center' });

  // Sample table
  const datasetView = el('div', { className: 'compose-train-dataset hidden', id: 'compose-train-dataset' });
  const sampleTable = el('div', { id: 'compose-train-samples' },
    el('div', { className: 'compose-train-sample-header' },
      el('span', {}, 'File'), el('span', {}, 'Dur'), el('span', {}, 'Caption'),
    ),
  );
  const sampleCounts = el('div', { className: 'compose-train-counts' },
    el('span', { id: 'compose-train-sample-count' }, '0 samples'),
    el('span', {}, ' \u00B7 '),
    el('span', { id: 'compose-train-labeled-count' }, '0 labeled'),
  );
  datasetView.append(sampleCounts, sampleTable);

  // Snapshots
  const snapshotSection = el('div', { className: 'compose-train-snapshots', style: { marginTop: '16px' } },
    el('div', { className: 'compose-train-snapshot-save-row' },
      el('input', { type: 'text', id: 'compose-train-snapshot-name', className: 'compose-input', placeholder: 'Snapshot name', maxlength: '64' }),
      el('button', { className: 'compose-ghost-btn', id: 'compose-train-snapshot-save', disabled: 'true', onClick: _saveSnapshot }, 'Save snapshot'),
    ),
    el('div', { id: 'compose-train-snapshot-list', className: 'hidden' }),
  );

  // Training monitor
  const monitor = el('div', { className: 'compose-train-monitor', id: 'compose-train-monitor' },
    el('div', { className: 'compose-train-status-header' },
      el('span', { id: 'compose-train-status-label' }, 'Idle'),
      el('span', { id: 'compose-train-epoch-info' }),
    ),
    el('div', { className: 'compose-train-loss hidden', id: 'compose-train-loss' },
      el('div', { className: 'compose-train-loss-current' },
        el('span', {}, 'Loss'), el('span', { id: 'compose-train-loss-value' }, '--'),
      ),
      el('div', { className: 'compose-train-loss-bar' },
        el('div', { id: 'compose-train-loss-fill', className: 'compose-train-loss-fill' }),
      ),
    ),
    el('div', { className: 'compose-train-chart-wrap hidden', id: 'compose-train-chart-wrap' },
      el('canvas', { id: 'compose-train-loss-chart', width: '600', height: '180' }),
    ),
    el('div', { className: 'compose-train-progress hidden', id: 'compose-train-progress' },
      el('div', { className: 'progress-bar' }, el('div', { className: 'progress-fill', id: 'compose-train-progress-fill' })),
      el('span', { id: 'compose-train-progress-pct' }, '0%'),
    ),
    el('div', { id: 'compose-train-log' },
      el('p', { className: 'text-dim' }, 'Configure training in the right panel, then start.'),
    ),
    el('div', { className: 'compose-train-complete hidden', id: 'compose-train-complete' },
      el('button', { className: 'compose-ghost-btn', id: 'compose-train-export', onClick: _trainExport }, 'Export to loras/'),
      el('button', { className: 'compose-ghost-btn', id: 'compose-train-reinit', onClick: _trainReinit }, 'Restore generation model'),
    ),
  );

  col.append(datasetView, snapshotSection, monitor);
  return col;
}

function buildTrainRightPanel() {
  const col = el('div', { className: 'compose-col compose-train-col-right', id: 'compose-train-right' });

  col.appendChild(el('h3', { className: 'compose-section-title' }, 'Training Config'));

  // Adapter type
  col.appendChild(el('div', { className: 'compose-control-group' },
    el('label', { className: 'compose-field-label' }, 'Adapter type'),
    el('select', { id: 'compose-train-adapter', className: 'compose-select' },
      el('option', { value: 'lora', selected: 'true' }, 'LoRA'),
      el('option', { value: 'lokr' }, 'LoKR'),
    ),
  ));

  // Rank
  col.appendChild(el('div', { className: 'compose-control-group' },
    el('label', { className: 'compose-field-label' }, 'Rank'),
    el('input', { type: 'number', id: 'compose-train-rank', className: 'compose-number', value: '64', min: '1', max: '256' }),
  ));

  // Epochs
  col.appendChild(el('div', { className: 'compose-control-group' },
    el('label', { className: 'compose-field-label' }, 'Epochs'),
    el('input', { type: 'number', id: 'compose-train-epochs', className: 'compose-number', value: '10', min: '1', max: '1000' }),
  ));

  // Learning rate
  col.appendChild(el('div', { className: 'compose-control-group' },
    el('label', { className: 'compose-field-label' }, 'Learning rate'),
    el('input', { type: 'number', id: 'compose-train-lr', className: 'compose-number', value: '0.0001', min: '0', max: '1', step: '0.00001' }),
  ));

  // Advanced
  const advDetails = el('details', { className: 'compose-advanced' });
  const advContent = el('div', { className: 'compose-advanced-content' });

  advContent.append(
    el('div', { className: 'compose-control-group' },
      el('label', { className: 'compose-field-label' }, 'Alpha'),
      el('input', { type: 'number', id: 'compose-train-alpha', className: 'compose-number', value: '128', min: '1', max: '512' }),
    ),
    el('div', { className: 'compose-control-group' },
      el('label', { className: 'compose-field-label' }, 'Dropout'),
      el('input', { type: 'number', id: 'compose-train-dropout', className: 'compose-number', value: '0.1', min: '0', max: '1', step: '0.05' }),
    ),
    el('div', { className: 'compose-control-group' },
      el('label', { className: 'compose-field-label' }, 'Batch size'),
      el('input', { type: 'number', id: 'compose-train-batch', className: 'compose-number', value: '1', min: '1', max: '8' }),
    ),
    el('div', { className: 'compose-control-group' },
      el('label', { className: 'compose-field-label' }, 'Gradient accumulation'),
      el('input', { type: 'number', id: 'compose-train-grad-accum', className: 'compose-number', value: '4', min: '1', max: '64' }),
    ),
    el('div', { className: 'compose-control-group' },
      el('label', { className: 'compose-field-label' }, 'Save every N epochs'),
      el('input', { type: 'number', id: 'compose-train-save-every', className: 'compose-number', value: '5', min: '1' }),
    ),
    el('div', { className: 'compose-control-group' },
      el('label', { className: 'compose-field-label' }, 'Seed'),
      el('input', { type: 'number', id: 'compose-train-seed', className: 'compose-number', value: '42', min: '0' }),
    ),
    el('label', { style: { display: 'flex', gap: '6px', alignItems: 'center', marginTop: '8px' } },
      el('input', { type: 'checkbox', id: 'compose-train-grad-ckpt', checked: 'true' }),
      'Gradient checkpointing',
    ),
  );

  advDetails.append(el('summary', { className: 'compose-advanced-toggle' }, 'Advanced'), advContent);
  col.appendChild(advDetails);

  // Start/Stop
  const startBtn = el('button', { className: 'compose-generate-btn', id: 'compose-train-start', disabled: 'true', onClick: _trainStart }, 'Start Training');
  const stopBtn = el('button', { className: 'compose-ghost-btn compose-train-stop hidden', id: 'compose-train-stop', onClick: _trainStop }, 'Stop Training');
  col.append(startBtn, stopBtn);

  return col;
}

// ─── Training pipeline logic ────────────────────────────────────────

async function _uploadTrainFiles(files) {
  const status = _id('compose-train-pipeline-status');
  if (status) status.textContent = 'Uploading...';
  const form = new FormData();
  for (const f of files) form.append('files', f);
  try {
    const res = await fetch('/api/compose/train/upload', { method: 'POST', body: form });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);
    const data = await res.json();
    _trainFiles = data.files || [];
    _updateTrainFileList();
    if (status) status.textContent = `${data.uploaded.length} uploaded, ${data.skipped.length} skipped`;
    _id('compose-train-scan').disabled = _trainFiles.length === 0;
  } catch (e) {
    if (status) status.textContent = 'Upload failed: ' + e.message;
  }
}

function _updateTrainFileList() {
  const list = _id('compose-train-file-list');
  if (!list) return;
  clearChildren(list);
  if (_trainFiles.length === 0) { list.classList.add('hidden'); return; }
  list.classList.remove('hidden');
  list.appendChild(el('div', { className: 'text-dim', style: { marginBottom: '4px' } }, `${_trainFiles.length} file(s)`));
  for (const f of _trainFiles) {
    list.appendChild(el('div', { className: 'compose-train-file-entry' }, f));
  }
}

async function _clearTrainFiles() {
  try {
    await api('/compose/train/clear', { method: 'POST' });
  } catch {}
  _trainFiles = [];
  _trainScanned = false;
  _trainLabeled = false;
  _trainPreprocessed = false;
  _updateTrainFileList();
  _id('compose-train-scan').disabled = true;
  _id('compose-train-label').disabled = true;
  _id('compose-train-preprocess').disabled = true;
  _id('compose-train-start').disabled = true;
  const ds = _id('compose-train-dataset');
  if (ds) ds.classList.add('hidden');
  const status = _id('compose-train-pipeline-status');
  if (status) status.textContent = '';
}

async function _trainScan() {
  const status = _id('compose-train-pipeline-status');
  const stemsMode = _id('compose-train-stems-mode')?.checked || false;
  // Ensure AceStep is running before first scan
  if (!_aceStepRunning) {
    if (status) status.textContent = 'Starting AceStep\u2026';
    try {
      await ensureAceStep();
      _aceStepRunning = true;
      // Update the generate button label now that AceStep is running
      const btn = _id('compose-generate-btn');
      if (btn && !btn.disabled) btn.textContent = _modeLabel();
    } catch (err) {
      if (status) status.textContent = 'AceStep: ' + err.message;
      return;
    }
  }
  if (status) status.textContent = 'Scanning...';
  try {
    await api('/compose/train/scan', { method: 'POST', body: JSON.stringify({ stems_mode: stemsMode }) });
    _trainScanned = true;
    await _fetchSamples();
    _id('compose-train-label').disabled = false;
    if (status) status.textContent = 'Scan complete';
  } catch (e) {
    if (status) status.textContent = 'Scan failed: ' + e.message;
  }
}

async function _trainLabel() {
  const status = _id('compose-train-pipeline-status');
  const lmModel = _id('compose-train-label-model')?.value || '';
  const stemsMode = _id('compose-train-stems-mode')?.checked || false;
  const progress = _id('compose-train-label-progress');
  if (status) status.textContent = 'Auto-labeling...';
  if (progress) progress.classList.remove('hidden');
  try {
    await api('/compose/train/label', { method: 'POST', body: JSON.stringify({ lm_model_path: lmModel, stems_mode: stemsMode }) });
    // Poll label status
    const timer = setInterval(async () => {
      try {
        const data = await api('/compose/train/label/status');
        const current = data.current ?? 0;
        const total = data.total ?? 1;
        const pct = total > 0 ? current / total : 0;
        const fill = _id('compose-train-label-fill');
        const pctEl = _id('compose-train-label-pct');
        if (fill) fill.style.width = `${Math.round(pct * 100)}%`;
        if (pctEl) pctEl.textContent = `${Math.round(pct * 100)}%`;
        // Update samples live
        await _fetchSamples();
        if (data.status === 'completed' || data.status === 'idle') {
          clearInterval(timer);
          _trainLabeled = true;
          if (progress) progress.classList.add('hidden');
          _id('compose-train-preprocess').disabled = false;
          _id('compose-train-snapshot-save').disabled = false;
          if (status) status.textContent = 'Labeling complete';
          await api('/compose/train/save', { method: 'POST' });
        } else if (data.status === 'failed') {
          clearInterval(timer);
          if (progress) progress.classList.add('hidden');
          if (status) status.textContent = 'Labeling failed: ' + (data.error || 'unknown error');
        }
      } catch {
        clearInterval(timer);
        if (progress) progress.classList.add('hidden');
      }
    }, 10000);
  } catch (e) {
    if (progress) progress.classList.add('hidden');
    if (status) status.textContent = 'Label failed: ' + e.message;
  }
}

async function _trainPreprocess() {
  const status = _id('compose-train-pipeline-status');
  const progress = _id('compose-train-progress');
  if (status) status.textContent = 'Preprocessing...';
  if (progress) progress.classList.remove('hidden');
  try {
    const result = await api('/compose/train/preprocess', { method: 'POST' });
    const taskId = result.task_id;
    const timer = setInterval(async () => {
      try {
        const data = await api(`/compose/train/preprocess/status${taskId ? '?task_id=' + taskId : ''}`);
        const current = data.current ?? 0;
        const total = data.total ?? 1;
        const pct = total > 0 ? current / total : 0;
        const fill = _id('compose-train-progress-fill');
        const pctEl = _id('compose-train-progress-pct');
        if (fill) fill.style.width = `${Math.round(pct * 100)}%`;
        if (pctEl) pctEl.textContent = `${Math.round(pct * 100)}%`;
        if (data.status === 'completed' || data.status === 'idle') {
          clearInterval(timer);
          _trainPreprocessed = true;
          if (progress) progress.classList.add('hidden');
          _id('compose-train-start').disabled = false;
          _id('compose-train-snapshot-save').disabled = false;
          if (status) status.textContent = 'Preprocessing complete';
          await api('/compose/train/save', { method: 'POST' });
        } else if (data.status === 'failed') {
          clearInterval(timer);
          if (progress) progress.classList.add('hidden');
          if (status) status.textContent = 'Preprocessing failed: ' + (data.error || 'unknown error');
        }
      } catch {
        clearInterval(timer);
        if (progress) progress.classList.add('hidden');
      }
    }, 10000);
  } catch (e) {
    if (progress) progress.classList.add('hidden');
    if (status) status.textContent = 'Preprocess failed: ' + e.message;
  }
}

async function _fetchSamples() {
  try {
    const data = await api('/compose/train/samples');
    const samples = data.samples || [];
    _renderSampleTable(samples);
    const ds = _id('compose-train-dataset');
    if (ds && samples.length > 0) ds.classList.remove('hidden');
  } catch {}
}

function _renderSampleTable(samples) {
  const container = _id('compose-train-samples');
  if (!container) return;
  // Keep header, remove rows
  while (container.children.length > 1) container.removeChild(container.lastChild);

  let labeledCount = 0;
  samples.forEach((s, i) => {
    const hasCaption = !!(s.caption || s.genre);
    if (hasCaption) labeledCount++;
    const captionArea = el('textarea', {
      className: 'compose-train-caption',
      value: s.caption || '',
    });
    captionArea.value = s.caption || '';
    captionArea.addEventListener('blur', async () => {
      try {
        await api(`/compose/train/sample/${i}`, {
          method: 'PUT',
          body: JSON.stringify({ caption: captionArea.value }),
        });
      } catch {}
    });
    const filename = s.filename || (s.audio_path || s.file || s.path || '').split('/').pop() || `Sample ${i}`;
    const dur = s.duration ? `${Math.floor(s.duration / 60)}:${String(Math.floor(s.duration % 60)).padStart(2, '0')}` : '--';
    const row = el('div', { className: 'compose-train-sample-row' + (hasCaption ? ' labeled' : '') },
      el('span', { className: 'compose-train-sample-file' }, filename),
      el('span', { className: 'compose-train-sample-dur' }, dur),
      captionArea,
    );
    container.appendChild(row);
  });

  const countEl = _id('compose-train-sample-count');
  if (countEl) countEl.textContent = `${samples.length} samples`;
  const labelEl = _id('compose-train-labeled-count');
  if (labelEl) labelEl.textContent = `${labeledCount} labeled`;
}

// ─── Training control ───────────────────────────────────────────────

async function _trainStart() {
  const status = _id('compose-train-status-label');
  const loss = _id('compose-train-loss');
  const progress = _id('compose-train-progress');
  const chart = _id('compose-train-chart-wrap');
  const startBtn = _id('compose-train-start');
  const stopBtn = _id('compose-train-stop');

  const payload = {
    adapter_type: (_id('compose-train-adapter') || {}).value || 'lora',
    lora_rank: Number((_id('compose-train-rank') || {}).value || 64),
    lora_alpha: Number((_id('compose-train-alpha') || {}).value || 128),
    lora_dropout: Number((_id('compose-train-dropout') || {}).value || 0.1),
    learning_rate: Number((_id('compose-train-lr') || {}).value || 0.0001),
    train_epochs: Number((_id('compose-train-epochs') || {}).value || 10),
    train_batch_size: Number((_id('compose-train-batch') || {}).value || 1),
    gradient_accumulation: Number((_id('compose-train-grad-accum') || {}).value || 4),
    save_every_n_epochs: Number((_id('compose-train-save-every') || {}).value || 5),
    training_seed: Number((_id('compose-train-seed') || {}).value || 42),
    gradient_checkpointing: !!_id('compose-train-grad-ckpt')?.checked,
  };

  if (status) status.textContent = 'Training...';
  if (loss) loss.classList.remove('hidden');
  if (progress) progress.classList.remove('hidden');
  if (chart) chart.classList.remove('hidden');
  if (startBtn) startBtn.disabled = true;
  if (stopBtn) stopBtn.classList.remove('hidden');

  try {
    await api('/compose/train/start', { method: 'POST', body: JSON.stringify(payload) });
    _startTrainStatusPoll();
  } catch (e) {
    if (status) status.textContent = 'Start failed: ' + e.message;
    if (startBtn) startBtn.disabled = false;
    if (stopBtn) stopBtn.classList.add('hidden');
  }
}

async function _trainStop() {
  try { await api('/compose/train/stop', { method: 'POST' }); } catch {}
  const status = _id('compose-train-status-label');
  if (status) status.textContent = 'Stopped';
  _id('compose-train-stop')?.classList.add('hidden');
  _id('compose-train-start') && (_id('compose-train-start').disabled = false);
}

async function _trainExport() {
  const name = prompt('Adapter name:', 'my-lora');
  if (!name) return;
  const status = _id('compose-train-status-label');
  try {
    await api('/compose/train/export', { method: 'POST', body: JSON.stringify({ name }) });
    if (status) status.textContent = 'Exported: ' + name;
    _refreshLoraBrowser();
  } catch (e) {
    if (status) status.textContent = 'Export failed: ' + e.message;
  }
}

async function _trainReinit() {
  const status = _id('compose-train-status-label');
  try {
    await api('/compose/train/reinitialize', { method: 'POST' });
    if (status) status.textContent = 'Generation model restored';
  } catch (e) {
    if (status) status.textContent = 'Reinit failed: ' + e.message;
  }
}

// ─── Training status polling + loss chart ────────────────────────────

function _startTrainStatusPoll() {
  _stopTrainStatusPoll();
  _trainPollTimer = setInterval(_pollTrainStatus, 10000);
}

function _stopTrainStatusPoll() {
  if (_trainPollTimer) { clearInterval(_trainPollTimer); _trainPollTimer = null; }
}

async function _pollTrainStatus() {
  try {
    const data = await api('/compose/train/status');
    const status = _id('compose-train-status-label');
    const epochInfo = _id('compose-train-epoch-info');
    const lossValue = _id('compose-train-loss-value');
    const lossFill = _id('compose-train-loss-fill');
    const progressFill = _id('compose-train-progress-fill');
    const progressPct = _id('compose-train-progress-pct');

    if (data.is_training) {
      if (status) status.textContent = 'Training...';
      if (epochInfo && data.current_epoch != null) epochInfo.textContent = `Epoch ${data.current_epoch}`;
      if (data.current_loss != null) {
        const loss = Number(data.current_loss).toFixed(4);
        if (lossValue) lossValue.textContent = loss;
        const bar = Math.max(0, Math.min(1, data.current_loss / 2));
        if (lossFill) lossFill.style.width = `${(1 - bar) * 100}%`;
      }
      if (data.loss_history) _drawLossChart(data.loss_history);
      if (data.progress != null) {
        const pct = Math.round(data.progress * 100);
        if (progressFill) progressFill.style.width = `${pct}%`;
        if (progressPct) progressPct.textContent = `${pct}%`;
      }
    } else if (data.status === 'done' || data.status === 'completed' || (!data.is_training && data.current_epoch > 0)) {
      _stopTrainStatusPoll();
      if (status) status.textContent = 'Training complete';
      _id('compose-train-stop')?.classList.add('hidden');
      _id('compose-train-complete')?.classList.remove('hidden');
    } else if (data.error) {
      _stopTrainStatusPoll();
      if (status) status.textContent = 'Error: ' + data.error;
    }
  } catch {}
}

function _drawLossChart(history) {
  const canvas = _id('compose-train-loss-chart');
  if (!canvas || !history?.length) return;
  const wrap = _id('compose-train-chart-wrap');
  if (wrap) wrap.classList.remove('hidden');

  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth;
  const h = canvas.clientHeight;
  canvas.width = w * dpr;
  canvas.height = h * dpr;
  const ctx = canvas.getContext('2d');
  ctx.scale(dpr, dpr);

  ctx.clearRect(0, 0, w, h);

  // Grid
  ctx.strokeStyle = 'rgba(255,255,255,0.05)';
  ctx.lineWidth = 1;
  for (let i = 0; i < 5; i++) {
    const y = (h / 5) * i;
    ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke();
  }

  // Loss line
  const maxLoss = Math.max(...history.map(h => h.loss || h));
  const minLoss = Math.min(...history.map(h => h.loss || h));
  const range = maxLoss - minLoss || 1;

  ctx.strokeStyle = '#f59e0b';
  ctx.lineWidth = 2;
  ctx.beginPath();
  history.forEach((point, i) => {
    const loss = point.loss ?? point;
    const x = (i / Math.max(history.length - 1, 1)) * w;
    const y = h - ((loss - minLoss) / range) * (h - 10) - 5;
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();

  // Latest dot
  if (history.length > 0) {
    const last = history[history.length - 1];
    const loss = last.loss ?? last;
    const x = w;
    const y = h - ((loss - minLoss) / range) * (h - 10) - 5;
    ctx.fillStyle = '#f59e0b';
    ctx.beginPath(); ctx.arc(x, y, 4, 0, Math.PI * 2); ctx.fill();
  }
}

// ─── Snapshots ──────────────────────────────────────────────────────

async function _saveSnapshot() {
  const nameInput = _id('compose-train-snapshot-name');
  let name = nameInput?.value?.trim() || '';
  if (!name) name = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
  try {
    await api('/compose/train/snapshots/save', { method: 'POST', body: JSON.stringify({ name }) });
    if (nameInput) nameInput.value = '';
    _loadSnapshotList();
  } catch {}
}

async function _loadSnapshotList() {
  try {
    const data = await api('/compose/train/snapshots');
    const list = _id('compose-train-snapshot-list');
    if (!list) return;
    clearChildren(list);
    const snaps = data.snapshots || [];
    if (snaps.length === 0) { list.classList.add('hidden'); return; }
    list.classList.remove('hidden');
    for (const snap of snaps) {
      const loadBtn = el('button', { className: 'compose-ghost-btn' }, 'Load');
      const delBtn = el('button', { className: 'compose-ghost-btn' }, 'Del');
      loadBtn.addEventListener('click', async () => {
        try {
          await api('/compose/train/snapshots/load', { method: 'POST', body: JSON.stringify({ name: snap.name }) });
          await _fetchSamples();
          _trainScanned = true;
          _trainLabeled = true;
          const state = await api('/compose/train/pipeline-state');
          _trainPreprocessed = state.has_tensors;
          _id('compose-train-start').disabled = !_trainPreprocessed;
          _id('compose-train-label').disabled = false;
          _id('compose-train-preprocess').disabled = false;
        } catch {}
      });
      delBtn.addEventListener('click', async () => {
        try {
          await api(`/compose/train/snapshots/${encodeURIComponent(snap.name)}`, { method: 'DELETE' });
          _loadSnapshotList();
        } catch {}
      });
      const meta = snap.meta || {};
      const metaText = `${meta.tensor_count || 0} tensors \u00B7 ${snap.size_mb || 0}MB`;
      list.appendChild(el('div', { className: 'compose-train-snapshot-entry' },
        el('span', { className: 'compose-train-snapshot-name' }, snap.name),
        el('span', { className: 'text-dim', style: { fontSize: '11px' } }, metaText),
        loadBtn, delBtn,
      ));
    }
  } catch {}
}

// ─── Pipeline state recovery ────────────────────────────────────────

async function _recoverPipelineState() {
  try {
    const state = await api('/compose/train/pipeline-state');
    _trainFiles = state.audio_files || [];
    _updateTrainFileList();
    _id('compose-train-scan').disabled = _trainFiles.length === 0;

    if (state.has_saved_dataset) {
      try { await api('/compose/train/load', { method: 'POST' }); } catch {}
      await _fetchSamples();
      _trainScanned = true;
      _trainLabeled = true;
      _id('compose-train-label').disabled = false;
      _id('compose-train-preprocess').disabled = false;
    }
    if (state.has_tensors) {
      _trainPreprocessed = true;
      _id('compose-train-start').disabled = false;
      _id('compose-train-snapshot-save').disabled = false;
    }

    _loadSnapshotList();

    // Check for in-progress training
    const trainStatus = await api('/compose/train/status');
    if (trainStatus.is_training) {
      _id('compose-train-start').disabled = true;
      _id('compose-train-stop')?.classList.remove('hidden');
      _id('compose-train-loss')?.classList.remove('hidden');
      _id('compose-train-progress')?.classList.remove('hidden');
      _id('compose-train-chart-wrap')?.classList.remove('hidden');
      _startTrainStatusPoll();
    }
  } catch {}
}

// ─── Output Panel ───────────────────────────────────────────────────

function buildOutputPanel() {
  return el('div', { className: 'compose-output', id: 'compose-output' },
    el('div', { className: 'compose-output-generating hidden', id: 'compose-generating' },
      el('div', { className: 'compose-spinner' }),
      el('span', {}, 'Generating\u2026 '),
      el('span', { id: 'compose-elapsed', className: 'compose-elapsed' }),
      el('button', { className: 'compose-ghost-btn', onClick: cancelGeneration }, 'Cancel'),
    ),
    el('div', { id: 'compose-output-idle', style: { textAlign: 'center', padding: '8px', color: 'var(--text-dim)', fontSize: '13px' } },
      'Generate a song to see results here'),
  );
}

// ─── Mode / Tab Switching ───────────────────────────────────────────

function switchMode(mode) {
  _mode = mode;
  document.querySelectorAll('#panel-compose .compose-mode-btn').forEach(b =>
    b.classList.toggle('active', b.dataset.mode === mode));
  const isTrain = mode === 'train';
  const cp = _id('compose-create-panel');
  const rp = _id('compose-rework-panel');
  const lp = _id('compose-lego-panel');
  const mp = _id('compose-complete-panel');
  const vp = _id('compose-voice-panel');
  const tabs = _id('compose-create-tabs');
  if (cp) cp.classList.toggle('hidden', mode !== 'create');
  if (rp) rp.classList.toggle('hidden', mode !== 'rework');
  if (lp) lp.classList.toggle('hidden', mode !== 'lego');
  if (mp) mp.classList.toggle('hidden', mode !== 'complete');
  if (vp) vp.classList.toggle('hidden', mode !== 'voice');
  if (tabs) tabs.classList.toggle('hidden', mode !== 'create');

  // Toggle between main grid and train grid
  const mainGrid = document.querySelector('#panel-compose > .compose-main:not(.compose-train-grid)');
  const trainGrid = _id('compose-train-grid');
  if (mainGrid) mainGrid.classList.toggle('hidden', isTrain);
  if (trainGrid) trainGrid.classList.toggle('hidden', !isTrain);

  // For lego/complete/voice, hide the center/right columns (original behavior)
  const centerCol = document.querySelector('.compose-col-center');
  const rightCol = document.querySelector('.compose-col-right');
  if (centerCol) centerCol.classList.toggle('hidden', mode === 'lego' || mode === 'complete' || mode === 'voice');
  if (rightCol) rightCol.classList.toggle('hidden', mode === 'voice');

  // Start/stop train polling
  if (isTrain) { _startTrainStatusPoll(); _recoverPipelineState(); }
  else { _stopTrainStatusPoll(); }

  // Load voice models when entering voice mode for the first time
  if (mode === 'voice') loadVoiceModels();

  // Lock gen_model to base for analyze modes (extract/lego/complete require it)
  const isAnalyze = mode === 'lego' || mode === 'complete';
  const genModelSel = _id('compose-gen-model');
  if (genModelSel) {
    if (isAnalyze) {
      if (!genModelSel.dataset.prevValue) genModelSel.dataset.prevValue = genModelSel.value;
      genModelSel.value = 'base';
      genModelSel.disabled = true;
    } else {
      if (genModelSel.dataset.prevValue) {
        genModelSel.value = genModelSel.dataset.prevValue;
        delete genModelSel.dataset.prevValue;
      }
      genModelSel.disabled = false;
    }
    updateBatchLimit();
  }

  // Disable duration + auto for analyze modes (locked to source audio length)
  const durSlider = _id('compose-duration');
  const autoBtn = _id('compose-auto-btn');
  if (durSlider) durSlider.disabled = isAnalyze || _autoOn;
  if (autoBtn) {
    autoBtn.disabled = isAnalyze;
    if (isAnalyze && _autoOn) {
      _autoOn = false;
      autoBtn.classList.remove('active');
      autoBtn.textContent = 'Auto';
    }
  }

  const btn = _id('compose-generate-btn');
  if (btn && !btn.disabled) {
    // Voice mode doesn't need AceStep — always show the action label
    if (mode === 'voice' || _aceStepRunning) {
      btn.textContent = _modeLabel();
    }
  }
}

function switchCreateTab(tab) {
  _createTab = tab;
  document.querySelectorAll('#panel-compose .compose-create-tab').forEach(b =>
    b.classList.toggle('active', b.dataset.tab === tab));
  ['my-lyrics', 'ai-lyrics', 'instrumental'].forEach(t => {
    const el = _id(`compose-tab-${t}`);
    if (el) el.classList.toggle('hidden', t !== tab);
  });
}

function switchApproach(approach) {
  _approach = approach;
  document.querySelectorAll('#panel-compose .compose-approach-btn').forEach(b =>
    b.classList.toggle('active', b.dataset.approach === approach));
  const cg = _id('compose-cover-group');
  const rg = _id('compose-region-group');
  if (cg) cg.classList.toggle('hidden', approach !== 'cover');
  if (rg) rg.classList.toggle('hidden', approach !== 'repaint');

  const btn = _id('compose-generate-btn');
  if (btn && !btn.disabled && _aceStepRunning) {
    btn.textContent = _modeLabel();
  }
}

// ─── Style Preview ──────────────────────────────────────────────────

function updateStylePreview() {
  const tags = [...document.querySelectorAll('#compose-create-panel .compose-tag.active')]
    .map(t => t.textContent.trim()).join(', ');
  const custom = (_id('compose-style-text') || {}).value?.trim() || '';
  const style = tags && custom ? `${tags} \u2014 ${custom}` : (tags || custom);

  const root = (_id('compose-key-root') || {}).value || '';
  const mode = (_id('compose-key-mode') || {}).value || '';
  const bpm = (_id('compose-bpm') || {}).value?.trim() || '';
  const timeSig = (_id('compose-time-sig') || {}).value || '4/4';
  const parts = [];
  if (root) parts.push(`${root} ${mode}`);
  if (bpm) parts.push(`${bpm} BPM`);
  if (parts.length > 0) parts.push(`${timeSig} time`);
  const params = parts.join(', ');

  const combined = [style, params].filter(Boolean).join(' \u00b7 ');
  const previewEl = _id('compose-preview-text');
  if (previewEl) {
    previewEl.textContent = combined || 'Nothing set \u2014 add tags or a description';
    previewEl.classList.toggle('empty', !combined);
  }

  // Tag count
  const n = document.querySelectorAll('#compose-create-panel .compose-tag.active').length;
  const status = _id('compose-tags-status');
  const countEl = _id('compose-tags-count');
  if (status) status.classList.toggle('hidden', n === 0);
  if (countEl) countEl.textContent = `${n} selected`;
}

// ─── Lyrics Count / Warning ─────────────────────────────────────────

function updateLyricsCount() {
  const text = (_id('compose-lyrics-text') || {}).value || '';
  const chars = text.length;
  const lines = text === '' ? 0 : text.split('\n').length;
  const el = _id('compose-lyrics-count');
  if (el) el.textContent = `${lines} line${lines !== 1 ? 's' : ''} \u00b7 ${chars} char${chars !== 1 ? 's' : ''}`;
}

function checkLyricsWarning() {
  const text = (_id('compose-lyrics-text') || {}).value || '';
  const warning = _id('compose-lyrics-warning');
  if (!warning) return;
  if (!text.trim()) { warning.classList.add('hidden'); return; }

  const contentLines = text.split('\n').filter(l => l.trim() && !l.trim().startsWith('['));
  const wordCount = contentLines.join(' ').split(/\s+/).filter(w => w.length > 0).length;
  const duration = Number((_id('compose-duration') || {}).value || 30);
  const minSeconds = wordCount * 0.6;

  if (wordCount > 0 && minSeconds > duration) {
    warning.textContent = '\u26A0 May be too long for selected duration';
    warning.classList.remove('hidden');
  } else {
    warning.classList.add('hidden');
  }
}

// ─── Batch Limit ────────────────────────────────────────────────────

function updateBatchLimit() {
  const model = (_id('compose-gen-model') || {}).value || 'turbo';
  const lm = (_id('compose-lm-model') || {}).value || '1.7b';
  const tier = (_id('compose-vram-tier') || {}).value || '16';
  const heavy = (model === 'sft' || model === 'base') && lm === '4b';
  const limits = _BATCH_LIMITS[tier] || _BATCH_LIMITS['16'];
  const max = heavy ? limits.heavy : limits.normal;

  const input = _id('compose-batch-size');
  const note = _id('compose-batch-note');
  if (input) {
    input.max = max;
    if (Number(input.value) > max) input.value = max;
    input.disabled = max === 1;
  }
  if (note) {
    if (max === 1) {
      note.textContent = 'Locked to 1 \u2014 this model + VRAM combination requires it.';
      note.classList.remove('hidden');
    } else {
      note.classList.add('hidden');
    }
  }
}

function syncAdvancedFromFriendly() {
  const la = Number((_id('compose-lyric-adherence') || {}).value || 1);
  const q = Number((_id('compose-quality') || {}).value || 1);
  const glSlider = _id('compose-guidance-lyric');
  const isSlider = _id('compose-inf-steps');
  if (glSlider) {
    glSlider.value = _LYRIC_STEPS[la];
    _updateSlider(glSlider);
    const label = _id('compose-gl-value');
    if (label) label.textContent = Number(glSlider.value).toFixed(1);
  }
  if (isSlider) {
    isSlider.value = _QUALITY_STEPS[q];
    _updateSlider(isSlider);
    const label = _id('compose-inf-steps-value');
    if (label) label.textContent = isSlider.value;
  }
}

// ─── Auto Duration ──────────────────────────────────────────────────

function toggleAutoDuration() {
  _autoOn = !_autoOn;
  const btn = _id('compose-auto-btn');
  const slider = _id('compose-duration');
  if (btn) {
    btn.classList.toggle('active', _autoOn);
    btn.textContent = _autoOn ? 'Auto \u2713' : 'Auto';
  }
  if (slider) slider.disabled = _autoOn;
  if (_autoOn) computeAutoDuration();
}

async function computeAutoDuration() {
  if (!_autoOn) return;
  const btn = _id('compose-auto-btn');
  if (btn) { btn.textContent = 'Computing\u2026'; btn.disabled = true; }
  try {
    const res = await fetch('/api/compose/estimate-duration', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        lyrics: (_id('compose-lyrics-text') || {}).value || '',
        bpm: (_id('compose-bpm') || {}).value?.trim() ? parseInt(_id('compose-bpm').value) : null,
        time_signature: (_id('compose-time-sig') || {}).value || '4/4',
        lm_model: (_id('compose-lm-model') || {}).value || '1.7b',
      }),
    });
    if (res.ok) {
      const data = await res.json();
      const secs = Math.max(10, Math.min(600, Math.round(data.seconds / 5) * 5));
      const slider = _id('compose-duration');
      if (slider) { slider.value = secs; _updateSlider(slider); slider.dispatchEvent(new Event('input')); }
    }
  } catch { /* leave as-is */ }
  if (btn) { btn.textContent = _autoOn ? 'Auto \u2713' : 'Auto'; btn.disabled = false; }
}

// ─── Audio Upload (Rework) ──────────────────────────────────────────

function browseAudio() {
  const input = document.createElement('input');
  input.type = 'file';
  input.accept = 'audio/*';
  input.addEventListener('change', () => { if (input.files[0]) handleAudioUpload(input.files[0]); });
  input.click();
}

function setupUploadDragDrop(zone, handler) {
  const onDrop = handler || handleAudioUpload;
  zone.addEventListener('dragenter', (e) => { e.preventDefault(); zone.classList.add('drag-over'); });
  zone.addEventListener('dragover', (e) => { e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; });
  zone.addEventListener('dragleave', (e) => { if (!zone.contains(e.relatedTarget)) zone.classList.remove('drag-over'); });
  zone.addEventListener('drop', (e) => {
    e.preventDefault(); zone.classList.remove('drag-over');
    if (e.dataTransfer.files[0]) onDrop(e.dataTransfer.files[0]);
  });
}

async function handleAudioUpload(file) {
  if (!file || !file.type.startsWith('audio/')) return;

  const fnEl = _id('compose-upload-filename');
  const durEl = _id('compose-upload-duration');
  if (fnEl) fnEl.textContent = file.name;
  _id('compose-upload-prompt')?.classList.add('hidden');
  _id('compose-upload-loaded')?.classList.remove('hidden');

  const form = new FormData();
  form.append('file', file);
  try {
    const res = await fetch('/api/compose/upload-audio', { method: 'POST', body: form });
    if (!res.ok) throw new Error(res.statusText);
    const data = await res.json();
    _uploadedPath = data.path;

    // Get duration from audio element
    const audio = new Audio(URL.createObjectURL(file));
    audio.addEventListener('loadedmetadata', () => {
      _uploadedDuration = audio.duration;
      if (durEl) durEl.textContent = _formatDuration(audio.duration);
      const re = _id('compose-region-end');
      if (re) { re.value = Math.round(audio.duration * 10) / 10; re.max = re.value; }
    });
  } catch (err) {
    removeUploadedAudio();
  }
}

function removeUploadedAudio() {
  _uploadedPath = null;
  _uploadedDuration = null;
  _id('compose-upload-prompt')?.classList.remove('hidden');
  _id('compose-upload-loaded')?.classList.add('hidden');
}

// ─── Lego Audio Upload ─────────────────────────────────────────────

function browseLegoAudio() {
  const input = document.createElement('input');
  input.type = 'file';
  input.accept = 'audio/*';
  input.addEventListener('change', () => { if (input.files[0]) handleLegoAudioUpload(input.files[0]); });
  input.click();
}

async function handleLegoAudioUpload(file) {
  if (!file || !file.type.startsWith('audio/')) return;
  const fnEl = _id('compose-lego-upload-filename');
  const durEl = _id('compose-lego-upload-duration');
  if (fnEl) fnEl.textContent = file.name;
  _id('compose-lego-upload-prompt')?.classList.add('hidden');
  _id('compose-lego-upload-loaded')?.classList.remove('hidden');

  const form = new FormData();
  form.append('file', file);
  try {
    const res = await fetch('/api/compose/upload-audio', { method: 'POST', body: form });
    if (!res.ok) throw new Error(res.statusText);
    const data = await res.json();
    _legoUploadedPath = data.path;
    const blobUrl = URL.createObjectURL(file);
    const audio = new Audio(blobUrl);
    audio.addEventListener('loadedmetadata', () => {
      _legoUploadedDuration = audio.duration;
      if (durEl) durEl.textContent = _formatDuration(audio.duration);
    });
    // Render source waveform
    _legoSourcePeaks = null;
    _id('lego-wf-result-section')?.classList.add('hidden');
    _renderSourceWaveform(blobUrl, 'lego-wf-source', 'lego-wf-source-canvas',
      (peaks) => { _legoSourcePeaks = peaks; });
  } catch {
    removeLegoUploadedAudio();
  }
}

function removeLegoUploadedAudio() {
  _legoUploadedPath = null;
  _legoUploadedDuration = null;
  _legoSourcePeaks = null;
  _id('compose-lego-upload-prompt')?.classList.remove('hidden');
  _id('compose-lego-upload-loaded')?.classList.add('hidden');
  _id('lego-wf-source-section')?.classList.add('hidden');
  _id('lego-wf-result-section')?.classList.add('hidden');
}

// ─── Complete Audio Upload ─────────────────────────────────────────

function browseCompleteAudio() {
  const input = document.createElement('input');
  input.type = 'file';
  input.accept = 'audio/*';
  input.addEventListener('change', () => { if (input.files[0]) handleCompleteAudioUpload(input.files[0]); });
  input.click();
}

async function handleCompleteAudioUpload(file) {
  if (!file || !file.type.startsWith('audio/')) return;
  const fnEl = _id('compose-complete-upload-filename');
  const durEl = _id('compose-complete-upload-duration');
  if (fnEl) fnEl.textContent = file.name;
  _id('compose-complete-upload-prompt')?.classList.add('hidden');
  _id('compose-complete-upload-loaded')?.classList.remove('hidden');

  const form = new FormData();
  form.append('file', file);
  try {
    const res = await fetch('/api/compose/upload-audio', { method: 'POST', body: form });
    if (!res.ok) throw new Error(res.statusText);
    const data = await res.json();
    _completeUploadedPath = data.path;
    const blobUrl = URL.createObjectURL(file);
    const audio = new Audio(blobUrl);
    audio.addEventListener('loadedmetadata', () => {
      _completeUploadedDuration = audio.duration;
      if (durEl) durEl.textContent = _formatDuration(audio.duration);
    });
    // Render source waveform
    _completeSourcePeaks = null;
    _id('complete-wf-result-section')?.classList.add('hidden');
    _renderSourceWaveform(blobUrl, 'complete-wf-source', 'complete-wf-source-canvas',
      (peaks) => { _completeSourcePeaks = peaks; });
  } catch {
    removeCompleteUploadedAudio();
  }
}

function removeCompleteUploadedAudio() {
  _completeUploadedPath = null;
  _completeUploadedDuration = null;
  _completeSourcePeaks = null;
  _id('compose-complete-upload-prompt')?.classList.remove('hidden');
  _id('compose-complete-upload-loaded')?.classList.add('hidden');
  _id('complete-wf-source-section')?.classList.add('hidden');
  _id('complete-wf-result-section')?.classList.add('hidden');
}

function loadLyricsFile() {
  const input = document.createElement('input');
  input.type = 'file';
  input.accept = '.txt,.lrc,text/plain';
  input.addEventListener('change', () => {
    const file = input.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (e) => {
      const ta = _id('compose-lyrics-text');
      if (ta) { ta.value = e.target.result; updateLyricsCount(); checkLyricsWarning(); }
    };
    reader.readAsText(file);
  });
  input.click();
}

// ─── Payload Building ───────────────────────────────────────────────

function getStylePrompt() {
  const tags = [...document.querySelectorAll('#compose-create-panel .compose-tag.active')]
    .map(t => t.textContent.trim()).join(', ');
  const custom = (_id('compose-style-text') || {}).value?.trim() || '';
  if (tags && custom) return `${tags} \u2014 ${custom}`;
  return tags || custom;
}

function buildPayload() {
  const seedRaw = (_id('compose-seed') || {}).value?.trim() || '';
  const shared = {
    lyrics: (_mode === 'create' && _createTab !== 'my-lyrics') ? '' : ((_id('compose-lyrics-text') || {}).value || ''),
    duration: Number((_id('compose-duration') || {}).value || 30),
    lyric_adherence: Number((_id('compose-lyric-adherence') || {}).value || 1),
    creativity: Number((_id('compose-creativity') || {}).value || 50),
    quality: Number((_id('compose-quality') || {}).value || 1),
    seed: seedRaw !== '' ? parseInt(seedRaw, 10) : null,
    gen_model: (_id('compose-gen-model') || {}).value || 'turbo',
    lm_model: (_id('compose-lm-model') || {}).value || '1.7b',
    batch_size: Number((_id('compose-batch-size') || {}).value || 1),
    scheduler: (_id('compose-scheduler') || {}).value || 'euler',
    audio_format: (_id('compose-audio-format') || {}).value || 'mp3',
    guidance_scale_raw: Number((_id('compose-guidance-lyric') || {}).value || 7),
    audio_guidance_scale: Number((_id('compose-guidance-audio') || {}).value || 4),
    inference_steps_raw: Number((_id('compose-inf-steps') || {}).value || 60),
  };

  if (_mode === 'rework') {
    const taskType = _approach === 'cover' ? 'cover' : 'repaint';
    const payload = {
      ...shared,
      style: (_id('compose-rework-direction') || {}).value?.trim() || '',
      task_type: taskType,
      src_audio_path: _uploadedPath,
    };
    if (taskType === 'cover') {
      payload.audio_cover_strength = Number((_id('compose-cover-strength') || {}).value || 50) / 100;
    } else {
      payload.repainting_start = Number((_id('compose-region-start') || {}).value || 0);
      payload.repainting_end = Number((_id('compose-region-end') || {}).value || 0);
    }
    return payload;
  }

  if (_mode === 'lego') {
    return {
      ...shared,
      style: (_id('compose-lego-direction') || {}).value?.trim() || '',
      task_type: 'lego',
      src_audio_path: _legoUploadedPath,
      track_name: _selectedLegoTrack,
      gen_model: 'base',
      lm_model: 'none',
      duration: _legoUploadedDuration || shared.duration,
      batch_size: 1,
    };
  }

  if (_mode === 'complete') {
    return {
      ...shared,
      style: (_id('compose-complete-direction') || {}).value?.trim() || '',
      task_type: 'complete',
      src_audio_path: _completeUploadedPath,
      track_classes: _selectedCompleteTracks,
      gen_model: 'base',
      lm_model: 'none',
      duration: _completeUploadedDuration || shared.duration,
      batch_size: 1,
    };
  }

  // Create mode
  const bpmRaw = (_id('compose-bpm') || {}).value?.trim() || '';
  const keyRoot = (_id('compose-key-root') || {}).value || '';
  const keyMode = (_id('compose-key-mode') || {}).value || '';
  const payload = {
    ...shared,
    style: getStylePrompt(),
    key: keyRoot ? `${keyRoot} ${keyMode}` : '',
    bpm: bpmRaw !== '' ? parseInt(bpmRaw, 10) : null,
    time_signature: (_id('compose-time-sig') || {}).value || '4/4',
  };

  if (_createTab === 'ai-lyrics') {
    const desc = (_id('compose-ai-description') || {}).value?.trim() || '';
    const styleContext = [getStylePrompt()].filter(Boolean).join(', ');
    const query = [desc, styleContext].filter(Boolean).join('. ');
    if (query) {
      payload.sample_query = query;
      payload.vocal_language = (_id('compose-ai-lang') || {}).value || 'en';
    }
  }

  return payload;
}

// ─── AceStep lazy startup ───────────────────────────────────────────

/**
 * Ensure AceStep is running before any generation call.
 * If status is "ready" (configured but not spawned), triggers launch and
 * polls until the subprocess is up — showing a notice in the output panel.
 * Resolves when running, rejects on crash/timeout/disabled.
 */
async function ensureAceStep() {
  const health = await api('/compose/health');
  const status = health.acestep_status;

  if (status === 'running') return;
  if (status === 'disabled') throw new Error('AceStep is disabled (start without --no-acestep)');
  if (status === 'crashed') throw new Error('AceStep crashed — check the terminal for details');

  // "ready" or "starting" — need to wait for it to be running
  if (status === 'ready') {
    await fetch('/api/compose/start', { method: 'POST' });
  }

  // Show startup notice in the generating panel
  const genPanel = _id('compose-generating');
  const idlePanel = _id('compose-output-idle');
  if (genPanel) {
    genPanel.classList.remove('hidden');
    clearChildren(genPanel);
    genPanel.append(
      el('div', { className: 'compose-spinner' }),
      el('span', {}, 'Starting AceStep\u2026 downloading models if needed. Please stand by. '),
      el('span', { id: 'compose-startup-elapsed', className: 'compose-elapsed' }),
    );
  }
  if (idlePanel) idlePanel.classList.add('hidden');

  const startTime = Date.now();
  const elapsedEl = _id('compose-startup-elapsed');
  const elapsedTimer = setInterval(() => {
    const secs = Math.floor((Date.now() - startTime) / 1000);
    const m = Math.floor(secs / 60);
    const s = secs % 60;
    if (elapsedEl) elapsedEl.textContent = m > 0 ? `${m}m ${String(s).padStart(2, '0')}s` : `${s}s`;
  }, 1000);

  // Poll health until running — no fixed timeout; keeps going as long as
  // AceStep is still starting (downloading/loading models).  Backend sets
  // "crashed" only when the process actually exits, so we'll catch that.
  const POLL_INTERVAL = 10000;
  try {
    while (true) {
      await new Promise(r => setTimeout(r, POLL_INTERVAL));
      const h = await api('/compose/health');
      if (h.acestep_status === 'running') return;
      if (h.acestep_status === 'crashed') throw new Error('AceStep crashed during startup');
      if (h.acestep_status === 'disabled') throw new Error('AceStep is disabled');
    }
  } finally {
    clearInterval(elapsedTimer);
    // Restore generating panel to its normal state
    if (genPanel) {
      clearChildren(genPanel);
      genPanel.classList.add('hidden');
      genPanel.append(
        el('div', { className: 'compose-spinner' }),
        el('span', {}, 'Generating\u2026 '),
        el('span', { id: 'compose-elapsed', className: 'compose-elapsed' }),
        el('button', { className: 'compose-ghost-btn', onClick: cancelGeneration }, 'Cancel'),
      );
    }
    if (idlePanel) idlePanel.classList.remove('hidden');
  }
}

// ─── Generation ─────────────────────────────────────────────────────

async function handleGenerate() {
  const btn = _id('compose-generate-btn');
  const hint = _id('compose-hint');

  // ── Initialize flow (AceStep not yet running) ──
  if (!_aceStepRunning) {
    if (btn) { btn.disabled = true; btn.textContent = 'Starting\u2026'; }
    if (hint) hint.textContent = '';
    try {
      await ensureAceStep();
      _aceStepRunning = true;
      if (btn) { btn.disabled = false; btn.textContent = _modeLabel(); }
    } catch (err) {
      if (hint) hint.textContent = `AceStep: ${err.message}`;
      if (btn) { btn.disabled = false; btn.textContent = '\u23FB Initialize'; }
    }
    return;
  }

  // ── Generate flow (AceStep is running) ──

  // Validation
  if (_mode === 'rework' && !_uploadedPath) {
    if (hint) hint.textContent = 'Upload audio to get started.';
    return;
  }
  if (_mode === 'lego' && !_legoUploadedPath) {
    if (hint) hint.textContent = 'Upload source audio for Lego mode.';
    return;
  }
  if (_mode === 'complete' && !_completeUploadedPath) {
    if (hint) hint.textContent = 'Upload source audio for Complete mode.';
    return;
  }
  if (_mode === 'complete' && _selectedCompleteTracks.length === 0) {
    if (hint) hint.textContent = 'Select at least one track to generate.';
    return;
  }
  if (hint) hint.textContent = '';

  const payload = buildPayload();
  const loraWasLoaded = _id('compose-lora-status')?.classList.contains('loaded');
  setGenerating(true);

  let taskId;
  try {
    const res = await fetch('/api/compose/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || res.statusText);
    }
    ({ task_id: taskId } = await res.json());
  } catch (err) {
    if (hint) hint.textContent = `Error: ${err.message}`;
    setGenerating(false);
    return;
  }

  // Poll /api/compose/status/{task_id}
  _pollTimer = setInterval(async () => {
    try {
      const res = await fetch(`/api/compose/status/${taskId}`);
      if (!res.ok) throw new Error(res.statusText);
      const data = await res.json();

      if (data.status === 'done') {
        clearInterval(_pollTimer);
        _pollTimer = null;
        setGenerating(false);
        showResults(taskId, data.results || [], payload);
        // Check if LoRA adapter was dropped during generation
        if (loraWasLoaded) {
          _refreshLoraStatus().then(() => {
            const still = _id('compose-lora-status')?.classList.contains('loaded');
            if (!still) {
              const hint = _id('compose-hint');
              if (hint) hint.textContent = 'Warning: LoRA adapter was unloaded during generation. Reload it before next run.';
            }
          });
        }
      } else if (data.status === 'error') {
        clearInterval(_pollTimer);
        _pollTimer = null;
        setGenerating(false);
        if (hint) hint.textContent = 'Generation failed. Check AceStep logs.';
      }
    } catch (err) {
      clearInterval(_pollTimer);
      _pollTimer = null;
      setGenerating(false);
      if (hint) hint.textContent = `Polling error: ${err.message}`;
    }
  }, 10000);
}

function setGenerating(on) {
  const btn = _id('compose-generate-btn');
  const genPanel = _id('compose-generating');
  const idlePanel = _id('compose-output-idle');

  if (btn) {
    btn.disabled = on;
    btn.textContent = on ? 'Generating\u2026' : _modeLabel();
  }
  if (genPanel) genPanel.classList.toggle('hidden', !on);
  if (idlePanel) idlePanel.classList.toggle('hidden', on);

  if (on) {
    const startTime = Date.now();
    const elapsed = _id('compose-elapsed');
    _elapsedTimer = setInterval(() => {
      const secs = Math.floor((Date.now() - startTime) / 1000);
      const m = Math.floor(secs / 60);
      const s = secs % 60;
      if (elapsed) elapsed.textContent = m > 0 ? `${m}m ${String(s).padStart(2, '0')}s` : `${s}s`;
    }, 1000);
  } else {
    clearInterval(_elapsedTimer);
    _elapsedTimer = null;
  }
}

function cancelGeneration() {
  if (_pollTimer) { clearInterval(_pollTimer); _pollTimer = null; }
  setGenerating(false);
}

// ─── Results ────────────────────────────────────────────────────────

function _captureLastSeed(results) {
  if (!results || !results.length) return;
  const sv = results[0].seed_value;
  if (sv != null && sv !== '') {
    const first = String(sv).split(',')[0].trim();
    if (first && !isNaN(first)) {
      _lastSeed = first;
      const btn = _id('compose-seed-last');
      if (btn) { btn.disabled = false; btn.title = 'Use last seed: ' + first; }
    }
  }
}

function showResults(taskId, results, payload) {
  const output = _id('compose-output');
  const idle = _id('compose-output-idle');
  if (idle) idle.classList.add('hidden');

  // Capture actual seed for Last button
  _captureLastSeed(results);

  // Destroy previous players
  for (const p of _resultPlayers) {
    try { p.ws.destroy(); } catch {}
  }
  _resultPlayers.length = 0;

  // AI Lyrics — populate the Generated Lyrics textarea
  if (_createTab === 'ai-lyrics' && results[0]?.lyrics) {
    const display = _id('compose-ai-lyrics-display');
    if (display) display.value = results[0].lyrics;
  }

  const fmt = payload.audio_format || 'mp3';

  results.forEach((result, i) => {
    const audioPath = result.audio_url || '';
    const card = buildResultCard(taskId, i, results.length, result, fmt);
    if (output) output.appendChild(card);

    // Emit composeReady for cross-tab integration
    appState.composePaths.push({ path: audioPath, title: result.prompt || 'Composed', metadata: result.meta });
    appState.emit('composeReady', { path: audioPath, title: result.prompt || 'Composed', metadata: result.meta });
  });

  // Render diff waveform for analyze modes (first result)
  if ((_mode === 'lego' || _mode === 'complete') && results.length > 0 && results[0].audio_url) {
    const resultUrl = `/api/compose/audio?path=${encodeURIComponent(results[0].audio_url)}`;
    if (_mode === 'lego') {
      _renderResultWaveform(resultUrl, 'lego-wf-result', 'lego-wf-result-canvas', _legoSourcePeaks);
    } else {
      _renderResultWaveform(resultUrl, 'complete-wf-result', 'complete-wf-result-canvas', _completeSourcePeaks);
    }
  }
}

/** All active result players — used for exclusive playback. */
const _resultPlayers = [];

/** Stop all other result players except the given one. */
function _stopOtherPlayers(except) {
  for (const p of _resultPlayers) {
    if (p.ws !== except && p.ws.isPlaying()) {
      p.ws.stop();
      p.playBtn.textContent = '\u25B6 Play';
    }
  }
}

function buildResultCard(taskId, index, total, result, fmt) {
  const audioPath = result.audio_url || '';
  const audioSrc = `/api/compose/audio?path=${encodeURIComponent(audioPath)}`;
  const dlAudioUrl = `/api/compose/download/${taskId}/${index}/audio`;
  const dlJsonUrl = `/api/compose/download/${taskId}/${index}/json`;
  const filename = `acestep-${taskId.slice(0, 8)}-${index + 1}.${fmt}`;

  const card = el('div', { className: 'stem-card' });

  const label = total > 1 ? `Result ${index + 1} of ${total}` : 'Result';

  if (audioPath) {
    // Transport buttons
    const playBtn = el('button', { className: 'btn btn-sm' }, '\u25B6 Play');
    const stopBtn = el('button', { className: 'btn btn-sm' }, '\u25A0 Stop');
    const rewindBtn = el('button', { className: 'btn btn-sm' }, '\u23EA Rewind');
    const timeLabel = el('span', { className: 'stem-time' }, '0:00 / 0:00');

    const saveBtn = el('button', {
      className: 'btn btn-sm',
      onClick: () => saveFileAs(dlAudioUrl, filename),
    }, '\u2193 Save');

    const header = el('div', { className: 'stem-card-header' },
      el('span', { className: 'stem-label' }, label),
      el('div', { className: 'stem-actions' },
        playBtn, stopBtn, rewindBtn, timeLabel, saveBtn,
      ),
    );

    const waveContainer = el('div', { className: 'stem-waveform' });
    card.append(header, waveContainer);

    // Wavesurfer inline player
    const ws = createWaveform(waveContainer, { height: 50 });
    ws.load(audioSrc);

    _resultPlayers.push({ ws, playBtn });

    playBtn.addEventListener('click', () => {
      if (ws.isPlaying()) {
        ws.pause();
        playBtn.textContent = '\u25B6 Play';
      } else {
        _stopOtherPlayers(ws);
        ws.play();
        playBtn.textContent = '\u23F8 Pause';
        // Load into global transport for cross-tab "Now Playing"
        transportLoad(audioSrc, label, false, 'Compose');
      }
    });

    stopBtn.addEventListener('click', () => {
      ws.stop();
      transportStop();
      playBtn.textContent = '\u25B6 Play';
    });

    rewindBtn.addEventListener('click', () => {
      ws.setTime(0);
    });

    ws.on('timeupdate', (time) => {
      const dur = ws.getDuration();
      timeLabel.textContent = `${formatTime(time)} / ${formatTime(dur)}`;
    });

    ws.on('finish', () => {
      playBtn.textContent = '\u25B6 Play';
      transportStop();
    });
  }

  // Actions row
  const actions = el('div', { className: 'compose-card-actions' },
    el('a', { className: 'btn btn-sm', href: dlJsonUrl, download: `acestep-${taskId.slice(0, 8)}-${index + 1}.json` }, 'JSON'),
    el('button', { className: 'btn btn-sm btn-primary', onClick: () => sendToSeparate(audioPath) }, '\u2192 Separate'),
  );
  card.appendChild(actions);

  return card;
}

// ─── Voice Mode ─────────────────────────────────────────────────────

async function loadVoiceModels() {
  if (_voiceModels.length > 0) return;  // already loaded
  const sel = _id('compose-voice-model');
  if (!sel) return;
  try {
    const data = await api('/voice/models');
    _voiceModels = data.models || [];
    clearChildren(sel);
    sel.appendChild(el('option', { value: '' }, 'Select a voice...'));
    for (const m of _voiceModels) {
      const label = m.downloaded ? m.name : `${m.name} (download)`;
      sel.appendChild(el('option', { value: m.name }, label));
    }
  } catch {
    clearChildren(sel);
    sel.appendChild(el('option', { value: '' }, 'Failed to load models'));
  }
}

function _reloadVoiceModels() {
  _voiceModels = [];  // force reload
  loadVoiceModels();
}

function showVoiceModelImport() {
  const row = _id('voice-model-import-row');
  if (!row) return;
  row.classList.remove('hidden');
  _id('voice-model-search-input')?.focus();
}

async function doVoiceModelSearch() {
  const query = (_id('voice-model-search-input') || {}).value?.trim();
  const container = _id('voice-model-search-results');
  const status = _id('voice-model-import-status');
  if (!query || query.length < 2) {
    if (status) status.textContent = 'Type at least 2 characters.';
    return;
  }

  if (status) status.textContent = 'Searching...';
  if (container) clearChildren(container);

  try {
    const data = await api(`/voice/models/search?q=${encodeURIComponent(query)}`);
    const results = data.results || [];
    if (status) status.textContent = results.length ? `${results.length} result(s)` : 'No models found.';
    if (!container) return;

    for (const r of results) {
      const row = el('div', { className: 'voice-search-result',
        style: { display: 'flex', justifyContent: 'space-between', alignItems: 'center',
          padding: '4px 6px', borderBottom: '1px solid var(--border)', fontSize: '12px' } },
        el('span', { style: { flex: '1', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' },
          title: r.repo_id }, r.display),
        el('button', { className: 'btn btn-sm', style: { flexShrink: '0', marginLeft: '6px' },
          onClick: () => doVoiceModelDownload(r.repo_id, r.display) }, 'Download'),
      );
      container.appendChild(row);
    }
  } catch (err) {
    if (status) status.textContent = `Search failed: ${err.message || err}`;
  }
}

async function doVoiceModelDownload(repoId, displayName) {
  const status = _id('voice-model-import-status');
  if (status) status.textContent = `Downloading ${displayName}...`;

  try {
    const data = await api('/voice/models/import', {
      method: 'POST',
      body: JSON.stringify({ repo_id: repoId, name: displayName }),
    });
    if (status) status.textContent = `Imported "${data.name}" (${data.size_mb} MB)`;
    _id('voice-model-import-row')?.classList.add('hidden');
    _reloadVoiceModels();
    setTimeout(() => {
      const sel = _id('compose-voice-model');
      if (sel) sel.value = data.name;
    }, 500);
  } catch (err) {
    if (status) status.textContent = `Download failed: ${err.message || err}`;
  }
}

function browseVoiceModel() {
  const input = document.createElement('input');
  input.type = 'file';
  input.accept = '.pth,.index';
  input.multiple = true;
  input.addEventListener('change', async () => {
    const files = Array.from(input.files || []);
    const pthFile = files.find(f => f.name.endsWith('.pth'));
    if (!pthFile) { alert('Select a .pth model file.'); return; }

    const status = _id('compose-voice-model-status');
    if (status) status.textContent = 'Uploading model...';

    try {
      // Upload .pth
      const form = new FormData();
      form.append('file', pthFile);
      const res = await fetch('/api/voice/models/upload', { method: 'POST', body: form });
      if (!res.ok) throw new Error((await res.json()).detail || res.statusText);
      const data = await res.json();

      // Upload .index if selected
      const idxFile = files.find(f => f.name.endsWith('.index'));
      if (idxFile) {
        const form2 = new FormData();
        form2.append('file', idxFile);
        await fetch(`/api/voice/models/upload?name=${encodeURIComponent(data.name)}`, { method: 'POST', body: form2 });
      }

      if (status) status.textContent = `Uploaded "${data.name}"`;
      _reloadVoiceModels();
      setTimeout(() => {
        const sel = _id('compose-voice-model');
        if (sel) sel.value = data.name;
      }, 500);
    } catch (err) {
      if (status) status.textContent = `Upload failed: ${err.message || err}`;
    }
  });
  input.click();
}

function _populateVoiceStemSelect() {
  const sel = _id('compose-voice-stem');
  if (!sel) return;
  const stemPaths = appState.stemPaths || {};
  // Keep first "Select" option, remove the rest
  while (sel.options.length > 1) sel.remove(1);
  for (const [label, path] of Object.entries(stemPaths)) {
    sel.appendChild(el('option', { value: path }, label));
  }
}

function selectVoiceStem() {
  const sel = _id('compose-voice-stem');
  if (!sel || !sel.value) return;
  const path = sel.value;
  const label = sel.options[sel.selectedIndex]?.text || 'stem';
  _voiceSourcePath = path;
  _voiceSourceDuration = null;

  const nameEl = _id('compose-voice-source-name');
  const durEl = _id('compose-voice-source-duration');
  const infoEl = _id('compose-voice-source-info');
  if (nameEl) nameEl.textContent = label;
  if (durEl) durEl.textContent = '';
  if (infoEl) infoEl.classList.remove('hidden');

  // Get duration via audio info endpoint
  fetch(`/api/audio/info?path=${encodeURIComponent(path)}`)
    .then(r => r.json())
    .then(info => {
      _voiceSourceDuration = info.duration;
      if (durEl) durEl.textContent = _formatDuration(info.duration);
    })
    .catch(() => {});

  // Build playable source card
  const audioUrl = `/api/audio/stream?path=${encodeURIComponent(path)}`;
  _buildVoiceSourcePlayer(audioUrl, label);
  _voiceSourcePeaks = null;
  _id('voice-wf-result-section')?.classList.add('hidden');
}

let _voiceSourceWs = null;

function _buildVoiceSourcePlayer(audioUrl, label) {
  const container = _id('compose-voice-source-player');
  if (!container) return;

  // Destroy previous
  if (_voiceSourceWs) { try { _voiceSourceWs.destroy(); } catch {} _voiceSourceWs = null; }
  clearChildren(container);
  container.classList.remove('hidden');

  const playBtn = el('button', { className: 'btn btn-sm' }, '\u25B6 Play');
  const stopBtn = el('button', { className: 'btn btn-sm' }, '\u25A0 Stop');
  const timeLabel = el('span', { className: 'stem-time' }, '0:00 / 0:00');

  const card = el('div', { className: 'stem-card' },
    el('div', { className: 'stem-card-header' },
      el('span', { className: 'stem-label' }, label),
      el('div', { className: 'stem-actions' }, playBtn, stopBtn, timeLabel),
    ),
  );
  const waveContainer = el('div', { className: 'stem-waveform' });
  card.appendChild(waveContainer);
  container.appendChild(card);

  const ws = createWaveform(waveContainer, { height: 50 });
  ws.load(audioUrl);
  _voiceSourceWs = ws;

  playBtn.addEventListener('click', () => {
    if (ws.isPlaying()) { ws.pause(); playBtn.textContent = '\u25B6 Play'; }
    else { ws.play(); playBtn.textContent = '\u23F8 Pause'; }
  });
  stopBtn.addEventListener('click', () => { ws.stop(); playBtn.textContent = '\u25B6 Play'; });
  ws.on('timeupdate', (t) => {
    timeLabel.textContent = `${formatTime(t)} / ${formatTime(ws.getDuration())}`;
  });
  ws.on('finish', () => { playBtn.textContent = '\u25B6 Play'; });
}

function browseVoiceAudio() {
  const input = document.createElement('input');
  input.type = 'file';
  input.accept = 'audio/*,.wav,.flac,.mp3,.ogg,.aiff,.m4a,.wma,.opus';
  input.addEventListener('change', () => { if (input.files[0]) handleVoiceFileUpload(input.files[0]); });
  input.click();
}

async function handleVoiceFileUpload(file) {
  if (!file) return;
  // Accept by extension — browser MIME can be empty for some audio formats
  const ext = (file.name || '').split('.').pop().toLowerCase();
  const validExts = ['wav','flac','mp3','ogg','aiff','m4a','wma','opus'];
  if (!validExts.includes(ext)) return;

  // Upload to voice-specific endpoint (no AceStep dependency)
  const form = new FormData();
  form.append('file', file);
  try {
    const res = await fetch('/api/voice/upload', { method: 'POST', body: form });
    if (!res.ok) throw new Error(res.statusText);
    const data = await res.json();
    _voiceSourcePath = data.path;

    const nameEl = _id('compose-voice-source-name');
    const durEl = _id('compose-voice-source-duration');
    const infoEl = _id('compose-voice-source-info');
    if (nameEl) nameEl.textContent = file.name;
    if (infoEl) infoEl.classList.remove('hidden');

    // Clear stem selector since we're using a file
    const sel = _id('compose-voice-stem');
    if (sel) sel.value = '';

    const blobUrl = URL.createObjectURL(file);
    const audio = new Audio(blobUrl);
    audio.addEventListener('loadedmetadata', () => {
      _voiceSourceDuration = audio.duration;
      if (durEl) durEl.textContent = _formatDuration(audio.duration);
    });

    // Build playable source card (use server URL so wavesurfer can stream)
    const serverUrl = `/api/audio/stream?path=${encodeURIComponent(data.path)}`;
    _buildVoiceSourcePlayer(serverUrl, file.name);
    _voiceSourcePeaks = null;
    _id('voice-wf-result-section')?.classList.add('hidden');
  } catch {
    removeVoiceSource();
  }
}

function removeVoiceSource() {
  _voiceSourcePath = null;
  _voiceSourceDuration = null;
  _voiceSourcePeaks = null;
  if (_voiceSourceWs) { try { _voiceSourceWs.destroy(); } catch {} _voiceSourceWs = null; }
  const sel = _id('compose-voice-stem');
  if (sel) sel.value = '';
  _id('compose-voice-source-info')?.classList.add('hidden');
  const playerEl = _id('compose-voice-source-player');
  if (playerEl) { clearChildren(playerEl); playerEl.classList.add('hidden'); }
  _id('voice-wf-result-section')?.classList.add('hidden');
}

async function handleVoiceGenerate() {
  const btn = _id('compose-voice-transform-btn');
  const hint = _id('compose-voice-hint');

  if (!_voiceSourcePath) {
    if (hint) hint.textContent = 'Select source audio first.';
    return;
  }
  const modelName = (_id('compose-voice-model') || {}).value;
  if (!modelName) {
    if (hint) hint.textContent = 'Select a voice model.';
    return;
  }
  if (hint) hint.textContent = '';

  const payload = {
    audio_path: _voiceSourcePath,
    model_name: modelName,
    pitch: Number((_id('compose-voice-pitch') || {}).value || 0),
    f0_method: (_id('compose-voice-f0') || {}).value || 'rmvpe',
    index_rate: Number((_id('compose-voice-index') || {}).value || 0.3),
    protect: Number((_id('compose-voice-protect') || {}).value || 0.33),
  };

  if (btn) { btn.disabled = true; btn.textContent = 'Transforming\u2026'; }

  try {
    const res = await fetch('/api/voice/convert', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || res.statusText);
    }
    const { job_id } = await res.json();
    _voiceJobId = job_id;

    // Poll via standard StemForge job polling
    pollJob(job_id, {
      onProgress(progress, stage) {
        if (hint) hint.textContent = stage || '';
      },
      onDone(result) {
        _voiceJobId = null;
        if (btn) { btn.disabled = false; btn.textContent = '\u25B6 Transform Voice'; }
        if (hint) hint.textContent = '';
        showVoiceResult(result);
      },
      onError(msg) {
        _voiceJobId = null;
        if (btn) { btn.disabled = false; btn.textContent = '\u25B6 Transform Voice'; }
        if (hint) hint.textContent = `Voice conversion failed: ${msg}`;
      },
    });
  } catch (err) {
    if (btn) { btn.disabled = false; btn.textContent = '\u25B6 Transform Voice'; }
    if (hint) hint.textContent = `Error: ${err.message}`;
  }
}

function showVoiceResult(result) {
  // Voice results go in the voice panel (left column), not compose-output (hidden in voice mode)
  const output = _id('compose-voice-result-container');

  const audioPath = result.output_path;
  const audioSrc = `/api/audio/stream?path=${encodeURIComponent(audioPath)}`;
  const label = `Voice (${result.model_name})`;

  // Build result card (reuse same pattern as buildResultCard)
  const card = el('div', { className: 'stem-card' });

  const playBtn = el('button', { className: 'btn btn-sm' }, '\u25B6 Play');
  const stopBtn = el('button', { className: 'btn btn-sm' }, '\u25A0 Stop');
  const rewindBtn = el('button', { className: 'btn btn-sm' }, '\u23EA Rewind');
  const timeLabel = el('span', { className: 'stem-time' }, '0:00 / 0:00');
  const saveBtn = el('button', {
    className: 'btn btn-sm',
    onClick: () => saveFileAs(`/api/audio/download?path=${encodeURIComponent(audioPath)}`,
      audioPath.split('/').pop() || 'voice.wav'),
  }, '\u2193 Save');

  const header = el('div', { className: 'stem-card-header' },
    el('span', { className: 'stem-label' }, label),
    el('div', { className: 'stem-actions' }, playBtn, stopBtn, rewindBtn, timeLabel, saveBtn),
  );

  const waveContainer = el('div', { className: 'stem-waveform' });
  card.append(header, waveContainer);

  const ws = createWaveform(waveContainer, { height: 50 });
  ws.load(audioSrc);

  _resultPlayers.push({ ws, playBtn });

  playBtn.addEventListener('click', () => {
    if (ws.isPlaying()) {
      ws.pause();
      playBtn.textContent = '\u25B6 Play';
    } else {
      _stopOtherPlayers(ws);
      ws.play();
      playBtn.textContent = '\u23F8 Pause';
      transportLoad(audioSrc, label, false, 'Compose › Voice');
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

  // Actions row
  const actions = el('div', { className: 'compose-card-actions' },
    el('button', { className: 'btn btn-sm btn-primary', onClick: () => sendToSeparate(audioPath) }, '\u2192 Separate'),
  );
  card.appendChild(actions);

  if (output) output.appendChild(card);

  // Store in app state and emit for cross-tab integration (Mix + Export)
  appState.voicePaths[label] = audioPath;
  appState.emit('transformReady', { path: audioPath, title: label });

  // Render diff waveform
  if (_voiceSourcePeaks) {
    _renderResultWaveform(audioSrc, 'voice-wf-result', 'voice-wf-result-canvas', _voiceSourcePeaks);
  }
}

// ─── Send to Separate ───────────────────────────────────────────────

async function sendToSeparate(audioPath) {
  try {
    const data = await api('/compose/send-to-session', {
      method: 'POST',
      body: JSON.stringify({ audio_path: audioPath }),
    });

    // Update app state and switch to Separate tab
    appState.audioPath = data.path;
    appState.audioInfo = {
      filename: data.filename,
      path: data.path,
      duration: data.duration,
      sample_rate: data.sample_rate,
      channels: data.channels,
    };
    appState.emit('fileLoaded', appState.audioInfo);

    // Switch to Separate tab
    const sepBtn = document.querySelector('.tab-btn[data-tab="separate"]');
    if (sepBtn) sepBtn.click();
  } catch (err) {
    const hint = _id('compose-hint');
    if (hint) hint.textContent = `Send failed: ${err.message}`;
  }
}
