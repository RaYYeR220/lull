# Licences of the reference data in this folder

These files are derived from third-party works and keep those works' licences (the code that
made them, `suite_b/*.py`, is under the repository's licence). Changes made: WebVTT cues
parsed into timed lines with their markup removed; USGS soundtracks transcribed by a speech
recogniser, aligned, cut into the describer's sentences and corrected where on-screen text or
narration gave the right words (`asr_corrections.json`).

| what | derived from | licence | attribution |
|---|---|---|---|
| `elephants_dream.json`, `elephants_dream.md`: the lines with source `pfeiffer` | Elephants Dream audio description track (WebVTT), http://html5videoguide.net/presentations/LCA_MM_Miniconf2013/elephant.audiodescriptions.vtt | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) | Silvia Pfeiffer |
| `elephants_dream.json`, `elephants_dream.md`: the lines with source `hunt` and `hunt_audio` (the voiced intervals of his recording) | Text Description Track for Elephants Dream and its voiced recording, https://github.com/OwenEdwards/ElephantsDreamAudioDescription | [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/); these parts are shared under the same licence | JJ Hunt (http://www.jjhunt.com) |
| `elephants_dream.json`: the film's metadata and the timeline check | Elephants Dream (2006), https://commons.wikimedia.org/wiki/File:Elephants_Dream_(2006).webm | [CC BY 2.5](https://creativecommons.org/licenses/by/2.5/) | Orange Open Movie Team / Blender Foundation |
| `woodshole.*`, `aquatic_connectivity.*`, `csc_intro.*` | USGS videos and their audio-described versions (URLs in each file) | public domain (U.S. Geological Survey) | U.S. Geological Survey |
| `asr_corrections.json` | on-screen text of frames of the USGS woodshole video | public domain (U.S. Geological Survey) | U.S. Geological Survey |

The transcripts are in `asr/` (see `asr/NOTICE.md`).
