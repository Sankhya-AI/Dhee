"use client";

import { CategoryPill } from "@/components/shared/category-pill";
import type { Memory } from "@/lib/types/memory";
import { NEURAL } from "@/lib/utils/neural-palette";

export function CategoryTab({ memory }: { memory: Memory }) {
  const categories = memory.categories || [];

  if (categories.length === 0) {
    return <p className="text-sm" style={{ color: NEURAL.shallow }}>No categories assigned.</p>;
  }

  return (
    <div className="space-y-4">
      <div>
        <h4 className="text-xs font-medium text-slate-300 mb-2">Categories</h4>
        <div className="flex flex-wrap gap-2">
          {categories.map((cat) => (
            <CategoryPill key={cat} name={cat} />
          ))}
        </div>
      </div>
    </div>
  );
}
