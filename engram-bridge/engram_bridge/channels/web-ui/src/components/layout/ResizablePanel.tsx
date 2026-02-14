import { useCallback, useRef, useState, useEffect, type ReactNode } from "react";

interface Props {
  children: ReactNode;
  defaultWidth?: number;
  minWidth?: number;
  maxWidth?: number;
  side?: "left" | "right";
  className?: string;
}

export function ResizablePanel({
  children,
  defaultWidth = 480,
  minWidth = 360,
  maxWidth = 640,
  side = "right",
  className = "",
}: Props) {
  const [width, setWidth] = useState(defaultWidth);
  const isDragging = useRef(false);
  const startX = useRef(0);
  const startWidth = useRef(0);

  const handleMouseDown = useCallback(
    (e: React.MouseEvent) => {
      isDragging.current = true;
      startX.current = e.clientX;
      startWidth.current = width;
      e.preventDefault();
    },
    [width],
  );

  useEffect(() => {
    const handleMouseMove = (e: MouseEvent) => {
      if (!isDragging.current) return;
      const delta = side === "right"
        ? startX.current - e.clientX
        : e.clientX - startX.current;
      const newWidth = Math.max(minWidth, Math.min(maxWidth, startWidth.current + delta));
      setWidth(newWidth);
    };

    const handleMouseUp = () => {
      isDragging.current = false;
    };

    document.addEventListener("mousemove", handleMouseMove);
    document.addEventListener("mouseup", handleMouseUp);
    return () => {
      document.removeEventListener("mousemove", handleMouseMove);
      document.removeEventListener("mouseup", handleMouseUp);
    };
  }, [minWidth, maxWidth, side]);

  return (
    <div
      className={`relative flex-shrink-0 ${className}`}
      style={{ width: `${width}px` }}
    >
      {/* Drag handle */}
      <div
        className={`absolute top-0 bottom-0 w-1 cursor-col-resize hover:bg-primary/30 active:bg-primary/50 transition-colors z-10 ${
          side === "right" ? "left-0" : "right-0"
        }`}
        onMouseDown={handleMouseDown}
      />
      {children}
    </div>
  );
}
