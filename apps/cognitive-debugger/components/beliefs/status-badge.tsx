import { Badge } from "@/components/ui/badge";

export function StatusBadge({
  kind,
  value,
}: {
  kind: "truth" | "freshness" | "lifecycle" | "protection" | "origin";
  value: string;
}) {
  const normalized = value.toLowerCase();
  let tone: "neutral" | "success" | "warning" | "danger" | "info" = "neutral";

  if (kind === "truth") {
    if (normalized === "held" || normalized === "revised") tone = "success";
    if (normalized === "challenged") tone = "warning";
    if (normalized === "retracted") tone = "danger";
  }
  if (kind === "freshness") {
    if (normalized === "current") tone = "info";
    if (normalized === "stale") tone = "warning";
    if (normalized === "superseded") tone = "danger";
  }
  if (kind === "lifecycle") {
    if (normalized === "active") tone = "success";
    if (normalized === "archived") tone = "warning";
    if (normalized === "tombstoned") tone = "danger";
  }
  if (kind === "protection") {
    tone = normalized === "pinned" ? "info" : "neutral";
  }
  if (kind === "origin") {
    tone = normalized === "user" ? "info" : "neutral";
  }

  return <Badge tone={tone}>{value.replaceAll("_", " ")}</Badge>;
}
