import { useCallback, useRef, type KeyboardEvent } from "react";
import { Button } from "@/components/ui/button";
import { SendHorizonal, Loader2 } from "lucide-react";

interface FollowUpInputProps {
  onSend: (text: string) => void;
  disabled?: boolean;
  isExecuting?: boolean;
  placeholder?: string;
}

export function FollowUpInput({ onSend, disabled, isExecuting, placeholder }: FollowUpInputProps) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const handleSend = useCallback(() => {
    const text = textareaRef.current?.value.trim();
    if (!text) return;
    onSend(text);
    if (textareaRef.current) {
      textareaRef.current.value = "";
      textareaRef.current.style.height = "auto";
    }
  }, [onSend]);

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        handleSend();
      }
    },
    [handleSend]
  );

  const handleInput = useCallback(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 150)}px`;
  }, []);

  return (
    <div className="flex items-end gap-2 p-3 border-t border-border bg-card/50">
      {isExecuting && (
        <div className="flex items-center gap-1.5 px-2 py-1.5">
          <Loader2 className="h-3.5 w-3.5 text-accent animate-spin" />
          <span className="text-xs text-muted-foreground">Running...</span>
        </div>
      )}
      <textarea
        ref={textareaRef}
        rows={1}
        placeholder={placeholder || "Follow up on this task..."}
        className="flex-1 resize-none bg-input rounded-lg px-3 py-2 text-sm outline-none placeholder:text-muted-foreground focus:ring-1 focus:ring-ring leading-relaxed"
        onKeyDown={handleKeyDown}
        onInput={handleInput}
        disabled={disabled || isExecuting}
      />
      <Button
        size="icon"
        variant="ghost"
        className="h-8 w-8 shrink-0 text-primary hover:text-primary hover:bg-primary/10"
        onClick={handleSend}
        disabled={disabled || isExecuting}
      >
        <SendHorizonal className="h-4 w-4" />
      </Button>
    </div>
  );
}
