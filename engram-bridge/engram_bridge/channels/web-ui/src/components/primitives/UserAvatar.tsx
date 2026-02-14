import { cn } from "@/lib/utils";

const COLORS = [
  "bg-blue-600", "bg-purple-600", "bg-emerald-600", "bg-amber-600",
  "bg-rose-600", "bg-cyan-600", "bg-pink-600", "bg-indigo-600",
];

function hashCode(s: string) {
  let hash = 0;
  for (let i = 0; i < s.length; i++) {
    hash = ((hash << 5) - hash + s.charCodeAt(i)) | 0;
  }
  return Math.abs(hash);
}

export function UserAvatar({ name, size = "sm" }: { name: string; size?: "xs" | "sm" | "md" }) {
  const initials = name.split(/[\s_-]+/).map(w => w[0]?.toUpperCase()).join("").slice(0, 2) || "?";
  const color = COLORS[hashCode(name) % COLORS.length];
  const sizes = { xs: "w-5 h-5 text-[9px]", sm: "w-6 h-6 text-[10px]", md: "w-8 h-8 text-xs" };

  return (
    <div className={cn("rounded-full flex items-center justify-center font-medium text-white flex-shrink-0", color, sizes[size])}>
      {initials}
    </div>
  );
}
