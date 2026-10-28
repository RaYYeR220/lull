/**
 * How a WCAG status reads at a glance. Only "met" (every check passed and every published
 * line reviewed by a person) is good; "met (automated checks)" and the engine's other
 * qualified passes stay neutral until a person signs off; "needs review" warns.
 */
export function wcagTone(status: string): "good" | "warn" | "none" {
  if (status === "met") return "good";
  if (status === "needs review") return "warn";
  return "none";
}
