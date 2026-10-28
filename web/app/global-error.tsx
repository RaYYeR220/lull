"use client";

import "./styles/tokens.css";
import "./styles/global.css";

/** The root layout itself failed: a bare page, still in the site's colours, with a way on. */
export default function GlobalError({ reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <html lang="en">
      <body>
        <main id="main" style={{ padding: "96px 24px", maxWidth: 720, margin: "0 auto", display: "grid", gap: 24 }}>
          <h1 className="t-display-2">Lull stopped working.</h1>
          <p className="t-lede">Something went wrong while the page loaded. Try it again, or open a recorded run.</p>
          <p style={{ display: "flex", gap: 16, flexWrap: "wrap", alignItems: "center" }}>
            <button type="button" className="skip" style={{ position: "static", border: 0, cursor: "pointer" }} onClick={() => reset()}>
              Try again
            </button>
            {/* eslint-disable-next-line @next/next/no-html-link-for-pages -- the app itself failed: a full page load, not a client navigation */}
            <a href="/watch" style={{ color: "var(--cream-0)" }}>
              Watch a recorded run
            </a>
          </p>
        </main>
      </body>
    </html>
  );
}
