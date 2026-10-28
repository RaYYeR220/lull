import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";
import { RouteFocus } from "@/components/chrome/RouteFocus";
import { Grain } from "@/components/texture/Room";
import { cx } from "@/lib/cx";
import { MAIN_ID, SITE } from "@/lib/site";
import { SITE_URL } from "@/lib/site-url";
import { figtree, newsreader, newsreaderItalic, plexMono } from "./fonts";
import "./styles/tokens.css";
import "./styles/global.css";

export const metadata: Metadata = {
  // absolute addresses for sharing cards: the site's own when the build knows it
  metadataBase: new URL(SITE_URL ?? "http://localhost:3000"),
  title: { default: SITE.name, template: `%s | ${SITE.name}` },
  description: SITE.description,
  openGraph: { type: "website", siteName: SITE.name, title: SITE.name, description: SITE.description, locale: "en" },
  twitter: { card: "summary_large_image", title: SITE.name, description: SITE.description },
};

export const viewport: Viewport = {
  themeColor: "#13151f",
  colorScheme: "dark",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" data-scroll-behavior="smooth" className={cx(newsreader.variable, newsreaderItalic.variable, figtree.variable, plexMono.variable)}>
      <body>
        <a className="skip" href={`#${MAIN_ID}`}>
          Skip to content
        </a>
        {children}
        <RouteFocus />
        <Grain />
      </body>
    </html>
  );
}
