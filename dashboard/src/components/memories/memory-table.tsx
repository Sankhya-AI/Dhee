"use client";

import { LayerBadge } from "@/components/shared/layer-badge";
import { StrengthIndicator } from "@/components/shared/strength-indicator";
import { CategoryPill } from "@/components/shared/category-pill";
import { useInspectorStore } from "@/lib/stores/inspector-store";
import { truncate, timeAgo } from "@/lib/utils/format";
import type { Memory } from "@/lib/types/memory";

export function MemoryTable({ memories }: { memories: Memory[] }) {
  const open = useInspectorStore((s) => s.open);

  return (
    <div className="overflow-x-auto rounded-lg border border-gray-200 bg-white">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-gray-100 bg-gray-50/50">
            <th className="px-4 py-2.5 text-left text-xs font-medium text-gray-500">
              Content
            </th>
            <th className="px-4 py-2.5 text-left text-xs font-medium text-gray-500 w-20">
              Layer
            </th>
            <th className="px-4 py-2.5 text-left text-xs font-medium text-gray-500 w-36">
              Strength
            </th>
            <th className="px-4 py-2.5 text-left text-xs font-medium text-gray-500 w-48">
              Categories
            </th>
            <th className="px-4 py-2.5 text-left text-xs font-medium text-gray-500 w-28">
              Created
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {memories.map((mem) => (
            <tr
              key={mem.id}
              onClick={() => open(mem.id)}
              className="cursor-pointer hover:bg-gray-50 transition-colors"
            >
              <td className="px-4 py-3 text-gray-900">
                {truncate(mem.content, 100)}
              </td>
              <td className="px-4 py-3">
                <LayerBadge layer={mem.layer} />
              </td>
              <td className="px-4 py-3">
                <StrengthIndicator strength={mem.strength} layer={mem.layer} />
              </td>
              <td className="px-4 py-3">
                <div className="flex flex-wrap gap-1">
                  {(mem.categories || []).slice(0, 3).map((cat) => (
                    <CategoryPill key={cat} name={cat} />
                  ))}
                  {(mem.categories || []).length > 3 && (
                    <span className="text-xs text-gray-400">
                      +{mem.categories.length - 3}
                    </span>
                  )}
                </div>
              </td>
              <td className="px-4 py-3 text-xs text-gray-500">
                {timeAgo(mem.created_at)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
