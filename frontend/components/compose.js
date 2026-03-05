/**
 * Compose tab — AceStep music generation UI.
 *
 * Adapted from ACE-Step Wrangler's frontend (index.html + app.js) into
 * StemForge's ES module pattern. All DOM built programmatically via el().
 */

import { appState, api, el, formatTime, saveFileAs } from '../app.js';
import {
  transportLoad, transportPlayPause, transportStop, transportSeekTo,
  transportIsPlaying, transportOnTimeUpdate, transportOnStateChange,
} from './audio-player.js';

function clearChildren(elem) {
  while (elem.firstChild) elem.removeChild(elem.firstChild);
}

// ─── Module state ────────────────────────────────────────────────────

let _mode = 'create';          // 'create' | 'rework' | 'lego' | 'complete'
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

const ACE_TRACKS = [
  'vocals', 'backing_vocals', 'drums', 'bass', 'guitar', 'keyboard',
  'strings', 'brass', 'woodwinds', 'synth', 'percussion', 'fx',
];
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

function _modeLabel() {
  if (_mode === 'rework') return _approach === 'cover' ? '\u25B6 Reimagine' : '\u25B6 Fix & Blend';
  if (_mode === 'lego') return '\u25B6 Replace Track';
  if (_mode === 'complete') return '\u25B6 Complete';
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

  // Output panel
  const output = buildOutputPanel();

  panel.append(modeBar, mainGrid, output);

  // Init slider fills
  panel.querySelectorAll('.compose-slider').forEach(s => {
    _updateSlider(s);
    s.addEventListener('input', () => _updateSlider(s));
  });

  // Sync advanced sliders from friendly defaults
  syncAdvancedFromFriendly();
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
  );

  setupUploadDragDrop(completeUploadZone, handleCompleteAudioUpload);

  col.append(createPanel, reworkPanel, legoPanel, completePanel);
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
    _id('compose-la-value').textContent = ['Loose', 'Med', 'Strict'][Number(laSlider.value)];
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

  col.append(
    durationGroup,
    el('div', { className: 'compose-divider' }),
    buildSliderGroup('Strictly follow lyrics', 'compose-la-value', 'Med', laSlider),
    buildSliderGroup('Creativity', 'compose-cr-value', '50%', crSlider),
    buildSliderGroup('Quality', 'compose-q-value', 'Balanced', qSlider),
    genBtn, genHint,
    advanced,
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
  content.appendChild(el('div', { className: 'compose-control-group' },
    el('label', { className: 'compose-field-label' }, 'Seed'),
    el('input', { type: 'number', id: 'compose-seed', className: 'compose-number', placeholder: 'Random', min: '0', max: '2147483647' }),
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

  details.append(summary, content);
  return details;
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
  const cp = _id('compose-create-panel');
  const rp = _id('compose-rework-panel');
  const lp = _id('compose-lego-panel');
  const mp = _id('compose-complete-panel');
  const tabs = _id('compose-create-tabs');
  if (cp) cp.classList.toggle('hidden', mode !== 'create');
  if (rp) rp.classList.toggle('hidden', mode !== 'rework');
  if (lp) lp.classList.toggle('hidden', mode !== 'lego');
  if (mp) mp.classList.toggle('hidden', mode !== 'complete');
  if (tabs) tabs.classList.toggle('hidden', mode !== 'create');

  // For lego/complete, hide the center column lyrics (not relevant)
  const centerCol = document.querySelector('.compose-col-center');
  if (centerCol) centerCol.classList.toggle('hidden', mode === 'lego' || mode === 'complete');

  const btn = _id('compose-generate-btn');
  if (btn && !btn.disabled && _aceStepRunning) {
    btn.textContent = _modeLabel();
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
    const audio = new Audio(URL.createObjectURL(file));
    audio.addEventListener('loadedmetadata', () => {
      _legoUploadedDuration = audio.duration;
      if (durEl) durEl.textContent = _formatDuration(audio.duration);
    });
  } catch {
    removeLegoUploadedAudio();
  }
}

function removeLegoUploadedAudio() {
  _legoUploadedPath = null;
  _legoUploadedDuration = null;
  _id('compose-lego-upload-prompt')?.classList.remove('hidden');
  _id('compose-lego-upload-loaded')?.classList.add('hidden');
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
    const audio = new Audio(URL.createObjectURL(file));
    audio.addEventListener('loadedmetadata', () => {
      _completeUploadedDuration = audio.duration;
      if (durEl) durEl.textContent = _formatDuration(audio.duration);
    });
  } catch {
    removeCompleteUploadedAudio();
  }
}

function removeCompleteUploadedAudio() {
  _completeUploadedPath = null;
  _completeUploadedDuration = null;
  _id('compose-complete-upload-prompt')?.classList.remove('hidden');
  _id('compose-complete-upload-loaded')?.classList.add('hidden');
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
  const POLL_INTERVAL = 3000;
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
  }, 2000);
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

function showResults(taskId, results, payload) {
  const output = _id('compose-output');
  const idle = _id('compose-output-idle');
  if (idle) idle.classList.add('hidden');

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
}

function buildResultCard(taskId, index, total, result, fmt) {
  const audioPath = result.audio_url || '';
  const audioSrc = `/api/compose/audio?path=${encodeURIComponent(audioPath)}`;
  const dlAudioUrl = `/api/compose/download/${taskId}/${index}/audio`;
  const dlJsonUrl = `/api/compose/download/${taskId}/${index}/json`;
  const filename = `acestep-${taskId.slice(0, 8)}-${index + 1}.${fmt}`;

  const card = el('div', { className: 'compose-result-card' });

  if (total > 1) {
    card.appendChild(el('div', { className: 'compose-card-label' }, `Result ${index + 1} of ${total}`));
  }

  // Inline player controls — delegates all playback to the global transport bar
  if (audioPath) {
    const playBtn = el('button', { className: 'compose-player-btn compose-player-play', type: 'button', title: 'Play' }, '\u25B6');
    const stopBtn = el('button', { className: 'compose-player-btn compose-player-stop', type: 'button', title: 'Stop', disabled: true }, '\u23F9');
    const rewindBtn = el('button', { className: 'compose-player-btn compose-player-rewind', type: 'button', title: 'Rewind' }, '\u23EA');
    const scrubberFill = el('div', { className: 'compose-scrubber-fill' });
    const scrubber = el('div', { className: 'compose-scrubber' }, scrubberFill);
    const timeEl = el('span', { className: 'compose-player-time' }, '0:00 / 0:00');

    const player = el('div', { className: 'compose-audio-player' },
      rewindBtn, playBtn, stopBtn, scrubber, timeEl,
    );
    card.appendChild(player);

    _initCardPlayer(audioSrc, playBtn, stopBtn, rewindBtn, scrubber, scrubberFill, timeEl);
  }

  // Actions row
  const actions = el('div', { className: 'compose-card-actions' },
    el('button', { className: 'btn btn-sm', onClick: () => saveFileAs(dlAudioUrl, filename) }, '\u2193 Download'),
    el('a', { className: 'btn btn-sm', href: dlJsonUrl, download: `acestep-${taskId.slice(0, 8)}-${index + 1}.json` }, 'JSON'),
    el('button', { className: 'btn btn-sm btn-primary', onClick: () => sendToSeparate(audioPath) }, '\u2192 Separate'),
  );
  card.appendChild(actions);

  return card;
}

// Track active card so only one card syncs with the transport at a time
let _activeCardUrl = null;
let _unsubTime = null;
let _unsubState = null;

function _initCardPlayer(audioSrc, playBtn, stopBtn, rewindBtn, scrubber, fill, timeEl) {

  function fmtTime(s) {
    if (!isFinite(s)) return '0:00';
    const m = Math.floor(s / 60);
    const sec = Math.floor(s % 60);
    return `${m}:${String(sec).padStart(2, '0')}`;
  }

  function updateProgress(cur, dur) {
    fill.style.width = dur ? ((cur / dur) * 100) + '%' : '0%';
    timeEl.textContent = `${fmtTime(cur)} / ${fmtTime(dur)}`;
  }

  function syncButtons(playing) {
    stopBtn.disabled = !playing;
    playBtn.textContent = playing ? '\u23F8' : '\u25B6';
    playBtn.title = playing ? 'Pause' : 'Play';
  }

  // Become the active card — subscribe to transport events
  function activate() {
    if (_activeCardUrl === audioSrc) return; // already active
    // Unsubscribe previous card
    if (_unsubTime) _unsubTime();
    if (_unsubState) _unsubState();
    _activeCardUrl = audioSrc;
    _unsubTime = transportOnTimeUpdate(updateProgress);
    _unsubState = transportOnStateChange(syncButtons);
  }

  playBtn.addEventListener('click', () => {
    if (_activeCardUrl !== audioSrc) {
      // Load this card's audio into the transport
      transportLoad(audioSrc, 'Composed', true);
      activate();
    } else {
      transportPlayPause();
    }
  });

  stopBtn.addEventListener('click', () => {
    transportStop();
  });

  rewindBtn.addEventListener('click', () => {
    transportSeekTo(0);
  });

  scrubber.addEventListener('click', (e) => {
    if (_activeCardUrl !== audioSrc) {
      transportLoad(audioSrc, 'Composed', false);
      activate();
    }
    const rect = scrubber.getBoundingClientRect();
    const x = Math.max(0, Math.min(e.clientX - rect.left, rect.width));
    const fraction = x / rect.width;
    transportSeekTo(fraction);
    if (!transportIsPlaying()) transportPlayPause();
  });
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
