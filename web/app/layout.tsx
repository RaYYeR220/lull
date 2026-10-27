import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";
import { Grain } from "@/components/texture/Room";
import { cx } from "@/lib/cx";
import { MAIN_ID, SITE } from "@/lib/site";
import { figtree, newsreader, plexMono } from "./fonts";
import "./styles/tokens.css";
import "./styles/global.css";

export const metadata: Metadata = {
  title: { default: SITE.name, template: `%s | ${SITE.name}` },
  description: SITE.description,
};

export const viewport: Viewport = {
  themeColor: "#13151f",
  colorScheme: "dark",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" data-scroll-behavior="smooth" className={cx(newsreader.variable, figtree.variable, plexMono.variable)}>
      <body>
        <a className="skip" href={`#${MAIN_ID}`}>
          Skip to content
        </a>
        {children}
        <Grain />
      </body>
    </html>
  );
}
