"use client";

import { useState, useEffect } from "react";

export function useScrollProgress(): number {
  const [progress, setProgress] = useState(0);

  useEffect(() => {
    const main = document.querySelector("main");
    if (!main) return;

    const handleScroll = () => {
      const scrollTop = main.scrollTop;
      const scrollHeight = main.scrollHeight - main.clientHeight;
      if (scrollHeight <= 0) {
        setProgress(0);
        return;
      }
      setProgress(Math.min(1, Math.max(0, scrollTop / scrollHeight)));
    };

    main.addEventListener("scroll", handleScroll, { passive: true });
    return () => main.removeEventListener("scroll", handleScroll);
  }, []);

  return progress;
}
