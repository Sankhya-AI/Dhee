"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  LayoutDashboard,
  Sparkles,
  List,
  Film,
  FolderTree,
  Users,
  AlertTriangle,
  GitBranch,
} from "lucide-react";
import { cn } from "@/lib/utils/format";

const NAV_ITEMS = [
  { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { href: "/constellation", label: "Constellation", icon: Sparkles },
  { href: "/memories", label: "Memories", icon: List },
  { href: "/scenes", label: "Scenes", icon: Film },
  { href: "/categories", label: "Categories", icon: FolderTree },
  { href: "/profiles", label: "Profiles", icon: Users },
  { href: "/conflicts", label: "Conflicts", icon: AlertTriangle },
  { href: "/staging", label: "Staging", icon: GitBranch },
];

export function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="flex h-screen w-56 flex-col border-r border-gray-200 bg-white">
      <div className="flex h-14 items-center gap-2 border-b border-gray-200 px-4">
        <div className="h-7 w-7 rounded-lg bg-purple-600 flex items-center justify-center">
          <span className="text-white text-xs font-bold">E</span>
        </div>
        <span className="text-sm font-semibold text-gray-900">Engram</span>
      </div>

      <nav className="flex-1 overflow-y-auto px-2 py-3 space-y-0.5">
        {NAV_ITEMS.map(({ href, label, icon: Icon }) => {
          const active = pathname === href || pathname?.startsWith(href + "/");
          return (
            <Link
              key={href}
              href={href}
              className={cn(
                "flex items-center gap-2.5 rounded-md px-2.5 py-2 text-sm transition-colors",
                active
                  ? "bg-purple-50 text-purple-700 font-medium"
                  : "text-gray-600 hover:bg-gray-50 hover:text-gray-900"
              )}
            >
              <Icon className="h-4 w-4 shrink-0" />
              {label}
            </Link>
          );
        })}
      </nav>

      <div className="border-t border-gray-200 px-4 py-3">
        <p className="text-[10px] text-gray-400 uppercase tracking-wider">
          Memory Kernel v2
        </p>
      </div>
    </aside>
  );
}
