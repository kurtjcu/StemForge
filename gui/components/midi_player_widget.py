"""Simple MIDI preview widget for StemForge.

Renders a MIDI file to audio using FluidSynth + a GM soundfont, then
plays via sounddevice.  Follows the WaveformWidget exclusive-playback
pattern: only one audio source (waveform or MIDI or mix) plays at a time.

Module-level globals
--------------------
_ALL_MIDI_PLAYERS — every live object with tick() + _stop() (includes
                    MidiPlayerWidget instances and MixPanel); tick_all_midi()
                    is called each frame from gui/app.py.

Usage
-----
    widget = MidiPlayerWidget("my_prefix")
    widget.build_ui()        # inside dpg parent context
    widget.load(midi_path)   # start background render
    # tick_all_midi() called each frame by app.py automatically
"""

import pathlib
import logging
import threading
import time

import numpy as np
import dearpygui.dearpygui as dpg

log = logging.getLogger("stemforge.gui.midi_player_widget")

# Duck-typed: any object with tick() and _stop() may be appended here.
# Includes MidiPlayerWidget instances and MixPanel.
_ALL_MIDI_PLAYERS: list = []
_active_midi_player = None  # single exclusive MIDI player


# ---------------------------------------------------------------------------
# Module-level tick / stop
# ---------------------------------------------------------------------------

def tick_all_midi() -> None:
    """Advance every registered MIDI player by one render frame.

    Call this once per frame inside the DearPyGUI render loop.
    """
    for w in _ALL_MIDI_PLAYERS:
        try:
            w.tick()
        except Exception as exc:
            log.debug("midi player tick error: %s", exc)


def stop_all_midi() -> None:
    """Stop playback on every registered MIDI player/mixer."""
    for w in _ALL_MIDI_PLAYERS:
        try:
            w._stop()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Soundfont discovery
# ---------------------------------------------------------------------------

_SF2_SEARCH_PATHS = [
    "/usr/share/soundfonts/FluidR3_GM.sf2",       # Fedora
    "/usr/share/soundfonts/FluidR3_GM2-2.sf2",     # Fedora alt
    "/usr/share/sounds/sf2/FluidR3_GM.sf2",         # Ubuntu
    "/usr/share/sounds/sf2/default-GM.sf2",          # Ubuntu alt
    "/usr/share/soundfonts/default.sf2",             # Arch
]


def find_soundfont() -> "pathlib.Path | None":
    """Return the first GM soundfont found on the system, or None."""
    for p in _SF2_SEARCH_PATHS:
        candidate = pathlib.Path(p)
        if candidate.exists():
            return candidate
    return None


# ---------------------------------------------------------------------------
# Stem → GM program defaults
# ---------------------------------------------------------------------------

_STEM_DEFAULT_PROGRAM: dict[str, int] = {
    "vocals": 52,              # Choir Aahs
    "Singing voice": 52,
    "drums": 0,                # Standard Kit (is_drum=True)
    "Drums & percussion": 0,
    "bass": 33,                # Electric Bass (finger)
    "Bass": 33,
    "other": 48,               # String Ensemble 1
    "Everything else": 48,
    "guitar": 25,              # Acoustic Guitar (steel)
    "Guitar": 25,
    "piano": 0,                # Acoustic Grand Piano
    "Piano": 0,
}

_STEM_IS_DRUM: dict[str, bool] = {
    "drums": True,
    "Drums & percussion": True,
}


# ---------------------------------------------------------------------------
# MidiPlayerWidget
# ---------------------------------------------------------------------------

class MidiPlayerWidget:
    """Simple MIDI preview widget with Play / Stop / Rewind controls.

    Renders the MIDI file to a numpy audio buffer via pretty_midi.fluidsynth()
    on a background thread, then plays back with sounddevice.
    """

    def __init__(self, tag_prefix: str) -> None:
        self._p = tag_prefix
        self._rendered: "np.ndarray | None" = None   # (samples,) float32 mono
        self._sr: int = 44100
        self._duration: float = 0.0
        self._playing: bool = False
        self._play_start: float = 0.0
        self._play_offset: float = 0.0
        self._midi_path: "pathlib.Path | None" = None
        self._stem_label: str = ""
        self._sf2_path: "pathlib.Path | None" = find_soundfont()
        _ALL_MIDI_PLAYERS.append(self)

    # ------------------------------------------------------------------
    # Tag helper
    # ------------------------------------------------------------------

    def _tag(self, name: str) -> str:
        return f"midip_{self._p}_{name}"

    # ------------------------------------------------------------------
    # UI construction  (call inside the target dpg parent context)
    # ------------------------------------------------------------------

    def build_ui(self) -> None:
        """Add MIDI preview controls to the active DPG context."""
        dpg.add_text("MIDI Preview", color=(175, 175, 255, 255))
        with dpg.group(horizontal=True):
            dpg.add_button(
                label="Play",
                tag=self._tag("play_btn"),
                callback=self._on_play,
                width=70,
                enabled=False,
            )
            dpg.add_button(
                label="Stop",
                tag=self._tag("stop_btn"),
                callback=self._on_stop,
                width=70,
                enabled=False,
            )
            dpg.add_button(
                label="<<",
                tag=self._tag("rewind_btn"),
                callback=self._on_rewind,
                width=50,
                enabled=False,
            )
            with dpg.tooltip(dpg.last_item()):
                dpg.add_text("Rewind to start")
            dpg.add_text("", tag=self._tag("time"), color=(160, 160, 160, 255))
        dpg.add_text(
            "Status: (no MIDI loaded)",
            tag=self._tag("status"),
            color=(120, 120, 140, 255),
            wrap=340,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(self, midi_path: pathlib.Path, stem_label: str = "") -> None:
        """Set the MIDI file and start a background render.

        *stem_label* selects the default GM instrument from
        _STEM_DEFAULT_PROGRAM.  If no soundfont is found, shows an
        error in the status text and returns without rendering.
        """
        self.clear()
        self._midi_path = midi_path
        self._stem_label = stem_label
        if self._sf2_path is None:
            self._set_status("Error: no soundfont found. Install fluid-soundfont-gm.")
            return
        self._set_status("Rendering MIDI...")
        threading.Thread(
            target=self._render,
            args=(midi_path, stem_label),
            daemon=True,
        ).start()

    def load_from_midi(self, midi_obj, stem_label: str = "") -> None:
        """Load from an in-memory PrettyMIDI object and start background render.

        Use this instead of :meth:`load` when the MIDI data is already in
        memory (e.g. directly from MidiPipeline.run without disk write).
        """
        self.clear()
        self._stem_label = stem_label
        if self._sf2_path is None:
            self._set_status("Error: no soundfont found. Install fluid-soundfont-gm.")
            return
        self._set_status("Rendering MIDI...")
        threading.Thread(
            target=self._render_from_obj,
            args=(midi_obj, stem_label),
            daemon=True,
        ).start()

    def clear(self) -> None:
        """Stop playback and reset all state."""
        self._stop()
        self._play_offset = 0.0
        self._rendered = None
        self._duration = 0.0
        self._midi_path = None
        self._stem_label = ""
        for btn in (self._tag("play_btn"), self._tag("stop_btn"), self._tag("rewind_btn")):
            if dpg.does_item_exist(btn):
                dpg.configure_item(btn, enabled=False)
        if dpg.does_item_exist(self._tag("time")):
            dpg.set_value(self._tag("time"), "")
        self._set_status("(no MIDI loaded)")

    def tick(self) -> None:
        """Advance playback time display.  Called once per frame by tick_all_midi()."""
        if not self._playing:
            return
        pos = self._play_offset + (time.time() - self._play_start)
        if pos >= self._duration:
            self._stop()
            self._play_offset = 0.0
            pos = 0.0
        if dpg.does_item_exist(self._tag("time")):
            m, s = divmod(int(pos), 60)
            dm, ds = divmod(int(self._duration), 60)
            dpg.set_value(self._tag("time"), f"{m}:{s:02d} / {dm}:{ds:02d}")

    # ------------------------------------------------------------------
    # Background render
    # ------------------------------------------------------------------

    def _render(self, path: pathlib.Path, stem_label: str) -> None:
        try:
            import pretty_midi
            midi = pretty_midi.PrettyMIDI(str(path))
            self._render_from_obj(midi, stem_label)
        except Exception as exc:
            log.error("MidiPlayerWidget render error (%s): %s", path, exc)
            self._set_status(f"Render error: {exc}")

    def _render_from_obj(self, midi_obj, stem_label: str) -> None:
        try:
            import copy
            midi = copy.deepcopy(midi_obj)

            # Apply default GM program for this stem type.
            program = _STEM_DEFAULT_PROGRAM.get(stem_label, 0)
            is_drum = _STEM_IS_DRUM.get(stem_label, False)
            for inst in midi.instruments:
                if is_drum:
                    inst.is_drum = True
                elif not inst.is_drum:
                    inst.program = program

            audio = midi.fluidsynth(fs=self._sr, sf2_path=str(self._sf2_path))
            self._rendered = audio
            self._duration = len(audio) / self._sr

            if dpg.does_item_exist(self._tag("play_btn")):
                dpg.configure_item(self._tag("play_btn"), enabled=True)
            if dpg.does_item_exist(self._tag("rewind_btn")):
                dpg.configure_item(self._tag("rewind_btn"), enabled=True)

            dm, ds = divmod(int(self._duration), 60)
            self._set_status(f"Ready  ({dm}:{ds:02d})")

        except Exception as exc:
            log.error("MidiPlayerWidget render error: %s", exc)
            self._set_status(f"Render error: {exc}")

    # ------------------------------------------------------------------
    # Button callbacks
    # ------------------------------------------------------------------

    def _on_play(self, sender, app_data, user_data) -> None:
        self._start_play(self._play_offset)

    def _on_stop(self, sender, app_data, user_data) -> None:
        self._stop()

    def _on_rewind(self, sender, app_data, user_data) -> None:
        if self._playing:
            self._start_play(0.0)
        else:
            self._play_offset = 0.0
            if dpg.does_item_exist(self._tag("time")) and self._duration > 0:
                dm, ds = divmod(int(self._duration), 60)
                dpg.set_value(self._tag("time"), f"0:00 / {dm}:{ds:02d}")

    # ------------------------------------------------------------------
    # Playback internals
    # ------------------------------------------------------------------

    def _start_play(self, offset: float = 0.0) -> None:
        global _active_midi_player

        # Stop all waveform widgets (cross-exclusion).
        from gui.components.waveform_widget import stop_all as stop_all_waveforms
        stop_all_waveforms()

        # Stop all other MIDI players / mix panel.
        for w in _ALL_MIDI_PLAYERS:
            if w is not self:
                try:
                    w._stop()
                except Exception:
                    pass
        _active_midi_player = self

        if self._rendered is None:
            return

        self._play_offset = offset
        self._play_start = time.time()
        self._playing = True

        if dpg.does_item_exist(self._tag("stop_btn")):
            dpg.configure_item(self._tag("stop_btn"), enabled=True)

        audio = self._rendered
        sr = self._sr
        start_sample = int(offset * sr)

        def _play_audio() -> None:
            try:
                import sounddevice as sd
                sd.play(audio[start_sample:], samplerate=sr)
            except Exception as exc:
                log.error("MidiPlayerWidget playback error: %s", exc)

        threading.Thread(target=_play_audio, daemon=True).start()

    def _stop(self) -> None:
        global _active_midi_player
        if self._playing:
            self._play_offset = min(
                self._play_offset + (time.time() - self._play_start),
                self._duration,
            )
            self._playing = False
            try:
                import sounddevice as sd
                sd.stop()
            except Exception:
                pass
        if _active_midi_player is self:
            _active_midi_player = None
        if dpg.does_item_exist(self._tag("stop_btn")):
            dpg.configure_item(self._tag("stop_btn"), enabled=False)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _set_status(self, msg: str) -> None:
        tag = self._tag("status")
        if dpg.does_item_exist(tag):
            dpg.set_value(tag, f"Status: {msg}")
