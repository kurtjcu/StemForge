# music21 Integration — Technical Specification

**Feature:** MIDI cleanup, analysis, transposition, and sheet music generation
**Location:** MIDI tab, post-extraction — new controls on every stem card + merged
**Dependencies:** music21 (BSD 3-clause), LilyPond (GPL, external binary — optional)

---

## Overview

music21 becomes the post-processing engine for all MIDI data in StemForge. It sits between BasicPitch/VocalMidi extraction and the user-facing MIDI cards, providing three tiers of functionality:

1. **Clean Up** — quantize, filter micro-notes, consolidate rests, fix durations → write back to cleaned PrettyMIDI (replaces raw extraction output in session)
2. **Analyze & Transform** — key detection, transposition, voice separation → update session MIDI in-place
3. **Sheet Music** — MusicXML for in-browser preview (OSMD) + PDF export via LilyPond

All three tiers use the same underlying round-trip: PrettyMIDI → temp .mid → music21 Score → manipulate → output (PrettyMIDI, MusicXML, or PDF).

---

## User Flow

```
Separate → MIDI → [Extract] → per-stem cards appear
                                  │
                                  ├─ [Clean Up]        ← quantize + filter → replaces card's MIDI
                                  ├─ [Detect Key]      ← returns detected key, updates UI selector
                                  ├─ [Transpose ±]     ← shift by interval → replaces card's MIDI
                                  ├─ [📄 Sheet Music]  ← preview (OSMD) + download PDF
                                  └─ [Save MIDI]       ← existing — now saves cleaned version
```

**"Clean Up" is the primary action.** Most users will extract MIDI, click Clean Up, then either save the MIDI or view sheet music. The cleanup step is not automatic — BasicPitch output is usable as-is for FluidSynth preview, and users may want to compare before/after.

---

## Backend

### New file: `utils/music21_bridge.py`

Lives in `utils/` (same layer as `midi_io.py`). No new pipeline class — this is CPU-only post-processing on data already in the session store.

```python
"""music21 bridge — MIDI cleanup, analysis, and notation export.

Round-trip: PrettyMIDI → temp .mid → music21 Score → manipulate → output.

All public functions accept PrettyMIDI objects directly (the type already
stored in SessionStore.stem_midi_data) and return either a new PrettyMIDI,
a string (MusicXML), or a file path (PDF).
"""

import io
import logging
import pathlib
import subprocess
import tempfile
from typing import Any

import music21

log = logging.getLogger("stemforge.utils.music21_bridge")

MidiData = Any  # pretty_midi.PrettyMIDI


# ──────────────────────────────────────────────────────────────────────
# Internal: PrettyMIDI ↔ music21 Score conversion
# ──────────────────────────────────────────────────────────────────────

def _to_score(
    midi_data: MidiData,
    *,
    quantize: bool = True,
    quarter_length_divisors: tuple[int, ...] = (4, 3),
) -> music21.stream.Score:
    """Write PrettyMIDI to temp file, parse into music21 Score.

    This is the single entry point for all music21 operations.
    Centralising it avoids repeated temp-file boilerplate.
    """


def _to_pretty_midi(score: music21.stream.Score) -> MidiData:
    """Convert music21 Score back to PrettyMIDI via temp MIDI file.

    music21 Score → streamToMidiFile() → write temp .mid → PrettyMIDI(path)
    """


# ──────────────────────────────────────────────────────────────────────
# Tier 1: Clean Up
# ──────────────────────────────────────────────────────────────────────

def clean_midi(
    midi_data: MidiData,
    *,
    quarter_length_divisors: tuple[int, ...] = (4, 3),
    min_note_quarterLength: float = 0.125,  # 32nd note
    key: str | None = None,
    time_signature: str | None = None,
) -> MidiData:
    """Quantize, filter, and clean a PrettyMIDI object → new PrettyMIDI.

    Steps:
    1. _to_score() with quantizePost=True
       - Snaps note onsets to nearest grid division
       - Snaps durations to notatable values (quarter, eighth, dotted, etc.)
       - Inserts measures/barlines
       - Separates overlapping notes into voices
    2. Remove notes shorter than min_note_quarterLength (micro-note filter)
    3. Consolidate consecutive short rests into larger rest values
    4. If key provided, insert KeySignature so accidentals are spelled
       correctly for downstream notation
    5. If time_signature provided, override the detected time signature
    6. score.makeNotation() — fixes beaming, stem direction, rest placement
    7. _to_pretty_midi() → return cleaned PrettyMIDI

    The returned object can replace the original in the session store.
    """


# ──────────────────────────────────────────────────────────────────────
# Tier 2: Analysis & Transformation
# ──────────────────────────────────────────────────────────────────────

def detect_key(midi_data: MidiData) -> dict:
    """Run Krumhansl-Schmuckler key detection on MIDI data.

    Returns:
        {
            "key": "F major",
            "confidence": 0.86,        # correlationCoefficient
            "alternates": [             # top 3 runner-up keys
                {"key": "D minor", "confidence": 0.71},
                ...
            ]
        }

    Uses score.analyze('key') which examines pitch-class frequency
    distribution, not the MIDI key signature meta-event.
    """


def transpose_midi(
    midi_data: MidiData,
    *,
    semitones: int = 0,
    interval: str | None = None,
    key: str | None = None,
) -> MidiData:
    """Transpose all notes by the given interval → new PrettyMIDI.

    Either semitones (int, e.g. 2 = up a whole step) or interval
    (music21 string, e.g. 'P5', 'm3', '-M2') must be provided.

    If key is provided, enharmonic spelling respects the target key
    (e.g. F→F# in D major, F→Gb in Bb minor).
    """


def detect_tempo(midi_data: MidiData) -> dict:
    """Estimate tempo from note onset distribution.

    Returns:
        {
            "bpm": 120.0,
            "confidence": "high" | "medium" | "low"
        }

    Supplements the BPM the user provided at extraction time.
    Useful when BPM was left at the default 120.
    """


# ──────────────────────────────────────────────────────────────────────
# Tier 3: Sheet Music / Notation Export
# ──────────────────────────────────────────────────────────────────────

def to_musicxml(
    midi_data: MidiData,
    *,
    quarter_length_divisors: tuple[int, ...] = (4, 3),
    key: str | None = None,
    time_signature: str | None = None,
    title: str | None = None,
) -> str:
    """Convert PrettyMIDI → MusicXML string for in-browser rendering.

    Runs the full cleanup pipeline internally (clean_midi logic),
    then serialises via score.write('musicxml') to a BytesIO buffer.
    Returns the MusicXML as a UTF-8 string.
    """


def to_pdf(
    midi_data: MidiData,
    output_path: pathlib.Path,
    *,
    quarter_length_divisors: tuple[int, ...] = (4, 3),
    key: str | None = None,
    time_signature: str | None = None,
    title: str | None = None,
) -> pathlib.Path:
    """Convert PrettyMIDI → PDF via music21 → LilyPond.

    1. Run cleanup + notation prep (same as to_musicxml)
    2. score.write('lily') to temp .ly file
    3. subprocess.run(['lilypond', '-o', ...]) to produce PDF
    4. Move PDF to output_path, return it

    Raises FileNotFoundError if LilyPond is not installed.
    """


def to_musicxml_file(
    midi_data: MidiData,
    output_path: pathlib.Path,
    **kwargs,
) -> pathlib.Path:
    """Write MusicXML to disk (for import into Finale/Sibelius/MuseScore).

    Same as to_musicxml() but writes to file instead of returning string.
    """


# ──────────────────────────────────────────────────────────────────────
# System
# ──────────────────────────────────────────────────────────────────────

def check_lilypond() -> dict:
    """Check LilyPond availability.

    Returns {"available": bool, "version": str | None}
    """
    try:
        result = subprocess.run(
            ["lilypond", "--version"],
            capture_output=True, text=True, timeout=5,
        )
        version = result.stdout.split("\n")[0] if result.returncode == 0 else None
        return {"available": result.returncode == 0, "version": version}
    except FileNotFoundError:
        return {"available": False, "version": None}
```

### Design notes

**Single conversion core.** `_to_score()` and `_to_pretty_midi()` are the only functions that touch temp files. Everything else operates on music21 Score objects or delegates to these two. This keeps the temp-file lifecycle in one place.

**Clean Up is idempotent.** Running clean_midi twice on the same data produces the same result. The session stores the cleaned PrettyMIDI — subsequent calls to sheet music or transposition operate on already-cleaned data, which is faster and more predictable.

**Transposition preserves the music21 round-trip quality.** Rather than naively shifting MIDI note numbers (which produces wrong enharmonic spellings), the Score-level transpose respects key context. An F# in G major transposed up a P4 becomes B, not Cb.

### New endpoints in `backend/api/midi.py`

```python
# ── Request models ────────────────────────────────────────────────────

class CleanUpRequest(BaseModel):
    stem_label: str                        # "merged" or stem name
    quantize_divisors: list[int] = [4, 3]
    min_note_length: float = 0.125         # quarterLength; 0.125 = 32nd note
    key: str | None = None
    time_signature: str | None = None

class TransposeRequest(BaseModel):
    stem_label: str
    semitones: int = 0                     # +/- half steps
    interval: str | None = None            # music21 interval string (e.g. "P5")
    key: str | None = None                 # target key for enharmonic spelling

class DetectKeyRequest(BaseModel):
    stem_label: str

class SheetMusicRequest(BaseModel):
    stem_label: str
    key: str | None = None
    time_signature: str | None = None
    quantize_divisors: list[int] = [4, 3]
    title: str | None = None


# ── Tier 1: Clean Up ─────────────────────────────────────────────────

@router.post("/clean")
def clean_stem_midi(req: CleanUpRequest, session=Depends(get_user_session)) -> dict:
    """Quantize and clean a stem's MIDI → replaces it in the session."""
    midi_data = _resolve_midi(req.stem_label, session)
    cleaned = clean_midi(
        midi_data,
        quarter_length_divisors=tuple(req.quantize_divisors),
        min_note_quarterLength=req.min_note_length,
        key=req.key,
        time_signature=req.time_signature,
    )
    _store_midi(req.stem_label, cleaned, session)
    note_count = sum(len(inst.notes) for inst in cleaned.instruments)
    return {
        "stem_label": req.stem_label,
        "note_count": note_count,
        "status": "cleaned",
    }


# ── Tier 2: Analysis & Transform ────────────────────────────────────

@router.post("/detect-key")
def detect_stem_key(req: DetectKeyRequest, session=Depends(get_user_session)) -> dict:
    """Run key detection on a stem's MIDI."""
    midi_data = _resolve_midi(req.stem_label, session)
    return detect_key(midi_data)


@router.post("/transpose")
def transpose_stem_midi(req: TransposeRequest, session=Depends(get_user_session)) -> dict:
    """Transpose a stem's MIDI → replaces it in the session."""
    midi_data = _resolve_midi(req.stem_label, session)
    transposed = transpose_midi(
        midi_data,
        semitones=req.semitones,
        interval=req.interval,
        key=req.key,
    )
    _store_midi(req.stem_label, transposed, session)
    note_count = sum(len(inst.notes) for inst in transposed.instruments)
    return {
        "stem_label": req.stem_label,
        "note_count": note_count,
        "status": "transposed",
    }


# ── Tier 3: Sheet Music ─────────────────────────────────────────────

@router.post("/sheet-music")
def get_sheet_music(req: SheetMusicRequest, session=Depends(get_user_session)) -> dict:
    """Return MusicXML string for in-browser rendering."""
    midi_data = _resolve_midi(req.stem_label, session)
    musicxml = to_musicxml(
        midi_data,
        quarter_length_divisors=tuple(req.quantize_divisors),
        key=req.key,
        time_signature=req.time_signature,
        title=req.title or req.stem_label,
    )
    return {"musicxml": musicxml, "stem_label": req.stem_label}


@router.post("/sheet-music/pdf")
def get_sheet_music_pdf(req: SheetMusicRequest, session=Depends(get_user_session)):
    """Return PDF file via LilyPond rendering."""
    midi_data = _resolve_midi(req.stem_label, session)
    out_path = MIDI_DIR / f"sheet_{req.stem_label}_{uuid.uuid4().hex[:6]}.pdf"
    to_pdf(
        midi_data,
        out_path,
        quarter_length_divisors=tuple(req.quantize_divisors),
        key=req.key,
        time_signature=req.time_signature,
        title=req.title or req.stem_label,
    )
    return FileResponse(out_path, media_type="application/pdf",
                        filename=f"{req.stem_label}_sheet_music.pdf")


@router.post("/sheet-music/musicxml")
def save_musicxml(req: SheetMusicRequest, session=Depends(get_user_session)):
    """Return MusicXML file for import into external notation software."""
    midi_data = _resolve_midi(req.stem_label, session)
    out_path = MIDI_DIR / f"{req.stem_label}_{uuid.uuid4().hex[:6]}.musicxml"
    to_musicxml_file(
        midi_data,
        out_path,
        quarter_length_divisors=tuple(req.quantize_divisors),
        key=req.key,
        time_signature=req.time_signature,
        title=req.title or req.stem_label,
    )
    return FileResponse(out_path, media_type="application/vnd.recordare.musicxml+xml",
                        filename=f"{req.stem_label}.musicxml")


# ── Helpers ──────────────────────────────────────────────────────────

def _resolve_midi(stem_label: str, session) -> MidiData:
    """Look up PrettyMIDI from session by label."""
    if stem_label == "merged":
        midi_data = session.merged_midi_data
        if midi_data is None:
            raise HTTPException(404, "No merged MIDI available")
        return midi_data
    stem_midi = session.stem_midi_data
    if stem_label not in stem_midi:
        raise HTTPException(404, f"No MIDI for stem '{stem_label}'")
    return stem_midi[stem_label]


def _store_midi(stem_label: str, midi_data: MidiData, session) -> None:
    """Write PrettyMIDI back into session, replacing the original."""
    if stem_label == "merged":
        session.merged_midi_data = midi_data
    else:
        data = session.stem_midi_data
        data[stem_label] = midi_data
        session.stem_midi_data = data
```

**Threading:** All endpoints are synchronous. music21 operations are CPU-bound, typically 1–5 seconds. No GPU lock needed. If large files prove slow, wrap in `job_manager` later.

**Session mutation:** Clean Up and Transpose replace the MIDI in the session store. This is intentional — the FluidSynth preview, Save MIDI, and Sheet Music buttons all operate on the same session data, so cleaning once improves all downstream outputs. The raw extraction result is not preserved (if needed, re-extract).

### LilyPond availability

Add to `backend/api/system.py` health/capabilities:

```python
from utils.music21_bridge import check_lilypond

# Include in /api/health or /api/capabilities response:
"lilypond": check_lilypond()
```

Frontend reads this at init to show/hide PDF buttons. MusicXML preview and all non-PDF features work without LilyPond.

---

## Frontend

### CDN dependency: OpenSheetMusicDisplay (OSMD)

- MIT license, renders MusicXML → SVG in browser via VexFlow
- CDN: `https://cdn.jsdelivr.net/npm/opensheetmusicdisplay@1.8.9/build/opensheetmusicdisplay.min.js`
- Same pattern as wavesurfer.js

Add to `frontend/index.html`:
```html
<script src="https://cdn.jsdelivr.net/npm/opensheetmusicdisplay@1.8.9/build/opensheetmusicdisplay.min.js"></script>
```

### UI changes in `frontend/components/midi.js`

Each MIDI stem card (built by `buildMidiCard()`) gains new controls. The card layout becomes:

```
┌─────────────────────────────────────────────────────────────┐
│ ♪ Bass (MIDI)                                    142 notes  │
│ ┌─────────────────────────────────────────────────────────┐ │
│ │  waveform (FluidSynth render)                           │ │
│ └─────────────────────────────────────────────────────────┘ │
│                                                             │
│ [▶ Play] [⏹ Stop] [⏮]    Instrument: [Electric Bass ▼]    │
│                                                             │
│ ┌─── MIDI Tools ────────────────────────────────────────┐   │
│ │ [🔧 Clean Up]  [🔍 Detect Key]  [↕ Transpose ±]     │   │
│ │ [📄 Sheet Music ▼]  [💾 Save MIDI]  [💾 Save XML]   │   │
│ └───────────────────────────────────────────────────────┘   │
│                                                             │
│ ┌─── Sheet Music Preview (collapsible) ─────────────────┐   │
│ │                                                       │   │
│ │  (OSMD rendered notation)                             │   │
│ │                                                       │   │
│ │  [Download PDF]  [Download MusicXML]                  │   │
│ └───────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

#### Clean Up button

1. POST `/api/midi/clean` with `{stem_label, key, time_signature}` (values from existing form controls on the page)
2. Response: `{note_count, status}`
3. Update the note count display on the card
4. Re-render the FluidSynth preview waveform (the session MIDI changed)
5. Brief "Cleaned ✓" confirmation text, fading after 2s

#### Detect Key button

1. POST `/api/midi/detect-key` with `{stem_label}`
2. Response: `{key: "F major", confidence: 0.86, alternates: [...]}`
3. Update the `#midi-key` selector to the detected key
4. Show detected key + confidence inline: "Detected: F major (86%)"

#### Transpose controls

A small inline row: `[−] [semitone count / interval display] [+]`

Each click:
1. POST `/api/midi/transpose` with `{stem_label, semitones: ±1}`
2. Response: `{note_count, status}`
3. Update the card, re-render waveform preview
4. Show current transposition offset: "Transposed: +3 semitones"

An "interval" dropdown (P5, M3, m3, etc.) is phase 2. Semitone buttons cover the common case.

#### Sheet Music button

Dropdown with three options:
- **Preview** → POST `/api/midi/sheet-music` → render via OSMD in collapsible panel below card
- **Download PDF** → POST `/api/midi/sheet-music/pdf` → browser download (hidden if LilyPond unavailable)
- **Download MusicXML** → POST `/api/midi/sheet-music/musicxml` → browser download

**OSMD rendering:**
```javascript
const container = el('div', { className: 'sheet-music-container' });
const osmd = new opensheetmusicdisplay.OpenSheetMusicDisplay(container, {
    autoResize: true,
    drawTitle: true,
});
await osmd.load(musicxml);
osmd.render();
```

**Merged MIDI:** The existing "Save merged MIDI" button area gets companion "Clean Up All" and "Sheet Music (All)" buttons that operate on `stem_label: "merged"`.

### Notation settings (optional, phase 2)

Expandable section below the MIDI Tools row:

- **Grid resolution:** dropdown → maps to `quantize_divisors`
  - "Standard (16th + triplets)" → `[4, 3]` (default)
  - "Simple (16th only)" → `[4]`
  - "Detailed (32nd + triplets)" → `[8, 3]`
- **Minimum note length:** dropdown
  - "32nd note" → `0.125` (default)
  - "16th note" → `0.25`
  - "8th note" → `0.5`

Low priority — defaults work for most cases.

---

## Dependencies

### Python (add to `pyproject.toml` and `pyproject.toml.MAC`)

```toml
"music21>=9.1,<10",
```

music21 is pure Python, ~50MB installed. No C extensions, no GPU, no CUDA. Runs on all platforms. BSD 3-clause license.

### System (documented, not bundled)

**LilyPond** — required for PDF export only. Everything else works without it.

```bash
# Fedora
sudo dnf install lilypond

# Ubuntu/Debian
sudo apt install lilypond

# macOS
brew install lilypond
```

### Frontend (CDN)

```
opensheetmusicdisplay@1.8.9 — MIT license
```

---

## File inventory

| File | Action | Description |
|------|--------|-------------|
| `utils/music21_bridge.py` | **New** | Core music21 round-trip, cleanup, analysis, export |
| `backend/api/midi.py` | **Edit** | Add 6 new endpoints (clean, detect-key, transpose, sheet-music, sheet-music/pdf, sheet-music/musicxml) |
| `backend/api/system.py` | **Edit** | Add LilyPond availability to capabilities |
| `frontend/index.html` | **Edit** | Add OSMD CDN script tag |
| `frontend/components/midi.js` | **Edit** | Add MIDI Tools row + OSMD rendering to stem cards |
| `frontend/style.css` | **Edit** | Styles for MIDI tools row, sheet music container |
| `pyproject.toml` | **Edit** | Add `music21` dependency |
| `pyproject.toml.MAC` | **Edit** | Add `music21` dependency |
| `ACKNOWLEDGMENTS.md` | **Edit** | Add music21, LilyPond, OSMD credits |
| `THIRD-PARTY-NOTICES.md` | **Edit** | Add license entries |
| `docs/INSTRUCTIONS.md` | **Edit** | Document MIDI tools + sheet music in MIDI section |
| `README.md` | **Edit** | LilyPond in optional dependencies / troubleshooting |

---

## Licensing impact

| Component | License | Integration | Risk |
|-----------|---------|-------------|------|
| music21 | BSD 3-clause | pip dependency | **None** — fully compatible with Apache 2.0 |
| LilyPond | GPL 3.0 | External binary (subprocess) | **None** — output is not derivative; same model as FFmpeg |
| OSMD | MIT | CDN script | **None** |
| VexFlow (OSMD dep) | MIT | Transitive via OSMD | **None** |

No GPL code enters the StemForge codebase. LilyPond is an optional system dependency called via subprocess, identical to how FFmpeg is used for video extraction.

---

## Effort estimate

| Component | Estimate |
|-----------|----------|
| `utils/music21_bridge.py` — core round-trip + clean_midi | 4–5 hours |
| `utils/music21_bridge.py` — detect_key, transpose, detect_tempo | 2–3 hours |
| `utils/music21_bridge.py` — to_musicxml, to_pdf, to_musicxml_file | 2–3 hours |
| Backend endpoints (6 new routes) | 2–3 hours |
| Frontend — MIDI Tools row + button handlers | 3–4 hours |
| Frontend — OSMD integration + sheet music panel | 2–3 hours |
| Testing + edge cases | 3–4 hours |
| Docs + acknowledgments | 1 hour |
| **Total** | **~19–26 hours** |

---

## Risks and mitigations

**music21 quantization quality on BasicPitch output.**
BasicPitch MIDI has clean-ish onsets (grid-aligned by onset threshold) but sometimes produces overlapping notes or micro-notes. Mitigation: post-quantization filter in clean_midi to drop notes < 32nd note, merge tiny rests, and run makeNotation().

**Round-trip fidelity (PrettyMIDI → music21 → PrettyMIDI).**
music21 may alter MIDI data during round-trip — notably, it re-voices overlapping notes, re-quantizes timings, and may drop controller events. Mitigation: clean_midi is an explicit user action, not automatic. Users keep the raw extraction until they choose to clean. GM program numbers and drum flags are stored in the session TrackState (not in the MIDI data), so they survive the round-trip.

**LilyPond not installed.**
PDF export gracefully degrades — the button is hidden or shows an install prompt. MusicXML preview and all cleanup/analysis features work without LilyPond.

**Large MIDI files.**
music21 parsing is CPU-bound and can take several seconds on complex multi-track MIDI. Mitigation: start synchronous, add job_manager wrapping if users report blocking. For sheet music, paginate OSMD rendering or limit initial render to first N measures.

**music21 install size.**
~50MB is nontrivial. The no-corpus variant (`music21` without `music21.corpus`) is available if we want to trim it, but the corpus isn't installed by default via pip — it's downloaded on demand. Standard `pip install music21` is fine.

---

## Implementation order

Recommended build sequence for incremental testability:

1. **`utils/music21_bridge.py`** — `_to_score()`, `_to_pretty_midi()`, `clean_midi()` only. Write unit tests against known BasicPitch output.
2. **`/api/midi/clean` endpoint** — wire up Clean Up button in frontend. Test full round-trip: extract → clean → preview sounds better.
3. **`detect_key()`** + **`/api/midi/detect-key`** — add Detect Key button. Verify against songs with known keys.
4. **`transpose_midi()`** + **`/api/midi/transpose`** — add Transpose buttons. Verify by ear.
5. **`to_musicxml()`** + **`/api/midi/sheet-music`** + OSMD frontend — sheet music preview.
6. **`to_pdf()`** + **`/api/midi/sheet-music/pdf`** — PDF export (requires LilyPond testing).
7. **`to_musicxml_file()`** + **`/api/midi/sheet-music/musicxml`** — MusicXML file export.

Steps 1–4 deliver the most user-facing value (cleaner MIDI) without any notation dependencies. Steps 5–7 add sheet music on top of the cleaned foundation.

---

## Future extensions

- **Auto-clean on extraction** — optional toggle: run clean_midi automatically after BasicPitch extraction, storing both raw and cleaned versions
- **Undo** — store the pre-clean PrettyMIDI in session so users can revert
- **In-browser notation editing** — OSMD cursor/selection API for correcting wrong notes before export
- **Voice separation export** — split polyphonic MIDI into separate single-voice tracks
- **Guitar tablature** — music21 supports tab notation output via LilyPond
- **Batch operations** — clean/transpose all stems at once
- **Tempo detection integration** — feed detect_tempo result back into BasicPitch re-extraction for better quantization alignment
