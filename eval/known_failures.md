# Known failures (hand-maintained)

Merged into the results report (`python report.py`) after the failures it derives from the
results themselves. One `## ` heading per failure, its text below it; keep each to what the
evaluation has shown.

## Run-to-run variance

Lull's descriptions come from remote models that do not reproduce an answer for a fixed seed
(Nemotron 3.5 Lightning ignored the seed in our checks), so the same video gives different
cues from one run to the next. Each suite scores one pre-registered run (Suite A: the first
complete --all invocation on the final engine) and does not average over runs; the intervals
are bootstrapped over videos or reference lines, not over repeated runs.

## Sparse descriptions compared with human audio description

Lull says less than a human describer. In the Suite B smoke on Elephants Dream 60-150 s it
voiced 55 words per minute (83 with the extended track) against 120 (Silvia Pfeiffer) and 95
(JJ Hunt), and the primary judge credited none of the describers' 46 facts in that clip to its
cues. Lull holds or cuts a description it cannot verify against the frames and fits the
standard track into the programme's own pauses, which leaves room for far fewer words than a
describer who writes for every scene.

## Lecture pauses swallowed before the speech-span fix

Before the engine's corrections the listen stage widened narration spans by 0.2-0.6 s and merged
sentences across planned pauses of about 1.9 s, so a Suite A lecture had only 4-5 speech-free
windows and the standard track voiced little more than slide titles; extended cues froze the
picture before their slide appeared. Found in the Suite A smoke (a01, a02, pre-fix engine) and
fixed in the engine; the scored run is on the fixed engine and the smoke numbers are not
results.

## TTS nondeterminism

Magpie TTS returns slightly different audio for the same sentence from one call to the next
(seed and temperature parameters did not change that in our checks): a01 re-rendered from an
empty cache came out 48.56 s instead of 48.40 s. Suite A's lectures are rendered once and
cached (the render tarball reproduces them bit for bit), and Lull's own cue durations vary
slightly between runs.

## Elephants Dream (full film) failed in publish

The scored recorded run of the full film (final-ed) finished its descriptions (43 verified, 21
held, 43 extended) and then failed while saving keyframes: a cue's keyframe time, 656.3 s, lies
after the last video frame of this file (the video stream ends at 653.8 s, the audio at 658.1
s), because the engine bounds keyframe times by the container's duration rather than the video
stream's. ffmpeg returned no frame and the run stopped without publishing. Counted as a pipeline
failure. The bug was then fixed in the engine (main 0b83117, keyframes kept inside the video
stream) and the full film was run once more (final-ed2); the site's full-film showcase comes
from that run, and Suite B is to be scored on it (see the run log).
