"use client";

import { useState } from "react";
import { profileTypeColor } from "@/lib/utils/colors";
import type { Profile } from "@/lib/types/profile";
import { cn } from "@/lib/utils/format";

function getInitials(name: string): string {
  return name
    .split(/\s+/)
    .map((w) => w[0])
    .join("")
    .toUpperCase()
    .slice(0, 2);
}

export function ProfileCard({ profile }: { profile: Profile }) {
  const [tab, setTab] = useState<"facts" | "preferences" | "relationships">(
    "facts"
  );
  const color = profileTypeColor(profile.type);

  const tabs = ["facts", "preferences", "relationships"] as const;

  return (
    <div className="rounded-lg border border-gray-200 bg-white p-4">
      <div className="flex items-center gap-3 mb-3">
        <div
          className="flex h-10 w-10 items-center justify-center rounded-full text-white text-sm font-semibold"
          style={{ backgroundColor: color }}
        >
          {getInitials(profile.name)}
        </div>
        <div>
          <p className="text-sm font-medium text-gray-900">{profile.name}</p>
          <span
            className="inline-flex rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase"
            style={{
              backgroundColor: color + "14",
              color: color,
            }}
          >
            {profile.type}
          </span>
        </div>
      </div>

      <div className="flex gap-1 border-b border-gray-100 mb-3">
        {tabs.map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={cn(
              "px-2 py-1.5 text-[10px] font-medium border-b -mb-px transition-colors capitalize",
              tab === t
                ? "border-purple-600 text-purple-700"
                : "border-transparent text-gray-400 hover:text-gray-600"
            )}
          >
            {t}
          </button>
        ))}
      </div>

      <div className="min-h-[60px]">
        {tab === "facts" && (
          <ul className="space-y-1">
            {(profile.facts || []).length === 0 ? (
              <li className="text-xs text-gray-400">No facts recorded</li>
            ) : (
              profile.facts!.map((fact, i) => (
                <li key={i} className="text-xs text-gray-600">
                  {fact}
                </li>
              ))
            )}
          </ul>
        )}
        {tab === "preferences" && (
          <ul className="space-y-1">
            {(profile.preferences || []).length === 0 ? (
              <li className="text-xs text-gray-400">No preferences recorded</li>
            ) : (
              profile.preferences!.map((pref, i) => (
                <li key={i} className="text-xs text-gray-600">
                  {pref}
                </li>
              ))
            )}
          </ul>
        )}
        {tab === "relationships" && (
          <ul className="space-y-1">
            {(profile.relationships || []).length === 0 ? (
              <li className="text-xs text-gray-400">No relationships recorded</li>
            ) : (
              profile.relationships!.map((rel, i) => (
                <li key={i} className="text-xs text-gray-600">
                  <span className="font-medium">{rel.target_name}</span>
                  {" — "}
                  {rel.relationship_type}
                </li>
              ))
            )}
          </ul>
        )}
      </div>
    </div>
  );
}
