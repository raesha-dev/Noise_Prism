# Local dataset preparation and data policy

## GramVaani real-telephone corpus

The local `GV_Dev_5h` and `GV_Eval_3h` directories contain MP3 audio,
utterance IDs, organizer text, and metadata. No speaker identifier field was
found in the inspected metadata. Imported rows therefore have blank speaker
IDs and use the organizer-provided Dev/Eval separation.

The current local manifest contains 1,132 Dev and 560 Eval clips. Every
manifest row has a nonempty reference and a corresponding audio file; imported
audio is native 8 kHz and between 2 and 15 seconds, below the REST limit of
30 seconds. These are preparation counts, not ASR results. References come
from the organizer-provided text file and may contain annotation errors.

The importer tags this corpus `hi-IN` based on corpus-level script inspection;
this is not per-clip language identification. No benchmark or test-split
transcription has been run. Keep audio and transcripts in ignored local
`bharatnoise_api/data/`; do not commit, publish, copy into samples, or show in
the UI. Verify current organizer terms before external use; the importer
labels the use as academic-only and prohibits redistribution.

## Team speech (synthetic TTS)

The owner-reviewed `voice_group_review_FILLED.csv` contains 54 synthetic TTS
clips and confirmed synthetic voice IDs. It assigns 31 clips to Dev and 23 to
Test, with one voice kept in one split across languages. This is
`voice_disjoint_synthetic`, not speaker-disjoint human speech. Its references
are the human-authored scripts. The TTS engine/provider and voice names still
need owner confirmation:

- TTS engine/provider: **not recorded**
- Voice names/model identifiers: **not recorded**
- Provider terms for publishing audio: **not checked**
- Real-person voice cloning: **not confirmed**

Check provider terms before publishing any audio. If any voice is cloned from a
real person, document their consent before any publication. Team audio remains
local-only regardless. Report synthetic speech as a separate source type and
never pool its results with GramVaani.

The checked/imported team rows were used to create 62 Dev-only synthetic
mixtures from 31 independent Dev source clips. Team Test was not used.
Development mixtures have clean references and can support measured SNR and
SI-SDR comparisons. Those metrics do not establish ASR improvement.

## DEMAND noise

The local noise license manifest lists 2,400 ten-second segments, 480 for each
of five noise types, totaling 400 minutes. Segment time regions are separated
between Dev and Test. The importer records a conservative CC BY-SA 3.0
assumption and attribution reminder; verify original corpus terms before
external use. Do not redistribute source noise or mixtures.

## Code-mixed speech

[`bharatnoise_api/data/team/codemix_script.csv`](../bharatnoise_api/data/team/codemix_script.csv)
is an empty header-only template. No sentences or audio have been supplied,
so code-mixed accuracy is not measured. The owner should preserve the typed
references and script convention exactly; do not auto-transliterate.

## Aggregate provenance

The local `bharatnoise_api/data/PROVENANCE.md` is generated from manifests and
contains aggregate counts only. Raw archives, audio, transcript references,
clip IDs, and benchmark CSVs remain ignored local data. No audio or transcript
is included in this repository's samples or public result report.
