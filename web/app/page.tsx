import { Footer } from "@/components/chrome/Footer";
import { Nav } from "@/components/chrome/Nav";
import { Benchmarks } from "@/components/landing/Benchmarks";
import { Developers } from "@/components/landing/Developers";
import { Guarantee } from "@/components/landing/Guarantee";
import { Hero } from "@/components/landing/Hero";
import { HowItWorks } from "@/components/landing/HowItWorks";
import { Outputs } from "@/components/landing/Outputs";
import { Problem } from "@/components/landing/Problem";
import { Refusal } from "@/components/landing/Refusal";
import { StudioPreview } from "@/components/landing/StudioPreview";
import { loadBenchmarks } from "@/lib/benchmarks";
import { loadShowcase } from "@/lib/showcase";
import { MAIN_ID, SHOWCASE_ID } from "@/lib/site";

/** The landing: ten sections, every number on it read from the showcase run. */
export default async function Home() {
  const [showcase, benchmarks] = await Promise.all([loadShowcase(SHOWCASE_ID), loadBenchmarks()]);
  return (
    <>
      <Nav />
      <main id={MAIN_ID} tabIndex={-1}>
        <Hero showcase={showcase} />
        <Problem />
        <HowItWorks />
        <Guarantee showcase={showcase} />
        <Refusal showcase={showcase} />
        <Outputs title={showcase.title} />
        <StudioPreview showcase={showcase} />
        <Developers />
        <Benchmarks benchmarks={benchmarks} showcase={showcase} />
      </main>
      <Footer />
    </>
  );
}
