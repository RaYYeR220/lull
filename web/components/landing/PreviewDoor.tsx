"use client";

import { Door } from "@/components/ui/Gate";
import { Glyph } from "@/components/ui/Glyph";
import { useHorizonController, useHorizonState } from "./HorizonProvider";

/** What pressing the door does next, for the silent preview and for real sound. */
const LABELS = {
  visual: { idle: "Preview the timing", playing: "Pause preview", paused: "Resume preview", ended: "Replay preview" },
  audio: { idle: "Hear the difference", playing: "Pause", paused: "Resume", ended: "Hear it again" },
} as const;

/** The gate's lit door: plays, pauses and replays the horizon. */
export function PreviewDoor({ describedBy }: { describedBy?: string }) {
  const controller = useHorizonController();
  const { mode, playing, started, ended } = useHorizonState();
  const phase = playing ? "playing" : ended ? "ended" : started ? "paused" : "idle";
  const icon = playing ? "pause" : mode === "audio" ? "voice" : "play";

  return (
    <Door
      tone="lit"
      icon={<Glyph name={icon} size={icon === "voice" ? 12 : 14} />}
      aria-controls="horizon"
      aria-describedby={describedBy}
      onClick={controller.toggle}
      data-preview-door=""
    >
      {LABELS[mode][phase]}
    </Door>
  );
}
