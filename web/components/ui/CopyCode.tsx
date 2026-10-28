"use client";

import { useRef, useState } from "react";
import { cx } from "@/lib/cx";
import styles from "./CopyCode.module.css";

/**
 * A code block with a Copy button. The clipboard is used where the browser allows it;
 * otherwise the code is selected so the reader can copy it with their own keys. What happened
 * is said politely beside the button.
 */
export function CopyCode({ code, label, className, lang }: { code: string; /** What the code is, for the button's name. */ label: string; className?: string; lang?: string }) {
  const pre = useRef<HTMLPreElement>(null);
  const [said, setSaid] = useState("");

  const copy = async () => {
    try {
      if (!navigator.clipboard?.writeText) throw new Error("no clipboard");
      await navigator.clipboard.writeText(code);
      setSaid("Copied.");
    } catch {
      const el = pre.current;
      const sel = window.getSelection();
      if (el && sel) {
        sel.removeAllRanges();
        const range = document.createRange();
        range.selectNodeContents(el);
        sel.addRange(range);
      }
      setSaid("Selected: press Ctrl+C (⌘C on a Mac) to copy.");
    }
    window.setTimeout(() => setSaid(""), 4000);
  };

  return (
    <div className={cx(styles.block, className)} data-copy="">
      <pre ref={pre} tabIndex={0} className={styles.pre} data-lang={lang}>
        <code>{code}</code>
      </pre>
      <div className={styles.bar}>
        <span className={styles.label}>
          {label}
        </span>
        <span role="status" className={styles.said}>
          {said}
        </span>
        <button type="button" className={styles.copy} onClick={() => void copy()} aria-label={`Copy ${label.charAt(0).toLowerCase()}${label.slice(1)}`}>
          Copy
        </button>
      </div>
    </div>
  );
}
