"""Audio enhancement pipeline — UVR denoise / dereverb via audio-separator.

Wraps the vendored python-audio-separator fork to provide curated
noise-removal and dereverb presets for separated stems.
"""

from __future__ import annotations

import logging
import pathlib
import threading
from dataclasses import dataclass
from typing import Callable

from utils.cache import get_model_cache_dir
from utils.paths import ENHANCE_DIR

log = logging.getLogger("stemforge.pipelines.enhance")

# ---------------------------------------------------------------------------
# Curated presets — model filename → (friendly name, description)
# ---------------------------------------------------------------------------

PRESETS: dict[str, dict] = {
    # ── Denoise (Roformer) ──────────────────────────────────────────────
    "denoise": {
        "label": "Denoise",
        "description": "Remove background noise and bleed-through",
        "model_filename": "denoise_mel_band_roformer_aufr33_sdr_27.9959.ckpt",
        "arch": "roformer",
    },
    "denoise_aggr": {
        "label": "Denoise (Aggressive)",
        "description": "Stronger noise removal — may affect subtle details",
        "model_filename": "denoise_mel_band_roformer_aufr33_aggr_sdr_27.9768.ckpt",
        "arch": "roformer",
    },
    "denoise_debleed": {
        "label": "Denoise + Debleed",
        "description": "Remove noise and cross-stem bleed-through",
        "model_filename": "mel_band_roformer_denoise_debleed_gabox.ckpt",
        "arch": "roformer",
    },
    # ── Dereverb (multiple architectures) ───────────────────────────────
    "dereverb": {
        "label": "Dereverb",
        "description": "Remove room reverb (BS-Roformer)",
        "model_filename": "deverb_bs_roformer_8_384dim_10depth.ckpt",
        "arch": "roformer",
    },
    "dereverb_anvuew": {
        "label": "Dereverb (anvuew)",
        "description": "MelBand dereverb — balanced quality",
        "model_filename": "dereverb_mel_band_roformer_anvuew_sdr_19.1729.ckpt",
        "arch": "roformer",
    },
    "dereverb_echo": {
        "label": "Dereverb + Echo",
        "description": "Remove reverb and echo artifacts",
        "model_filename": "dereverb-echo_mel_band_roformer_sdr_13.4843_v2.ckpt",
        "arch": "roformer",
    },
    "dereverb_mdxc": {
        "label": "Dereverb (MDX23C)",
        "description": "MDX23C architecture dereverb — different character",
        "model_filename": "MDX23C-De-Reverb-aufr33-jarredou.ckpt",
        "arch": "mdxc",
    },
    "dereverb_vr": {
        "label": "Dereverb (Classic UVR)",
        "description": "Classic VR architecture dereverb",
        "model_filename": "UVR-De-Reverb-aufr33-jarredou.pth",
        "arch": "vr",
    },
}


# ---------------------------------------------------------------------------
# Config / Result
# ---------------------------------------------------------------------------

@dataclass
class EnhanceConfig:
    """Per-run configuration for :class:`EnhancePipeline`."""

    preset: str               # key into PRESETS
    output_dir: pathlib.Path = ENHANCE_DIR


@dataclass
class EnhanceResult:
    """Return value from :meth:`EnhancePipeline.run`."""

    output_path: pathlib.Path
    preset: str
    label: str


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

class EnhancePipeline:
    """UVR denoise / dereverb via audio-separator's Separator class."""

    def __init__(self) -> None:
        self._separator = None
        self._loaded_model: str | None = None
        self._lock = threading.Lock()
        self._config: EnhanceConfig | None = None

    def configure(self, config: EnhanceConfig) -> None:
        self._config = config

    def load_model(self, preset_key: str) -> None:
        """Load the model for the given preset.  Reuses if already loaded."""
        preset = PRESETS.get(preset_key)
        if not preset:
            raise ValueError(f"Unknown enhance preset: {preset_key!r}")

        model_filename = preset["model_filename"]

        with self._lock:
            if self._loaded_model == model_filename and self._separator is not None:
                log.info("Model %s already loaded, reusing", model_filename)
                return

            # Clear previous model if different
            if self._separator is not None:
                self.clear()

            log.info("Loading enhance model: %s", model_filename)
            from audio_separator.separator.separator import Separator

            model_dir = str(get_model_cache_dir("uvr"))
            output_dir = str(self._config.output_dir if self._config else ENHANCE_DIR)

            self._separator = Separator(
                model_file_dir=model_dir,
                output_dir=output_dir,
                output_format="WAV",
                normalization_threshold=0.9,
                sample_rate=44100,
            )
            self._separator.load_model(model_filename)
            self._loaded_model = model_filename

    def run(
        self,
        audio_path: str | pathlib.Path,
        preset_key: str,
        progress_cb: Callable[[float, str], None] | None = None,
    ) -> EnhanceResult:
        """Run enhancement on an audio file."""
        preset = PRESETS.get(preset_key)
        if not preset:
            raise ValueError(f"Unknown enhance preset: {preset_key!r}")

        if progress_cb:
            progress_cb(0.05, f"Loading {preset['label']} model...")

        self.load_model(preset_key)

        if progress_cb:
            progress_cb(0.15, f"Processing with {preset['label']}...")

        output_dir = self._config.output_dir if self._config else ENHANCE_DIR
        output_dir.mkdir(parents=True, exist_ok=True)

        # Separator writes output files to its output_dir and returns paths
        self._separator.output_dir = str(output_dir)
        output_files = self._separator.separate(str(audio_path))

        if progress_cb:
            progress_cb(0.95, "Finalizing...")

        if not output_files:
            raise RuntimeError("Enhancement produced no output files")

        # The separator outputs two stems: primary (clean) and secondary (removed).
        # Pick the primary stem — the one NOT containing noise/reverb keywords.
        noise_keywords = {"noise", "reverb", "instrumental"}
        output_path = None
        for f in output_files:
            name_lower = pathlib.Path(f).stem.lower()
            if not any(kw in name_lower for kw in noise_keywords):
                output_path = pathlib.Path(f)
                break

        # Fallback to first output if heuristic didn't match
        if output_path is None:
            output_path = pathlib.Path(output_files[0])

        if not output_path.exists():
            raise RuntimeError(f"Expected output not found: {output_path}")

        log.info("Enhancement complete: %s → %s", audio_path, output_path)

        if progress_cb:
            progress_cb(1.0, "Done")

        return EnhanceResult(
            output_path=output_path,
            preset=preset_key,
            label=preset["label"],
        )

    def clear(self) -> None:
        """Release model resources."""
        with self._lock:
            if self._separator is not None:
                del self._separator
                self._separator = None
                self._loaded_model = None

                import gc
                import torch
                gc.collect()
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()

                log.info("Enhance pipeline cleared")
