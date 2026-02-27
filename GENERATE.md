🎤 What the model can do with a vocal stem
These are emergent, indirect behaviors—not guaranteed, but sometimes observable.

Capture rough pitch contour  
If the vocal stem is monophonic and clean, the model may pick up a melodic shape (rise/fall, phrasing). This is similar to giving it a melody WAV, but less reliable because vocals contain formants, consonants, vibrato, and noise that the model was not trained to interpret musically.

Capture coarse rhythm / phrasing  
The timing of syllables can sometimes influence the rhythmic structure of the generated audio. Think of it as “energy envelopes” rather than actual rhythmic transcription.

Influence emotional contour  
A vocal stem with long sustained notes vs. rapid syllables may push the model toward ambient pads vs. rhythmic textures.

Act as a loose “guide track”  
The model may treat the stem as a general audio-conditioning signal, nudging the generation toward similar dynamics or temporal structure.

These effects are weak, inconsistent, and non-literal.

🚫 What the model cannot do with a vocal stem
These limitations are grounded in the model’s documented capabilities and training focus. Stable Audio Open 1.0 is trained on Creative Commons sound effects and field recordings, with only modest instrumental music performance and no explicit vocal modeling. 

It cannot reproduce the voice  
No timbre preservation, no singer identity, no formant modeling.

It cannot generate lyrics or intelligible words  
The model has no linguistic-to-audio singing pathway.

It cannot perform voice conversion  
It won’t turn the input vocal into a new style, gender, or timbre.

It cannot harmonize or accompany the vocal  
It does not align harmonic structure to vocal pitch in a musically aware way.

It cannot keep the vocal in the output  
The VAE + diffusion pipeline generates new audio; it does not mix or retain the input waveform.

It cannot follow polyphonic or noisy stems  
If the stem contains reverb, backing vocals, or artifacts from source separation, the conditioning becomes even less meaningful.

🧠 Why the model behaves this way
Stable Audio Open 1.0’s architecture includes:

A VAE that compresses audio into coarse latents

A T5 text encoder for prompt conditioning

A transformer diffusion model that samples latents

Optional melody/symbolic conditioning pathways (depending on your code path)

But no component is trained to interpret vocal semantics. The model was trained on 7,300 hours of Creative Commons audio, with strong performance on sound effects and field recordings, and only modest instrumental music generation. Vocals are not a major part of the dataset. 

So when you feed a vocal stem, the model treats it as a generic audio feature map—useful only for coarse temporal/pitch cues.

🧪 Practical expectations when using a vocal stem
You should expect:

Loose melodic influence (if monophonic)

Loose rhythmic influence

No intelligible vocal reproduction

No singer identity retention

No lyrical content

No stable alignment between stem and output

In practice, the output will feel like a new piece of audio vaguely shaped by the vocal’s contour, not a remix or transformation of the vocal itself.
