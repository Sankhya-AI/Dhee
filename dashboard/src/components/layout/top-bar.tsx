"use client";

import { Search } from "lucide-react";
import { useFilterStore } from "@/lib/stores/filter-store";
import { useCallback, useState } from "react";
import { NEURAL } from "@/lib/utils/neural-palette";

export function TopBar() {
  const setSearchQuery = useFilterStore((s) => s.setSearchQuery);
  const [value, setValue] = useState("");

  const handleSubmit = useCallback(
    (e: React.FormEvent) => {
      e.preventDefault();
      setSearchQuery(value);
    },
    [value, setSearchQuery]
  );

  return (
    <header
      className="flex h-14 items-center gap-4 border-b px-6"
      style={{
        backgroundColor: NEURAL.cortex,
        borderColor: `rgba(124, 58, 237, 0.12)`,
      }}
    >
      <form onSubmit={handleSubmit} className="relative flex-1 max-w-md">
        <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-4 w-4" style={{ color: NEURAL.shallow }} />
        <input
          type="text"
          placeholder="Search memories..."
          value={value}
          onChange={(e) => setValue(e.target.value)}
          className="w-full rounded-lg py-1.5 pl-9 pr-3 text-sm placeholder:text-slate-500 focus:outline-none focus:ring-1"
          style={{
            backgroundColor: NEURAL.synapse,
            color: '#e2e8f0',
            border: `1px solid rgba(124, 58, 237, 0.12)`,
          }}
        />
      </form>
    </header>
  );
}
