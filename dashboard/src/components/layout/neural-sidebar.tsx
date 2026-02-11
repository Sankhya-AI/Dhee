"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Brain,
  Scan,
  Cable,
  Moon,
  Users,
} from "lucide-react";
import { cn } from "@/lib/utils/format";
import { NEURAL } from "@/lib/utils/neural-palette";

const NAV_ITEMS = [
  { href: "/", label: "Brain", icon: Brain },
  { href: "/cortex", label: "Cortex", icon: Scan },
  { href: "/synapses", label: "Synapses", icon: Cable },
  { href: "/hippocampus", label: "Hippocampus", icon: Moon },
  { href: "/profiles", label: "Profiles", icon: Users },
];

export function NeuralSidebar() {
  const pathname = usePathname();

  return (
    <aside
      className="flex h-screen w-56 flex-col border-r"
      style={{
        backgroundColor: NEURAL.cortex,
        borderColor: `rgba(124, 58, 237, 0.12)`,
      }}
    >
      {/* Logo */}
      <div
        className="flex h-14 items-center gap-2.5 px-4 border-b"
        style={{ borderColor: `rgba(124, 58, 237, 0.12)` }}
      >
        <div className="relative h-8 w-8 rounded-lg flex items-center justify-center overflow-hidden"
          style={{ background: `linear-gradient(135deg, ${NEURAL.episodic}, ${NEURAL.semantic})` }}
        >
          <Brain className="h-4.5 w-4.5 text-white" />
          <div className="absolute inset-0 animate-breathe rounded-lg" style={{ boxShadow: `0 0 12px ${NEURAL.neuralGlow}` }} />
        </div>
        <div>
          <span className="text-sm font-semibold text-white">Engram</span>
          <p className="text-[9px] uppercase tracking-widest" style={{ color: NEURAL.shallow }}>Neural Memory</p>
        </div>
      </div>

      {/* Navigation */}
      <nav className="flex-1 overflow-y-auto px-2 py-3 space-y-0.5">
        {NAV_ITEMS.map(({ href, label, icon: Icon }) => {
          const active = href === "/"
            ? pathname === "/"
            : pathname === href || pathname?.startsWith(href + "/");
          return (
            <Link
              key={href}
              href={href}
              className={cn(
                "flex items-center gap-2.5 rounded-lg px-3 py-2.5 text-sm transition-all duration-200",
                active
                  ? "font-medium"
                  : "hover:bg-white/[0.04]"
              )}
              style={active ? {
                background: `linear-gradient(135deg, rgba(124, 58, 237, 0.15), rgba(124, 58, 237, 0.05))`,
                color: '#c4b5fd',
                boxShadow: `inset 0 0 20px rgba(124, 58, 237, 0.08)`,
              } : {
                color: '#94a3b8',
              }}
            >
              <Icon className="h-4 w-4 shrink-0" />
              {label}
            </Link>
          );
        })}
      </nav>

      {/* Footer */}
      <div className="px-4 py-3 border-t" style={{ borderColor: `rgba(124, 58, 237, 0.12)` }}>
        <p className="text-[10px] uppercase tracking-wider" style={{ color: NEURAL.forgotten }}>
          Memory Kernel v2
        </p>
      </div>
    </aside>
  );
}
