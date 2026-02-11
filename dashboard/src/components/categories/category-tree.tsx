"use client";

import { useState } from "react";
import { ChevronDown, ChevronRight, FolderTree } from "lucide-react";
import { StrengthIndicator } from "@/components/shared/strength-indicator";
import type { Category } from "@/lib/types/category";
import Link from "next/link";

function CategoryNode({ category, depth = 0 }: { category: Category; depth?: number }) {
  const [expanded, setExpanded] = useState(depth < 1);
  const hasChildren = category.children && category.children.length > 0;

  return (
    <div>
      <div
        className="flex items-center gap-2 py-2 px-3 hover:bg-gray-50 rounded-md transition-colors"
        style={{ paddingLeft: `${12 + depth * 20}px` }}
      >
        {hasChildren ? (
          <button onClick={() => setExpanded(!expanded)} className="p-0.5">
            {expanded ? (
              <ChevronDown className="h-3.5 w-3.5 text-gray-400" />
            ) : (
              <ChevronRight className="h-3.5 w-3.5 text-gray-400" />
            )}
          </button>
        ) : (
          <span className="w-4.5" />
        )}

        <FolderTree className="h-3.5 w-3.5 text-purple-500 shrink-0" />

        <span className="text-sm text-gray-900 font-medium flex-1">
          {category.name}
        </span>

        <Link
          href={`/memories?category=${category.id}`}
          className="inline-flex rounded-full bg-gray-100 px-2 py-0.5 text-[10px] font-medium text-gray-600 hover:bg-gray-200"
        >
          {category.memory_count}
        </Link>

        <div className="w-24">
          <StrengthIndicator strength={category.strength} layer="sml" />
        </div>
      </div>

      {hasChildren && expanded && (
        <div>
          {category.children!.map((child) => (
            <CategoryNode key={child.id} category={child} depth={depth + 1} />
          ))}
        </div>
      )}
    </div>
  );
}

export function CategoryTree({ categories }: { categories: Category[] }) {
  return (
    <div className="rounded-lg border border-gray-200 bg-white divide-y divide-gray-100">
      {categories.map((cat) => (
        <CategoryNode key={cat.id} category={cat} />
      ))}
    </div>
  );
}
