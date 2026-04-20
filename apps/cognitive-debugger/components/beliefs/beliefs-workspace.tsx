"use client";

import { Fragment, startTransition, useDeferredValue, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { motion } from "framer-motion";
import { Pin, RefreshCw, Trash2 } from "lucide-react";

import {
  Belief,
  EvidenceRow,
  HistoryRow,
  ImpactRow,
  fetchBeliefDetail,
  fetchBeliefEvidence,
  fetchBeliefHistory,
  fetchBeliefImpact,
  fetchBeliefs,
  fetchOverview,
  correctBelief,
  markStale,
  pinBelief,
  tombstoneBelief,
  type OverviewResponse,
} from "@/lib/api";
import { formatRelativeTime, toPercent } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { StatusBadge } from "@/components/beliefs/status-badge";
import { StatCard } from "@/components/beliefs/stat-card";

type Filters = {
  search: string;
  domain: string;
  truthStatus: string;
  freshnessStatus: string;
  lifecycleStatus: string;
  protectionLevel: string;
  origin: string;
  minConfidence: string;
  maxConfidence: string;
};

const defaultFilters: Filters = {
  search: "",
  domain: "",
  truthStatus: "",
  freshnessStatus: "",
  lifecycleStatus: "",
  protectionLevel: "",
  origin: "",
  minConfidence: "0",
  maxConfidence: "1",
};

export function BeliefsWorkspace({ selectedBeliefId }: { selectedBeliefId?: string }) {
  const router = useRouter();
  const [overview, setOverview] = useState<OverviewResponse | null>(null);
  const [selectedUser, setSelectedUser] = useState<string>("");
  const [beliefs, setBeliefs] = useState<Belief[]>([]);
  const [total, setTotal] = useState(0);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string>("");
  const [detail, setDetail] = useState<Belief | null>(null);
  const [evidence, setEvidence] = useState<EvidenceRow[]>([]);
  const [history, setHistory] = useState<HistoryRow[]>([]);
  const [impact, setImpact] = useState<ImpactRow[]>([]);
  const [filters, setFilters] = useState<Filters>(defaultFilters);
  const [pendingCorrection, setPendingCorrection] = useState("");
  const [pendingReason, setPendingReason] = useState("");
  const deferredSearch = useDeferredValue(filters.search);

  const activeUser = selectedUser || overview?.user_id || "";
  const effectiveSelectedId = selectedBeliefId || beliefs[0]?.id || "";

  const refreshOverview = async (userId?: string) => {
    const data = await fetchOverview(userId);
    setOverview(data);
    setSelectedUser((current) => current || data.user_id);
    return data;
  };

  const refreshBeliefs = async (userId: string) => {
    const response = await fetchBeliefs({
      user_id: userId,
      search: deferredSearch,
      domain: filters.domain,
      truth_status: filters.truthStatus,
      freshness_status: filters.freshnessStatus,
      lifecycle_status: filters.lifecycleStatus,
      protection_level: filters.protectionLevel,
      origin: filters.origin,
      min_confidence: filters.minConfidence,
      max_confidence: filters.maxConfidence,
      page: 1,
      page_size: 100,
    });
    setBeliefs(response.items);
    setTotal(response.total);
    return response.items;
  };

  const refreshDetail = async (beliefId: string) => {
    if (!beliefId) {
      setDetail(null);
      setEvidence([]);
      setHistory([]);
      setImpact([]);
      return;
    }
    const [belief, evidenceResult, historyResult, impactResult] = await Promise.all([
      fetchBeliefDetail(beliefId),
      fetchBeliefEvidence(beliefId),
      fetchBeliefHistory(beliefId),
      fetchBeliefImpact(beliefId),
    ]);
    setDetail(belief);
    setEvidence(evidenceResult.items);
    setHistory(historyResult.items);
    setImpact(impactResult.items);
  };

  useEffect(() => {
    let cancelled = false;
    setIsLoading(true);
    setError("");

    (async () => {
      try {
        const info = await refreshOverview();
        const rows = await refreshBeliefs(info.user_id);
        const nextSelectedId = selectedBeliefId || rows[0]?.id;
        if (!cancelled && nextSelectedId) {
          await refreshDetail(nextSelectedId);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Unable to load beliefs.");
        }
      } finally {
        if (!cancelled) {
          setIsLoading(false);
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!activeUser) return;
    let cancelled = false;
    setIsLoading(true);
    (async () => {
      try {
        await refreshOverview(activeUser);
        const rows = await refreshBeliefs(activeUser);
        const nextSelectedId = selectedBeliefId || rows[0]?.id;
        if (!cancelled) {
          await refreshDetail(nextSelectedId);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Unable to refresh beliefs.");
        }
      } finally {
        if (!cancelled) {
          setIsLoading(false);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [
    activeUser,
    deferredSearch,
    filters.domain,
    filters.truthStatus,
    filters.freshnessStatus,
    filters.lifecycleStatus,
    filters.protectionLevel,
    filters.origin,
    filters.minConfidence,
    filters.maxConfidence,
    selectedBeliefId,
  ]);

  useEffect(() => {
    if (!effectiveSelectedId) return;
    refreshDetail(effectiveSelectedId).catch((err) => {
      setError(err instanceof Error ? err.message : "Unable to load belief detail.");
    });
  }, [effectiveSelectedId]);

  const domains = useMemo(
    () => Array.from(new Set(beliefs.map((belief) => belief.domain))).sort(),
    [beliefs],
  );

  const handleRowClick = (beliefId: string) => {
    startTransition(() => {
      router.push(`/beliefs/${beliefId}`);
    });
  };

  const handleRefresh = async () => {
    if (!activeUser) return;
    setIsLoading(true);
    try {
      await refreshOverview(activeUser);
      await refreshBeliefs(activeUser);
      await refreshDetail(effectiveSelectedId);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to refresh beliefs.");
    } finally {
      setIsLoading(false);
    }
  };

  const mutateDetail = async <T,>(action: () => Promise<T>, onSuccess?: (result: T) => void) => {
    try {
      const result = await action();
      await handleRefresh();
      onSuccess?.(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Mutation failed.");
    }
  };

  return (
    <div className="space-y-5 py-5">
      <div className="rounded-[28px] border border-stone bg-gradient-to-br from-ivory via-white to-mist p-6 shadow-sm">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
          <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-2">
              <StatusBadge kind="origin" value="operator console" />
              <StatusBadge kind="freshness" value="audit first" />
            </div>
            <div>
              <h1 className="text-3xl font-semibold tracking-tight text-ink">
                Cognitive Debugger
              </h1>
              <p className="mt-2 max-w-3xl text-sm text-[#445046]">
                Inspect Dhee&apos;s working beliefs, trace their evidence, review history, and correct state without mutating the past.
              </p>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-3">
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
            <Button variant="outline" onClick={handleRefresh}>
              <RefreshCw className="mr-2 h-4 w-4" />
              Refresh
            </Button>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-5">
        <StatCard label="Active" value={overview?.counts.active ?? 0} helper="Listable beliefs in the current mental state." />
        <StatCard label="Stale" value={overview?.counts.stale ?? 0} helper="Beliefs marked outdated without being declared false." />
        <StatCard label="Contradicted" value={overview?.counts.contradicted ?? 0} helper="Beliefs with unresolved contradiction links." />
        <StatCard label="Pinned" value={overview?.counts.pinned ?? 0} helper="Protected beliefs that should not be casually mutated." />
        <StatCard label="Tombstoned" value={overview?.counts.tombstoned ?? 0} helper="Soft-deleted beliefs retained for auditability." />
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Filters</CardTitle>
          <CardDescription>Search and narrow the current cognition state.</CardDescription>
        </CardHeader>
        <CardContent className="grid grid-cols-1 gap-3 lg:grid-cols-5">
          <input
            value={filters.search}
            onChange={(event) => setFilters((current) => ({ ...current, search: event.target.value }))}
            placeholder="Search belief text"
            className="h-10 rounded-md border border-stone bg-white px-3 text-sm lg:col-span-2"
          />
          <select
            value={filters.domain}
            onChange={(event) => setFilters((current) => ({ ...current, domain: event.target.value }))}
            className="h-10 rounded-md border border-stone bg-white px-3 text-sm"
          >
            <option value="">All domains</option>
            {domains.map((domain) => (
              <option key={domain} value={domain}>
                {domain}
              </option>
            ))}
          </select>
          <select
            value={filters.truthStatus}
            onChange={(event) => setFilters((current) => ({ ...current, truthStatus: event.target.value }))}
            className="h-10 rounded-md border border-stone bg-white px-3 text-sm"
          >
            <option value="">All truth states</option>
            {["proposed", "held", "challenged", "revised", "retracted"].map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
          <select
            value={filters.freshnessStatus}
            onChange={(event) => setFilters((current) => ({ ...current, freshnessStatus: event.target.value }))}
            className="h-10 rounded-md border border-stone bg-white px-3 text-sm"
          >
            <option value="">All freshness</option>
            {["current", "stale", "superseded"].map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
          <select
            value={filters.lifecycleStatus}
            onChange={(event) => setFilters((current) => ({ ...current, lifecycleStatus: event.target.value }))}
            className="h-10 rounded-md border border-stone bg-white px-3 text-sm"
          >
            <option value="">All lifecycle</option>
            {["active", "archived", "tombstoned"].map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
          <select
            value={filters.protectionLevel}
            onChange={(event) => setFilters((current) => ({ ...current, protectionLevel: event.target.value }))}
            className="h-10 rounded-md border border-stone bg-white px-3 text-sm"
          >
            <option value="">All protection</option>
            {["normal", "pinned"].map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
          <select
            value={filters.origin}
            onChange={(event) => setFilters((current) => ({ ...current, origin: event.target.value }))}
            className="h-10 rounded-md border border-stone bg-white px-3 text-sm"
          >
            <option value="">All origins</option>
            {["memory", "user", "observation", "outcome", "correction"].map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
          <input
            value={filters.minConfidence}
            onChange={(event) => setFilters((current) => ({ ...current, minConfidence: event.target.value }))}
            placeholder="Min confidence"
            type="number"
            min="0"
            max="1"
            step="0.05"
            className="h-10 rounded-md border border-stone bg-white px-3 text-sm"
          />
          <input
            value={filters.maxConfidence}
            onChange={(event) => setFilters((current) => ({ ...current, maxConfidence: event.target.value }))}
            placeholder="Max confidence"
            type="number"
            min="0"
            max="1"
            step="0.05"
            className="h-10 rounded-md border border-stone bg-white px-3 text-sm"
          />
        </CardContent>
      </Card>

      {error ? (
        <Card className="border-[#e1c0bb]">
          <CardContent className="py-5 text-sm text-danger">{error}</CardContent>
        </Card>
      ) : null}

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[1.2fr_0.8fr]">
        <Card className="min-h-[640px]">
          <CardHeader>
            <div className="flex items-center justify-between gap-3">
              <div>
                <CardTitle>Beliefs</CardTitle>
                <CardDescription>{total} matching beliefs</CardDescription>
              </div>
              {isLoading ? <div className="text-sm text-muted">Loading…</div> : null}
            </div>
          </CardHeader>
          <CardContent className="soft-scrollbar overflow-x-auto">
            <table className="w-full min-w-[920px] border-separate border-spacing-y-2">
              <thead>
                <tr className="text-left text-xs uppercase tracking-[0.16em] text-muted">
                  <th className="pb-2">Statement</th>
                  <th className="pb-2">Domain</th>
                  <th className="pb-2">Confidence</th>
                  <th className="pb-2">Statuses</th>
                  <th className="pb-2">Updated</th>
                  <th className="pb-2">Sources</th>
                  <th className="pb-2">Conflicts</th>
                  <th className="pb-2">Origin</th>
                </tr>
              </thead>
              <tbody>
                {beliefs.map((belief) => {
                  const selected = belief.id === effectiveSelectedId;
                  return (
                    <motion.tr
                      key={belief.id}
                      initial={{ opacity: 0, y: 8 }}
                      animate={{ opacity: 1, y: 0 }}
                      transition={{ type: "spring", stiffness: 150, damping: 20 }}
                      className={`cursor-pointer rounded-xl border ${
                        selected ? "border-black bg-white shadow-sm" : "border-transparent bg-[#fcfaf6]"
                      }`}
                      onClick={() => handleRowClick(belief.id)}
                    >
                      <td className="rounded-l-xl border-y border-l border-stone px-4 py-4">
                        <div className="font-medium text-ink">{belief.claim}</div>
                      </td>
                      <td className="border-y border-stone px-4 py-4 text-sm text-muted">{belief.domain}</td>
                      <td className="border-y border-stone px-4 py-4">
                        <div className="text-sm font-semibold text-ink">{toPercent(belief.confidence)}</div>
                      </td>
                      <td className="border-y border-stone px-4 py-4">
                        <div className="flex flex-wrap gap-2">
                          <StatusBadge kind="truth" value={belief.truth_status} />
                          <StatusBadge kind="freshness" value={belief.freshness_status} />
                          <StatusBadge kind="lifecycle" value={belief.lifecycle_status} />
                          <StatusBadge kind="protection" value={belief.protection_level} />
                        </div>
                      </td>
                      <td className="border-y border-stone px-4 py-4 text-sm text-muted">{formatRelativeTime(belief.updated_at)}</td>
                      <td className="border-y border-stone px-4 py-4 text-sm text-muted">{belief.source_count}</td>
                      <td className="border-y border-stone px-4 py-4 text-sm text-muted">{belief.contradiction_count}</td>
                      <td className="rounded-r-xl border-y border-r border-stone px-4 py-4">
                        <StatusBadge kind="origin" value={belief.origin} />
                      </td>
                    </motion.tr>
                  );
                })}
              </tbody>
            </table>
          </CardContent>
        </Card>

        <Card className="min-h-[640px]">
          <CardHeader>
            <CardTitle>Belief Detail</CardTitle>
            <CardDescription>
              {detail ? "Trace evidence, revisions, and impact before you mutate state." : "Select a belief to inspect it."}
            </CardDescription>
          </CardHeader>
          <CardContent>
            {detail ? (
              <Fragment>
                <div className="rounded-2xl border border-stone bg-[#fcfaf6] p-4">
                  <div className="text-lg font-semibold text-ink">{detail.claim}</div>
                  <div className="mt-3 flex flex-wrap gap-2">
                    <StatusBadge kind="truth" value={detail.truth_status} />
                    <StatusBadge kind="freshness" value={detail.freshness_status} />
                    <StatusBadge kind="lifecycle" value={detail.lifecycle_status} />
                    <StatusBadge kind="protection" value={detail.protection_level} />
                    <StatusBadge kind="origin" value={detail.origin} />
                  </div>
                  <div className="mt-4 grid grid-cols-2 gap-3 text-sm text-muted">
                    <div>Domain: <span className="font-medium text-ink">{detail.domain}</span></div>
                    <div>Confidence: <span className="font-medium text-ink">{toPercent(detail.confidence)}</span></div>
                    <div>Sources: <span className="font-medium text-ink">{detail.source_count}</span></div>
                    <div>Updated: <span className="font-medium text-ink">{formatRelativeTime(detail.updated_at)}</span></div>
                  </div>
                  <div className="mt-5 flex flex-wrap gap-2">
                    <Button variant="outline" size="sm" onClick={() => mutateDetail(() => markStale(detail.id, pendingReason || "Marked stale from debugger"))}>
                      Mark stale
                    </Button>
                    <Button variant="outline" size="sm" onClick={() => mutateDetail(() => pinBelief(detail.id, detail.protection_level !== "pinned"))}>
                      <Pin className="mr-2 h-3.5 w-3.5" />
                      {detail.protection_level === "pinned" ? "Unpin" : "Pin"}
                    </Button>
                    <Button variant="danger" size="sm" onClick={() => mutateDetail(() => tombstoneBelief(detail.id, pendingReason || "Tombstoned from debugger"))}>
                      <Trash2 className="mr-2 h-3.5 w-3.5" />
                      Tombstone
                    </Button>
                  </div>
                  <div className="mt-4 grid gap-2">
                    <textarea
                      value={pendingCorrection}
                      onChange={(event) => setPendingCorrection(event.target.value)}
                      placeholder="Corrected successor belief text"
                      className="min-h-[90px] rounded-xl border border-stone bg-white px-3 py-3 text-sm"
                    />
                    <input
                      value={pendingReason}
                      onChange={(event) => setPendingReason(event.target.value)}
                      placeholder="Reason for mutation"
                      className="h-10 rounded-xl border border-stone bg-white px-3 text-sm"
                    />
                    <Button
                      onClick={() =>
                        mutateDetail(
                          async () => {
                            const response = await correctBelief(
                              detail.id,
                              pendingCorrection || `${detail.claim} (corrected)`,
                              pendingReason || "Corrected from debugger",
                            );
                            setPendingCorrection("");
                            setPendingReason("");
                            return response;
                          },
                          (response) => {
                            startTransition(() => {
                              router.push(`/beliefs/${response.new_belief.id}`);
                            });
                          },
                        )
                      }
                    >
                      Create corrected successor
                    </Button>
                  </div>
                </div>

                <Tabs defaultValue="evidence">
                  <TabsList className="mt-5">
                    <TabsTrigger value="evidence">Evidence</TabsTrigger>
                    <TabsTrigger value="history">History</TabsTrigger>
                    <TabsTrigger value="impact">Impact</TabsTrigger>
                  </TabsList>
                  <TabsContent value="evidence" className="space-y-3">
                    {evidence.length === 0 ? (
                      <p className="text-sm text-muted">No evidence recorded yet.</p>
                    ) : (
                      evidence.map((item) => (
                        <div key={item.id} className="rounded-xl border border-stone bg-white p-3">
                          <div className="flex items-center justify-between gap-2">
                            <StatusBadge kind="truth" value={item.supports ? "supporting" : "contradicting"} />
                            <div className="text-xs text-muted">{formatRelativeTime(item.created_at)}</div>
                          </div>
                          <p className="mt-2 text-sm leading-6 text-ink">{item.content}</p>
                          <div className="mt-2 text-xs text-muted">
                            Source {item.source} · Confidence {toPercent(item.confidence)}
                          </div>
                        </div>
                      ))
                    )}
                  </TabsContent>
                  <TabsContent value="history" className="space-y-3">
                    {history.length === 0 ? (
                      <p className="text-sm text-muted">No history recorded yet.</p>
                    ) : (
                      history.map((item) => (
                        <div key={item.id} className="rounded-xl border border-stone bg-white p-3">
                          <div className="flex items-center justify-between gap-2">
                            <StatusBadge kind="freshness" value={item.event_type} />
                            <div className="text-xs text-muted">{formatRelativeTime(item.created_at)}</div>
                          </div>
                          <p className="mt-2 text-sm leading-6 text-ink">{item.reason || "No reason recorded."}</p>
                          <div className="mt-2 text-xs text-muted">
                            {item.truth_status_before ? `${item.truth_status_before} → ${item.truth_status_after}` : item.truth_status_after}
                            {item.confidence_after !== null && item.confidence_after !== undefined ? ` · ${toPercent(item.confidence_after)}` : ""}
                          </div>
                        </div>
                      ))
                    )}
                  </TabsContent>
                  <TabsContent value="impact" className="space-y-3">
                    {impact.length === 0 ? (
                      <p className="text-sm text-muted">No impact trace recorded yet.</p>
                    ) : (
                      impact.map((item) => (
                        <div key={item.id} className="rounded-xl border border-stone bg-white p-3">
                          <div className="flex items-center justify-between gap-2">
                            <StatusBadge kind="origin" value={item.influence_type} />
                            <div className="text-xs text-muted">{formatRelativeTime(item.created_at)}</div>
                          </div>
                          <p className="mt-2 text-sm leading-6 text-ink">
                            {item.query || item.answer_fragment || "No query recorded."}
                          </p>
                        </div>
                      ))
                    )}
                  </TabsContent>
                </Tabs>
              </Fragment>
            ) : (
              <p className="text-sm text-muted">No belief selected.</p>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
