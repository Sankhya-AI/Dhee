"use client";

export function CategoryPill({
  name,
  onClick,
}: {
  name: string;
  onClick?: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium transition-colors bg-purple-500/10 text-purple-300 ring-1 ring-purple-500/20 hover:bg-purple-500/20"
    >
      {name}
    </button>
  );
}
