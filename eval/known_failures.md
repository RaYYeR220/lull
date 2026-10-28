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

## Lull conveys little of what the human describers chose to say

In the scored Suite B, the primary judge credited Lull's cues (standard and extended, inside
the window) with 1 of JJ Hunt's 124 Elephants Dream facts (0.8 %), 2 of Silvia Pfeiffer's 152
(1.3 %) and 9 of the USGS describers' 198 (4.5 %); one human describer judged against the
other gets 38.8 % and 42.7 %. Over the full film Lull voiced 37.9 words per minute on the
standard track (73.6 with the extended track); Pfeiffer's track has 89.3 and Hunt's 69.4.

## Extended freezes fall inside continuous narration

Suite A pre-registers 0 extended freeze points inside speech for its lectures. On Suite B's
real videos the extended track paused the picture inside speech at 6 of 31 extended cues
(Elephants Dream), 22 of 22 (woodshole), 14 of 14 (aquatic_connectivity) and 1 of 1
(csc_intro), against the reference transcript. In continuous narration the extended track
pauses the picture between words, mid-sentence: the narration is paused, not talked over,
but the sentence is interrupted.

## Suite B woodshole: the standard track voiced nothing

On woodshole the standard track voiced 0 words: none of its cues was verified (5 held, 0
verified); everything Lull said there was on the extended track (22 cues).

## The two Suite B judges disagree

The second judge (Qwen3 235B) credits Lull far more often than the primary one (Nemotron 3
Ultra) on USGS: 17.7 % against 4.5 % of the facts (window, with extended), raw agreement 85.9 %,
kappa 0.31. On Pfeiffer's facts the raw agreement is 96.1 % but kappa is -0.02 (both judges
credit almost nothing, so the agreement is no better than chance). The primary judge's
numbers are the pre-registered ones.

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
from that run, and so does Suite B (see the Suite B deviations).
