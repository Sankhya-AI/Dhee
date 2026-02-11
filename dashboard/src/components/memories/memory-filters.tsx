"use client";

import { useFilterStore } from "@/lib/stores/filter-store";

export function MemoryFilters() {
  const { layer, setLayer } = useFilterStore();

  return (
    <div className="flex items-center gap-3">
      <label className="text-xs text-gray-500">Layer</label>
      <select
        value={layer}
        onChange={(e) => setLayer(e.target.value as "all" | "sml" | "lml")}
        className="rounded-md border border-gray-200 bg-white px-2.5 py-1.5 text-xs text-gray-700 focus:border-purple-300 focus:outline-none focus:ring-1 focus:ring-purple-300"
      >
        <option value="all">All</option>
        <option value="sml">SML</option>
        <option value="lml">LML</option>
      </select>
    </div>
  );
}
