import { useCallback, useRef, type KeyboardEvent } from "react";
import { Button } from "@/components/ui/button";
import { SendHorizonal } from "lucide-react";

interface ChatInputProps {
  onSend: (text: string) => void;
  disabled?: boolean;
}

export function ChatInput({ onSend, disabled }: ChatInputProps) {
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
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, []);

  return (
    <div className="flex items-end gap-2 p-4 border-t border-border bg-background">
      <textarea
        ref={textareaRef}
        rows={1}
        placeholder="Send a message... (Shift+Enter for newline)"
        className="flex-1 resize-none bg-input rounded-lg px-3 py-2.5 text-sm outline-none placeholder:text-muted-foreground focus:ring-1 focus:ring-ring leading-relaxed"
        onKeyDown={handleKeyDown}
        onInput={handleInput}
        disabled={disabled}
      />
      <Button
        size="icon"
        className="h-10 w-10 shrink-0"
        onClick={handleSend}
        disabled={disabled}
      >
        <SendHorizonal className="h-4 w-4" />
      </Button>
    </div>
  );
}
