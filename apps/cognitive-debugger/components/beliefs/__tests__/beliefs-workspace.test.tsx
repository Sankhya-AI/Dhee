import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { BeliefsWorkspace } from "@/components/beliefs/beliefs-workspace";

const push = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push }),
}));

vi.mock("framer-motion", () => ({
  motion: {
    tr: ({ children, ...props }: React.ComponentProps<"tr">) => <tr {...props}>{children}</tr>,
    div: ({ children, ...props }: React.ComponentProps<"div">) => <div {...props}>{children}</div>,
  },
}));

const overview = {
  user_id: "u1",
  users: ["u1"],
  counts: {
    active: 1,
    stale: 0,
    contradicted: 0,
    pinned: 0,
    tombstoned: 0,
  },
  contradictions: 0,
  influence: {
    total: 1,
    by_type: { included: 1 },
  },
};

const belief = {
  id: "belief-1",
  claim: "Auth uses JWT rotation",
  domain: "system_state",
  confidence: 0.82,
  status: "held",
  truth_status: "held",
  freshness_status: "current",
  lifecycle_status: "active",
  protection_level: "normal",
  origin: "user",
  successor_id: null,
  created_at: 1712336400,
  updated_at: 1712336400,
  source_memory_ids: [],
  source_episode_ids: [],
  tags: [],
  source_count: 1,
  contradiction_count: 0,
  evidence_for: 1,
  evidence_against: 0,
  stability: 0.9,
  is_listable: true,
};

const api = vi.hoisted(() => ({
  fetchOverview: vi.fn(),
  fetchBeliefs: vi.fn(),
  fetchBeliefDetail: vi.fn(),
  fetchBeliefEvidence: vi.fn(),
  fetchBeliefHistory: vi.fn(),
  fetchBeliefImpact: vi.fn(),
  correctBelief: vi.fn(),
  markStale: vi.fn(),
  pinBelief: vi.fn(),
  tombstoneBelief: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  fetchOverview: api.fetchOverview,
  fetchBeliefs: api.fetchBeliefs,
  fetchBeliefDetail: api.fetchBeliefDetail,
  fetchBeliefEvidence: api.fetchBeliefEvidence,
  fetchBeliefHistory: api.fetchBeliefHistory,
  fetchBeliefImpact: api.fetchBeliefImpact,
  correctBelief: api.correctBelief,
  markStale: api.markStale,
  pinBelief: api.pinBelief,
  tombstoneBelief: api.tombstoneBelief,
}));

describe("BeliefsWorkspace", () => {
  beforeEach(() => {
    push.mockReset();
    api.fetchOverview.mockReset();
    api.fetchBeliefs.mockReset();
    api.fetchBeliefDetail.mockReset();
    api.fetchBeliefEvidence.mockReset();
    api.fetchBeliefHistory.mockReset();
    api.fetchBeliefImpact.mockReset();
    api.correctBelief.mockReset();
    api.markStale.mockReset();
    api.pinBelief.mockReset();
    api.tombstoneBelief.mockReset();

    api.fetchOverview.mockResolvedValue(overview);
    api.fetchBeliefs.mockResolvedValue({
      user_id: "u1",
      total: 1,
      page: 1,
      page_size: 100,
      items: [belief],
    });
    api.fetchBeliefDetail.mockResolvedValue(belief);
    api.fetchBeliefEvidence.mockResolvedValue({
      belief_id: belief.id,
      items: [
        {
          id: "e1",
          belief_id: belief.id,
          content: "Operator confirmed JWT refresh rotation in auth service.",
          supports: true,
          source: "user",
          confidence: 0.9,
          created_at: 1712336400,
        },
      ],
    });
    api.fetchBeliefHistory.mockResolvedValue({
      belief_id: belief.id,
      items: [
        {
          seq: 1,
          id: "h1",
          belief_id: belief.id,
          event_type: "proposed",
          reason: "Imported from operator memory",
          payload: {},
          confidence_after: 0.82,
          truth_status_after: "held",
          created_at: 1712336400,
        },
      ],
    });
    api.fetchBeliefImpact.mockResolvedValue({
      belief_id: belief.id,
      items: [
        {
          id: "i1",
          belief_id: belief.id,
          user_id: "u1",
          influence_type: "included",
          query: "auth incident",
          metadata: { surface: "context" },
          created_at: 1712336400,
        },
      ],
    });
  });

  it("renders belief detail tabs and routes to a corrected successor", async () => {
    api.correctBelief.mockResolvedValue({
      old_belief: belief,
      new_belief: { ...belief, id: "belief-2", claim: "Auth uses cookie sessions" },
    });

    render(<BeliefsWorkspace />);

    expect((await screen.findAllByText("Auth uses JWT rotation")).length).toBeGreaterThan(0);
    expect(screen.getByRole("tab", { name: "Evidence" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "History" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Impact" })).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText("Corrected successor belief text"), {
      target: { value: "Auth uses cookie sessions" },
    });
    fireEvent.change(screen.getByPlaceholderText("Reason for mutation"), {
      target: { value: "Operator verified session migration" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create corrected successor" }));

    await waitFor(() => {
      expect(api.correctBelief).toHaveBeenCalledWith(
        "belief-1",
        "Auth uses cookie sessions",
        "Operator verified session migration",
      );
    });

    await waitFor(() => {
      expect(push).toHaveBeenCalledWith("/beliefs/belief-2");
    });
  });
});
