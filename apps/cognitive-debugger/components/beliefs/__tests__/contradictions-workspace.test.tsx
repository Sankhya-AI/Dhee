import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ContradictionsWorkspace } from "@/components/beliefs/contradictions-workspace";

const api = vi.hoisted(() => ({
  fetchOverview: vi.fn(),
  fetchContradictions: vi.fn(),
  markStale: vi.fn(),
  mergeBeliefs: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  fetchOverview: api.fetchOverview,
  fetchContradictions: api.fetchContradictions,
  markStale: api.markStale,
  mergeBeliefs: api.mergeBeliefs,
}));

describe("ContradictionsWorkspace", () => {
  beforeEach(() => {
    api.fetchOverview.mockReset();
    api.fetchContradictions.mockReset();
    api.markStale.mockReset();
    api.mergeBeliefs.mockReset();

    api.fetchOverview.mockResolvedValue({
      user_id: "u1",
      users: ["u1"],
      counts: {
        active: 2,
        stale: 0,
        contradicted: 2,
        pinned: 0,
        tombstoned: 0,
      },
      contradictions: 1,
      influence: { total: 0, by_type: {} },
    });
    api.fetchContradictions.mockResolvedValue({
      user_id: "u1",
      items: [
        {
          belief_a: {
            id: "belief-a",
            claim: "Repo uses FastAPI",
            domain: "system_state",
            confidence: 0.8,
            truth_status: "held",
            freshness_status: "current",
            lifecycle_status: "active",
            protection_level: "normal",
            updated_at: 1712336400,
          },
          belief_b: {
            id: "belief-b",
            claim: "Repo does not use FastAPI",
            domain: "system_state",
            confidence: 0.6,
            truth_status: "challenged",
            freshness_status: "current",
            lifecycle_status: "active",
            protection_level: "normal",
            updated_at: 1712336400,
          },
          shared_source_overlap: 1,
          recommended_resolution: "keep_a",
        },
      ],
    });
    api.markStale.mockResolvedValue({});
    api.mergeBeliefs.mockResolvedValue({});
  });

  it("resolves a contradiction by marking the losing belief stale", async () => {
    render(<ContradictionsWorkspace />);

    expect(await screen.findByText("Contradictory belief pair")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Keep A" }));

    await waitFor(() => {
      expect(api.markStale).toHaveBeenCalledWith(
        "belief-b",
        "Marked stale in contradiction review; kept belief-a",
      );
    });
  });

  it("merges a contradiction pair into the survivor belief", async () => {
    render(<ContradictionsWorkspace />);

    expect(await screen.findByText("Contradictory belief pair")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Merge into A" }));

    await waitFor(() => {
      expect(api.mergeBeliefs).toHaveBeenCalledWith(
        "belief-a",
        "belief-b",
        "Merged from contradictions review",
      );
    });
  });
});
