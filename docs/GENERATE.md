# Generate Tab — Stable Audio Open

StemForge's Generate tab produces new audio using
[Stable Audio Open 1.0](https://huggingface.co/stabilityai/stable-audio-open-1.0),
a latent-diffusion model that generates stereo 44.1 kHz audio from text prompts.

---

## Quick start

1. **Accept the model license** at
   https://huggingface.co/stabilityai/stable-audio-open-1.0
   (free HuggingFace account required).
2. Create a Read token at https://huggingface.co/settings/tokens.
3. Run `huggingface-cli login` and paste the token.
4. Open StemForge → Generate tab → type a prompt → click **Generate**.

Weights (~2 GB) are downloaded on the first run and cached under
`~/.cache/stemforge/musicgen/`.

---

## Conditioning modes

### Text prompt (always required)

A natural-language description of the audio to generate.
Good prompts are specific about instrumentation, genre, tempo, and mood.

### Audio conditioning (optional)

Load any audio file (typically a separated stem) as `init_audio_path`.
The file is resampled to 44.1 kHz; the VAE encodes it into latents that
seed the diffusion process alongside the text conditioning.

In standard mode, audio conditioning is applied to the **first chunk only**
so the reference timbre is not repeated on every 47 s boundary.

### MIDI conditioning (optional)

Load a MIDI file as `midi_path`. StemForge extracts BPM, key signature,
and General MIDI instrument families, then appends them as comma-separated
tags to the text prompt. The model receives these as language cues — this
is prompt enrichment, not symbolic conditioning.

All three sources can be combined in a single generation.

---

## Parameters

| Parameter | Default | Description |
|---|---|---|
| Duration | 30 s | Target length. Durations > 47 s are split into equal chunks and crossfaded. Max 600 s. |
| Steps | 100 | Diffusion sampling steps. More = higher quality, slower. |
| CFG scale | 7.0 | Classifier-free guidance. Higher = more prompt-faithful. |
| Negative prompt | `low quality, distorted, noise, clipping` | Text describing what to avoid. |

---

## Vocal Preservation Mode

An optional mode for generation conditioned on a vocal or melodic stem.
Enable it in the GUI to access the following controls:

| Control | Default | Description |
|---|---|---|
| Conditioning strength | 0.7 | Scales init audio amplitude before VAE encoding. 1.0 = full reference, 0.0 = no audio conditioning. |
| Timing lock | On | Divides source audio into windows and generates each separately, preserving rhythmic alignment. Windows are joined with a 50 ms crossfade. |
| Window size | 10 s | Window length for timing-locked generation. Clamped to ≤ 47 s. |

When Vocal Preservation is enabled but no audio conditioning source is
provided, it degrades gracefully to negative-prompt-only mode.

---

## Chunked generation

Stable Audio Open generates at most 47 seconds per call. For longer
durations, StemForge splits the request into equal chunks (each ≤ 47 s)
and concatenates them with a crossfade. This is transparent — just set
the desired duration up to 600 s.

---

## Vocal stems as conditioning input

Stable Audio Open 1.0 was trained on ~7,300 hours of Creative Commons
sound effects and field recordings, with modest instrumental music
coverage and no explicit vocal modeling. When you feed a vocal stem as
audio conditioning, the model treats it as a generic audio feature map.

### What the model can do with a vocal stem

These are emergent, indirect behaviors — not guaranteed, but sometimes
observable:

- **Rough pitch contour** — if the vocal is monophonic and clean, the
  model may pick up a melodic shape (rise/fall, phrasing). Less reliable
  than a pure melody WAV because vocals contain formants, consonants,
  vibrato, and noise the model was not trained to interpret musically.
- **Coarse rhythm / phrasing** — syllable timing can sometimes influence
  the rhythmic structure. Think "energy envelopes" rather than rhythmic
  transcription.
- **Emotional contour** — long sustained notes vs. rapid syllables may
  push the model toward ambient pads vs. rhythmic textures.
- **Loose guide track** — the stem acts as a general audio-conditioning
  signal, nudging generation toward similar dynamics or temporal structure.

These effects are weak, inconsistent, and non-literal.

### What the model cannot do with a vocal stem

- Cannot reproduce the voice (no timbre preservation, no singer identity,
  no formant modeling)
- Cannot generate lyrics or intelligible words
- Cannot perform voice conversion
- Cannot harmonize or accompany the vocal in a musically aware way
- Cannot retain the input waveform in the output
- Cannot follow polyphonic or noisy stems (reverb, backing vocals, or
  separation artifacts degrade conditioning further)

### Why

The architecture (VAE → T5 text encoder → transformer diffusion) has no
component trained to interpret vocal semantics. The model was trained
primarily on sound effects and field recordings, so a vocal stem is just
a generic feature map — useful for coarse temporal/pitch cues at best.

### Practical expectations

In practice the output will feel like a new piece of audio vaguely shaped
by the vocal's contour, not a remix or transformation of the vocal itself.
Expect loose melodic and rhythmic influence, no intelligible vocal
reproduction, no singer identity retention, and no stable alignment
between the stem and the output.
