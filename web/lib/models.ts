/**
 * Which model does what in a run, as the engine calls them: the Token Factory ids are the
 * engine's own constants (engine/src/lull/tf.py) and the roles are the ones its ledger records;
 * the speech models run on a NeMo-Speech.cpp server (engine/src/lull/speech.py). A licence is
 * given where the project recorded it; otherwise the page says where to read it.
 */
export type ModelRow = {
  stage: string;
  model: string;
  /** The id the engine sends, when it calls the model by id. */
  id: string | null;
  does: string;
  licence: string | null;
  where: "Nebius Token Factory" | "speech server";
};

export const MODELS: ModelRow[] = [
  {
    stage: "Listen",
    model: "Parakeet TDT 0.6B v3",
    id: null,
    does: "Transcribes the speech with a time for every word: where nobody speaks is where a line can go.",
    licence: "CC-BY-4.0",
    where: "speech server",
  },
  {
    stage: "Listen",
    model: "Nemotron 3 Diarization",
    id: null,
    does: "Tells the speakers apart, so a pause is a silence of everyone.",
    licence: "OpenMDW",
    where: "speech server",
  },
  {
    stage: "Watch",
    model: "Nemotron 3.5 Super VL",
    id: "nvidia/Nemotron-3.5-Super-VL",
    does: "Watches the video and lists what a listener would miss, with times (role watch).",
    licence: "OpenMDW-1.1",
    where: "Nebius Token Factory",
  },
  {
    stage: "Plan",
    model: "Nemotron 3 Ultra",
    id: "nvidia/Nemotron-3-Ultra-550b-a55b",
    does: "Reads the whole video's events and transcript at once and drafts lines that fit each pause (role plan, reasoning on).",
    licence: null,
    where: "Nebius Token Factory",
  },
  {
    stage: "Finalize",
    model: "Nemotron 3.5 Super VL",
    id: "nvidia/Nemotron-3.5-Super-VL",
    does: "Checks each line claim by claim against the frames it describes (role verify).",
    licence: "OpenMDW-1.1",
    where: "Nebius Token Factory",
  },
  {
    stage: "Finalize",
    model: "Nemotron 3.5 Lightning",
    id: "nvidia/Nemotron-3_5-Lightning",
    does: "Small rewrites: cuts an unsupported claim, shortens a line that runs long, repairs a writing-rule break (roles remove, compress, repair); also lists names to look up (role research).",
    licence: null,
    where: "Nebius Token Factory",
  },
  {
    stage: "Voice",
    model: "Magpie TTS Multilingual 357M",
    id: null,
    does: "Speaks each line; its measured length decides whether it fits.",
    licence: "NVIDIA Open Model License",
    where: "speech server",
  },
];
