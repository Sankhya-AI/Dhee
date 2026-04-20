"use client";

import { useEffect, useState } from "react";
import { RefreshCw } from "lucide-react";

import { fetchActivity, fetchOverview, type ActivityRow, type OverviewResponse } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { StatusBadge } from "@/components/beliefs/status-badge";
import { formatRelativeTime } from "@/lib/utils";

export function ActivityWorkspace() {
  const [overview, setOverview] = useState<OverviewResponse | null>(null);
  const [selectedUser, setSelectedUser] = useState("");
  const [items, setItems] = useState<ActivityRow[]>([]);
  const [error, setError] = useState("");

  const activeUser = selectedUser || overview?.user_id || "";

  const refresh = async (userId?: string) => {
    const info = await fetchOverview(userId);
    setOverview(info);
    setSelectedUser((current) => current || info.user_id);
    const activity = await fetchActivity(info.user_id);
    setItems(activity.items);
  };

  useEffect(() => {
    refresh().catch((err) => setError(err instanceof Error ? err.message : "Unable to load activity."));
  }, []);

  useEffect(() => {
    if (!activeUser) return;
    refresh(activeUser).catch((err) => setError(err instanceof Error ? err.message : "Unable to load activity."));
  }, [activeUser]);

  return (
    <div className="space-y-5 py-5">
      <div className="flex flex-col gap-4 rounded-[28px] border border-stone bg-gradient-to-br from-ivory via-white to-mist p-6 shadow-sm lg:flex-row lg:items-end lg:justify-between">
        <div>
          <h1 className="text-3xl font-semibold tracking-tight text-ink">Activity</h1>
          <p className="mt-2 max-w-3xl text-sm text-[#445046]">
            Recent belief mutations and coarse influence traces, sorted by recency.
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

      <Card>
        <CardHeader>
          <CardTitle>Recent activity</CardTitle>
          <CardDescription>Belief events and influence traces from the append-only audit log.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {items.length === 0 ? (
            <p className="text-sm text-muted">No activity recorded yet.</p>
          ) : (
            items.map((item) => (
              <div key={item.id} className="rounded-2xl border border-stone bg-[#fcfaf6] p-4">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="flex flex-wrap items-center gap-2">
                    <StatusBadge kind="origin" value={item.row_type} />
                    <StatusBadge kind="freshness" value={item.kind} />
                  </div>
                  <div className="text-xs text-muted">{formatRelativeTime(item.created_at)}</div>
                </div>
                <div className="mt-3 text-base font-semibold text-ink">{item.belief_claim || "Unknown belief"}</div>
                <div className="mt-1 text-sm text-muted">{item.reason || "No reason recorded."}</div>
                <div className="mt-2 text-xs uppercase tracking-[0.14em] text-muted">
                  {item.actor || "system"} · {item.belief_domain || "unknown domain"}
                </div>
              </div>
            ))
          )}
        </CardContent>
      </Card>
    </div>
  );
}
