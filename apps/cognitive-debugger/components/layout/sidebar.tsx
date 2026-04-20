"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { motion } from "framer-motion";
import {
  ChartBarIcon,
  ExclamationTriangleIcon,
  SparklesIcon,
} from "@heroicons/react/24/outline";
import { ChevronLeft, ChevronRight } from "lucide-react";

import { cn } from "@/lib/utils";

const navItems = [
  { id: "beliefs", label: "Beliefs", href: "/beliefs", icon: SparklesIcon },
  { id: "contradictions", label: "Contradictions", href: "/contradictions", icon: ExclamationTriangleIcon },
  { id: "activity", label: "Activity", href: "/activity", icon: ChartBarIcon },
];

export function Sidebar({
  collapsed,
  onToggle,
}: {
  collapsed: boolean;
  onToggle: () => void;
}) {
  const pathname = usePathname();

  if (collapsed) {
    return (
      <div
        className="group relative mt-4 hidden h-[calc(100vh_-_2rem)] w-14 shrink-0 flex-col items-center bg-[#dbe6e8] lg:flex"
        role="button"
        tabIndex={0}
        onClick={onToggle}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            onToggle();
          }
        }}
      >
        <div className="flex h-14 w-full items-center justify-center">
          <div className="flex h-9 w-9 items-center justify-center rounded-full border border-stone bg-white text-sm font-semibold">
            D
          </div>
          <div className="absolute -right-2 top-5 rounded-full border border-gray-200 bg-white p-1 opacity-0 shadow-sm transition-opacity group-hover:opacity-100">
            <ChevronRight className="h-4 w-4 text-gray-700" />
          </div>
        </div>
        <nav className="mt-6 flex w-full flex-1 flex-col items-center gap-2 px-2">
          {navItems.map((item) => {
            const active = pathname?.startsWith(item.href);
            return (
              <Link
                key={item.id}
                href={item.href}
                onClick={(event) => event.stopPropagation()}
                className={cn(
                  "flex h-10 w-10 items-center justify-center rounded-xl transition-colors",
                  active
                    ? "border border-gray-200 bg-white text-black shadow-sm"
                    : "text-gray-700 hover:border hover:border-gray-200 hover:bg-white hover:text-black hover:shadow-sm",
                )}
              >
                <item.icon className="h-5 w-5" />
              </Link>
            );
          })}
        </nav>
      </div>
    );
  }

  return (
    <div className="group relative mt-4 hidden h-[calc(100vh_-_2rem)] w-64 shrink-0 flex-col lg:flex">
      <div className="flex items-center justify-between px-4 py-4">
        <Link href="/beliefs" className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-full border border-stone bg-white text-sm font-semibold shadow-sm">
            D
          </div>
          <div>
            <div className="text-lg font-semibold text-ink">Dhee</div>
            <div className="text-xs uppercase tracking-[0.16em] text-muted">Cognitive Debugger</div>
          </div>
        </Link>
        <button
          type="button"
          onClick={onToggle}
          className="rounded-md p-1.5 text-muted opacity-0 transition-all hover:bg-white hover:opacity-100 group-hover:opacity-100"
          title="Collapse Sidebar"
        >
          <ChevronLeft className="h-4 w-4" />
        </button>
      </div>
      <nav className="mt-4 flex flex-1 flex-col gap-2 px-5">
        {navItems.map((item) => {
          const active = pathname?.startsWith(item.href);
          return (
            <motion.div
              key={item.id}
              whileHover={{ x: 4 }}
              whileTap={{ scale: 0.98 }}
              transition={{ type: "spring", stiffness: 300, damping: 30 }}
            >
              <Link
                href={item.href}
                className={cn(
                  "relative flex items-center gap-3 rounded-xl p-3 text-sm text-[#6f6d75] transition-colors",
                  active
                    ? "border border-[#e7e7e7] bg-white text-black shadow-md"
                    : "hover:border hover:border-[#e7e7e7] hover:bg-white hover:text-black",
                )}
              >
                {active ? (
                  <motion.div
                    layoutId="active-indicator"
                    className="absolute left-0 top-1/2 h-4 w-0.5 -translate-y-1/2 rounded-full bg-black"
                  />
                ) : null}
                <item.icon className="h-5 w-5" />
                <span>{item.label}</span>
              </Link>
            </motion.div>
          );
        })}
      </nav>
      <div className="mx-5 mb-5 rounded-2xl border border-stone bg-white/80 p-4 shadow-sm">
        <div className="text-xs uppercase tracking-[0.16em] text-muted">Why this exists</div>
        <p className="mt-2 text-sm leading-6 text-ink">
          Inspect what Dhee believes, why it believes it, what conflicts, and how those beliefs change behavior.
        </p>
      </div>
    </div>
  );
}
