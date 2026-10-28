import type { ReactNode } from "react";
import { Section } from "@/components/ui/Section";
import { cx } from "@/lib/cx";
import styles from "./HowItWorks.module.css";

const STEPS: { verb: string; text: string; model: ReactNode }[] = [
  {
    verb: "Listen",
    text: "Every word gets a start and an end, every speaker a turn. What’s left between them are the pauses.",
    model: (
      <>
        <b>NVIDIA Parakeet TDT</b> + Nemotron Diarization
      </>
    ),
  },
  {
    verb: "Watch",
    text: "The model watches the video itself and lists what a viewer would see, and what the dialogue already says.",
    model: <b>Nemotron 3.5 Super VL</b>,
  },
  {
    verb: "Write",
    text: "For every moment worth describing, up to three versions of the line: short, medium and long.",
    model: <b>Nemotron 3 Ultra</b>,
  },
  {
    verb: "Fit",
    text: "A solver places the lines. Durations are measured from real audio, never guessed by a model.",
    model: (
      <>
        <b>Constraint solver</b>, no model
      </>
    ),
  },
  {
    verb: "Check & speak",
    text: "Each claim is checked against its frames. A line is voiced only if its check passes; one that fails is held for a person.",
    model: (
      <>
        <b>Super VL</b> checks, <b>NVIDIA Magpie</b> speaks
      </>
    ),
  },
];

/** 3. How Lull works: five verbs on one hairline that warms to lamp at the last step. */
export function HowItWorks() {
  return (
    <Section
      id="how"
      eyebrow="How it works"
      heading="How Lull works"
      lede="Five steps, in this order. Models do the seeing and the writing. Code keeps the time."
    >
      <ol className={styles.steps}>
        {STEPS.map((step, i) => (
          <li key={step.verb} className={styles.step}>
            <span className={cx("t-step-index", styles.n)} aria-hidden="true">
              {i + 1}
            </span>
            <span className={styles.node} aria-hidden="true" />
            <h3 className="t-verb">{step.verb}</h3>
            <p className={styles.text}>{step.text}</p>
            <p className={styles.model}>
              <span>{step.model}</span>
            </p>
          </li>
        ))}
      </ol>
    </Section>
  );
}
