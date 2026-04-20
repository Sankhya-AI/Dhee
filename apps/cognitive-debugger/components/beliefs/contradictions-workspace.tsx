"use client";

import { useEffect, useState } from "react";
import { RefreshCw } from "lucide-react";

import {
  fetchContradictions,
  fetchOverview,
  markStale,
  mergeBeliefs,
  type ContradictionRow,
  type OverviewResponse,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { StatusBadge } from "@/components/beliefs/status-badge";
import { formatRelativeTime, toPercent } from "@/lib/utils";

export function ContradictionsWorkspace() {
  const [overview, setOverview] = useState<OverviewResponse | null>(null);
  const [selectedUser, setSelectedUser] = useState("");
  const [items, setItems] = useState<ContradictionRow[]>([]);
  const [error, setError] = useState("");

  const activeUser = selectedUser || overview?.user_id || "";

  const refresh = async (userId?: string) => {
    const info = await fetchOverview(userId);
    setOverview(info);
    setSelectedUser((current) => current || info.user_id);
    const contradictions = await fetchContradictions(info.user_id);
    setItems(contradictions.items);
  };

  useEffect(() => {
    refresh().catch((err) => setError(err instanceof Error ? err.message : "Unable to load contradictions."));
  }, []);

  useEffect(() => {
    if (!activeUser) return;
    refresh(activeUser).catch((err) => setError(err instanceof Error ? err.message : "Unable to load contradictions."));
  }, [activeUser]);

  const resolveKeep = async (survivorId: string, loserId: string) => {
    await markStale(loserId, `Marked stale in contradiction review; kept ${survivorId}`);
    await refresh(activeUser);
  };

  const resolveMerge = async (survivorId: string, loserId: string) => {
    await mergeBeliefs(survivorId, loserId, "Merged from contradictions review");
    await refresh(activeUser);
  };

  return (
    <div className="space-y-5 py-5">
      <div className="flex flex-col gap-4 rounded-[28px] border border-stone bg-gradient-to-br from-ivory via-white to-mist p-6 shadow-sm lg:flex-row lg:items-end lg:justify-between">
        <div>
          <h1 className="text-3xl font-semibold tracking-tight text-ink">Contradictions</h1>
          <p className="mt-2 max-w-3xl text-sm text-[#445046]">
            Review high-overlap beliefs that cannot both be true and resolve them deliberately.
          </p>
        </div>
        <div className="flex gap-3">
          <select
            value={activeUser}
            onChange={(event) => setSelectedUser(event.target.value)}
            className="h-9 rounded-md border border-stone bg-white px-3 text-sm"
          >
            {(overview?.users.length ? overview.users : ["default"]).map((user) => (
              <option key={user} value={user}>
                {user}
              </option>
            ))}
          </select>
          <Button variant="outline" onClick={() => refresh(activeUser)}>
            <RefreshCw className="mr-2 h-4 w-4" />
            Refresh
          </Button>
        </div>
      </div>

      {error ? (
        <Card className="border-[#e1c0bb]">
          <CardContent className="py-5 text-sm text-danger">{error}</CardContent>
        </Card>
      ) : null}

      <div className="grid gap-4">
        {items.length === 0 ? (
          <Card>
            <CardContent className="py-8 text-sm text-muted">No active contradictions for this user.</CardContent>
          </Card>
        ) : (
          items.map((item) => (
            <Card key={`${item.belief_a.id}-${item.belief_b.id}`}>
              <CardHeader>
                <div className="flex flex-wrap items-center gap-2">
                  <StatusBadge kind="freshness" value={item.recommended_resolution} />
                  <StatusBadge kind="origin" value={`${item.shared_source_overlap} shared sources`} />
                </div>
                <CardTitle>Contradictory belief pair</CardTitle>
                <CardDescription>
                  Both beliefs are currently active and surfaced as conflicting claims.
                </CardDescription>
              </CardHeader>
              <CardContent className="grid gap-4 lg:grid-cols-2">
                {[item.belief_a, item.belief_b].map((belief, index) => (
                  <div key={belief.id} className="rounded-2xl border border-stone bg-[#fcfaf6] p-4">
                    <div className="text-xs uppercase tracking-[0.16em] text-muted">Belief {index === 0 ? "A" : "B"}</div>
                    <div className="mt-2 text-lg font-semibold text-ink">{belief.claim}</div>
                    <div className="mt-3 flex flex-wrap gap-2">
                      <StatusBadge kind="truth" value={belief.truth_status} />
                      <StatusBadge kind="freshness" value={belief.freshness_status} />
                      <StatusBadge kind="protection" value={belief.protection_level} />
                    </div>
                    <div className="mt-4 space-y-1 text-sm text-muted">
                      <div>Confidence: <span className="font-medium text-ink">{toPercent(belief.confidence)}</span></div>
                      <div>Domain: <span className="font-medium text-ink">{belief.domain}</span></div>
                      <div>Updated: <span className="font-medium text-ink">{formatRelativeTime(belief.updated_at)}</span></div>
                    </div>
                  </div>
                ))}
                <div className="lg:col-span-2 flex flex-wrap gap-2">
                  <Button variant="outline" onClick={() => resolveKeep(item.belief_a.id, item.belief_b.id)}>
                    Keep A
                  </Button>
                  <Button variant="outline" onClick={() => resolveKeep(item.belief_b.id, item.belief_a.id)}>
                    Keep B
                  </Button>
                  <Button
                    variant="outline"
                    onClick={async () => {
                      await markStale(item.belief_a.id, "Marked stale from contradictions view");
                      await markStale(item.belief_b.id, "Marked stale from contradictions view");
                      await refresh(activeUser);
                    }}
                  >
                    Mark both stale
                  </Button>
                  <Button onClick={() => resolveMerge(item.belief_a.id, item.belief_b.id)}>
                    Merge into A
                  </Button>
                </div>
              </CardContent>
            </Card>
          ))
        )}
      </div>
    </div>
  );
}
