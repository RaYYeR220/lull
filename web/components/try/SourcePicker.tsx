"use client";

import Link from "next/link";
import { useId, useRef, useState, type DragEvent, type Ref } from "react";
import { Door } from "@/components/ui/Gate";
import { Glyph } from "@/components/ui/Glyph";
import {
  LIMITS,
  MODES,
  VOICES,
  budgetEstimate,
  capReached,
  type Budget,
  type Mode,
  type RunInput,
  type RunOptions,
} from "@/lib/api";
import { cx } from "@/lib/cx";
import { watchHref } from "@/lib/routes";
import { clock, megabytes, usdShort } from "@/lib/run-format";
import type { RunSource } from "@/lib/run-state";
import { isoDuration } from "@/lib/timecode";
import shared from "./shared.module.css";
import styles from "./SourcePicker.module.css";

/** A sample the API offers, with what this site knows about it from its recorded run. */
export type SampleItem = { id: string; title: string; credit: string | null; duration: number | null; recorded: boolean };

type FileState =
  | { status: "checking"; file: File }
  | { status: "ready"; file: File; duration: number | null }
  | { status: "refused"; file: File; message: string };

const MODE_COPY: Record<Mode, { name: string; what: string }> = {
  standard: { name: "Standard", what: "Lines fit into the pauses that are already there." },
  extended: { name: "Extended", what: "Also makes a version that holds the picture for lines that need more room." },
};

const ACCEPT = "video/mp4,video/quicktime,video/webm,video/x-matroska,.mp4,.mov,.webm,.mkv";
const FORMATS = "mp4, mov, webm or mkv";

/** The video's length from its metadata, read by a detached <video>; null when the browser can't tell. */
function readDuration(file: File, timeoutMs = 8000): Promise<number | null> {
  return new Promise((resolve) => {
    const url = URL.createObjectURL(file);
    const video = document.createElement("video");
    let timer = 0;
    const done = (d: number | null) => {
      window.clearTimeout(timer);
      video.removeAttribute("src");
      video.load();
      URL.revokeObjectURL(url);
      resolve(d);
    };
    timer = window.setTimeout(() => done(null), timeoutMs);
    video.preload = "metadata";
    video.muted = true;
    video.onloadedmetadata = () => done(Number.isFinite(video.duration) && video.duration > 0 ? video.duration : null);
    video.onerror = () => done(null);
    video.src = url;
  });
}

/** The API's own checks, made before anything is sent: type, size, then length. */
async function checkFile(file: File): Promise<FileState> {
  const dot = file.name.lastIndexOf(".");
  const ext = dot >= 0 ? file.name.slice(dot).toLowerCase() : "";
  if (!(LIMITS.extensions as readonly string[]).includes(ext)) {
    return { status: "refused", file, message: `This isn’t a video Lull takes. Choose an ${FORMATS} file.` };
  }
  if (file.size > LIMITS.maxBytes) {
    return {
      status: "refused",
      file,
      message: `This file is ${megabytes(file.size)}. Live descriptions take files up to ${megabytes(LIMITS.maxBytes)}; trim or compress it first.`,
    };
  }
  const duration = await readDuration(file);
  if (duration !== null && duration > LIMITS.maxSeconds) {
    return {
      status: "refused",
      file,
      message: `This video is ${clock(duration)} long. Live descriptions take up to ${clock(LIMITS.maxSeconds)}; trim it first.`,
    };
  }
  return { status: "ready", file, duration };
}

type Props = {
  /** null while the API is still being asked. */
  samples: SampleItem[] | null;
  service: "loading" | "ok" | "down";
  budget: Budget | null;
  headingRef: Ref<HTMLHeadingElement>;
  onStart: (input: RunInput, source: RunSource, opts: RunOptions) => void;
};

/** Choose a sample or a file, a mode and a voice, then start. */
export function SourcePicker({ samples, service, budget, headingRef, onStart }: Props) {
  const uid = useId();
  const [picked, setPicked] = useState<string | null>(null);
  const [file, setFile] = useState<FileState | null>(null);
  const [mode, setMode] = useState<Mode>("standard");
  const [voice, setVoice] = useState<string>(VOICES[0]);
  const [dragging, setDragging] = useState(false);
  const checks = useRef(0);

  const list = samples ?? [];
  const pickedSample = picked?.startsWith("sample:") ? list.find((s) => `sample:${s.id}` === picked) : undefined;
  // the first sample until the visitor chooses; a choice that disappeared falls back the same way
  const choice = picked === "file" || pickedSample ? picked! : list[0] ? `sample:${list[0].id}` : "file";
  const sample = choice === "file" ? undefined : list.find((s) => `sample:${s.id}` === choice);

  async function take(f: File) {
    setPicked("file");
    const n = ++checks.current;
    setFile({ status: "checking", file: f });
    const result = await checkFile(f);
    if (n === checks.current) setFile(result);
  }

  function onDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setDragging(false);
    const f = e.dataTransfer.files[0];
    if (f) void take(f);
  }

  const duration = sample ? sample.duration : file?.status === "ready" ? file.duration : null;
  const estimate = duration !== null ? budgetEstimate(duration) : null;
  const cap = capReached(budget, estimate ?? 0);

  let blocked: string | null = null;
  if (service === "down") blocked = "The live service isn’t answering, so a description can’t start.";
  else if (service === "loading") blocked = "Checking the live service.";
  else if (cap === "total") blocked = "The live demo’s model budget is spent. Recorded runs still play.";
  else if (cap === "day")
    blocked =
      estimate !== null
        ? `Today’s model budget hasn’t ${usdShort(estimate)} of room left for this clip. Recorded runs still play.`
        : "Today’s model budget is spent. Recorded runs still play.";
  else if (choice === "file") {
    if (!file) blocked = "Choose a video file first.";
    else if (file.status === "checking") blocked = "Checking the file.";
    else if (file.status === "refused") blocked = file.message;
  } else if (!sample) blocked = "Choose a clip first.";

  function start() {
    if (blocked) return;
    const opts: RunOptions = { mode, voice };
    if (sample) onStart({ sample: sample.id }, { kind: "sample", id: sample.id, title: sample.title }, opts);
    else if (file?.status === "ready") onStart({ file: file.file }, { kind: "file", name: file.file.name, bytes: file.file.size }, opts);
  }

  const radioName = `${uid}-clip`;
  const reasonId = `${uid}-reason`;
  const fileStatusId = `${uid}-file-status`;
  const fileInputId = `${uid}-file`;

  return (
    <section aria-labelledby={`${uid}-title`} className={cx(shared.panel, styles.picker)}>
      <div className={styles.clips}>
        <h2 id={`${uid}-title`} ref={headingRef} tabIndex={-1} className={styles.h}>
          Choose a clip
        </h2>
        <fieldset className={styles.set}>
          <legend className="sr-only">Clip to describe</legend>

          {samples === null && service !== "down" ? <p className={styles.wait}>Asking the live service for its samples.</p> : null}
          {service === "down" ? (
            <p className={styles.wait}>Samples come from the live service, which isn’t answering.</p>
          ) : null}

          {list.map((s) => {
            const value = `sample:${s.id}`;
            const id = `${uid}-s-${s.id}`;
            return (
              <div key={s.id} className={cx(styles.option, choice === value && styles.on)}>
                <input
                  type="radio"
                  id={id}
                  name={radioName}
                  value={value}
                  checked={choice === value}
                  onChange={() => setPicked(value)}
                  className={styles.radio}
                />
                <label htmlFor={id} className={styles.optLabel}>
                  <span className={styles.optTitle}>
                    {s.title}
                    {s.duration !== null ? (
                      <time className={cx(shared.mono, styles.dur)} dateTime={isoDuration(s.duration)}>
                        {clock(s.duration)}
                      </time>
                    ) : null}
                  </span>
                  {s.credit ? <span className={styles.optSub}>{s.credit}</span> : null}
                </label>
                {s.recorded ? (
                  <Link href={watchHref(s.id)} prefetch={false} className={cx(shared.link, styles.rec)}>
                    Watch the recorded run<span className="sr-only"> of {s.title}</span>
                  </Link>
                ) : null}
              </div>
            );
          })}

          <div
            className={cx(styles.option, styles.upload, choice === "file" && styles.on, dragging && styles.drag)}
            onDragOver={(e) => {
              e.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={onDrop}
          >
            <input
              type="radio"
              id={`${uid}-own`}
              name={radioName}
              value="file"
              checked={choice === "file"}
              onChange={() => setPicked("file")}
              className={styles.radio}
            />
            <label htmlFor={`${uid}-own`} className={styles.optLabel}>
              <span className={styles.optTitle}>Your own clip</span>
              <span className={styles.optSub}>
                {FORMATS}, up to {megabytes(LIMITS.maxBytes)} and {clock(LIMITS.maxSeconds)} long
              </span>
            </label>
            <div className={styles.drop}>
              <input
                id={fileInputId}
                type="file"
                accept={ACCEPT}
                className={cx("sr-only", styles.fileInput)}
                aria-describedby={fileStatusId}
                onChange={(e) => {
                  const f = e.currentTarget.files?.[0];
                  if (f) void take(f);
                }}
              />
              <label htmlFor={fileInputId} className={cx(shared.btn, styles.choose)}>
                {file ? "Choose another file" : "Choose a file"}
              </label>
              <span className={styles.hint}>or drop a video here</span>
              <p id={fileStatusId} className={styles.fileStatus} aria-live="polite" data-status={file?.status ?? "none"}>
                {file ? (
                  <>
                    <span className={cx(shared.mono, styles.fname)}>{file.file.name}</span>
                    {file.status === "checking" ? <span>Checking the file</span> : null}
                    {file.status === "ready" ? (
                      <span>
                        {megabytes(file.file.size)}
                        {file.duration !== null ? `, ${clock(file.duration)} long` : ", length checked on upload"}
                      </span>
                    ) : null}
                    {file.status === "refused" ? (
                      <span className={styles.refused}>
                        <Glyph name="out" size={10} />
                        {file.message}
                      </span>
                    ) : null}
                  </>
                ) : (
                  <span>No file chosen.</span>
                )}
              </p>
            </div>
          </div>
        </fieldset>
      </div>

      <div className={styles.opts}>
        <fieldset className={styles.modes}>
          <legend className={shared.label}>Mode</legend>
          {MODES.map((m) => (
            <div key={m} className={styles.mode}>
              <input
                type="radio"
                id={`${uid}-m-${m}`}
                name={`${uid}-mode`}
                value={m}
                checked={mode === m}
                onChange={() => setMode(m)}
                className={styles.radio}
                aria-describedby={`${uid}-m-${m}-what`}
              />
              <label htmlFor={`${uid}-m-${m}`} className={styles.optLabel}>
                <span className={styles.optTitle}>{MODE_COPY[m].name}</span>
                <span id={`${uid}-m-${m}-what`} className={styles.optSub}>
                  {MODE_COPY[m].what}
                </span>
              </label>
            </div>
          ))}
        </fieldset>

        <div className={styles.voice}>
          <label htmlFor={`${uid}-voice`} className={shared.label}>
            Voice
          </label>
          <select id={`${uid}-voice`} value={voice} onChange={(e) => setVoice(e.currentTarget.value)} className={styles.select}>
            {VOICES.map((v) => (
              <option key={v} value={v}>
                {v}
              </option>
            ))}
          </select>
        </div>

        <p className={styles.estimate}>
          {estimate !== null
            ? `The spend check needs ${usdShort(estimate)} of budget left for this clip.`
            : "The spend check needs $0.05 of budget a minute, plus $0.02."}
        </p>

        <Door
          tone="lit"
          className={styles.start}
          onClick={start}
          disabled={blocked !== null}
          aria-describedby={blocked ? reasonId : undefined}
          icon={<Glyph name="play" size={11} />}
        >
          {sample ? `Describe ${sample.title}` : "Describe your clip"}
        </Door>
        {blocked ? (
          <p id={reasonId} className={styles.reason}>
            {blocked}
          </p>
        ) : null}
      </div>
    </section>
  );
}
