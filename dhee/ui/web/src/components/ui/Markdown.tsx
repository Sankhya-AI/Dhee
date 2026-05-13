// Tiny dependency-free Markdown renderer. Inputs are text-escaped before
// regex substitution so user content can never break out into HTML. Supports:
//   #, ##, ### headings
//   **bold**, *italic*
//   `inline code`
//   ```fenced code```
//   [label](url)
//   [[wiki-link]]  (resolved against `wikiTitles` to a hash route)
//   - list, * list, 1. list
//   > blockquote

const ESC: Record<string, string> = {
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  '"': "&quot;",
  "'": "&#39;",
};

function escape(s: string): string {
  return s.replace(/[&<>"']/g, (c) => ESC[c] || c);
}

function safeUrl(url: string): string {
  const trimmed = url.trim();
  if (/^(https?:|mailto:|#|\/)/i.test(trimmed)) return trimmed;
  return "#";
}

function renderInline(src: string, wikiResolve?: (title: string) => string | null): string {
  let out = escape(src);
  // inline code (`code`)
  out = out.replace(/`([^`]+)`/g, (_, code) => `<code class="md-code">${code}</code>`);
  // wiki [[link]] — escape rendered title; the target is resolved via callback
  out = out.replace(/\[\[([^\]|]+)(?:\|([^\]]+))?\]\]/g, (_, title: string, alias?: string) => {
    const label = (alias || title).trim();
    const href = wikiResolve ? wikiResolve(title.trim()) : null;
    if (!href) return `<span class="md-wiki md-wiki-missing">${label}</span>`;
    return `<a class="md-wiki md-link" href="${escape(href)}">${label}</a>`;
  });
  // markdown links [label](url)
  out = out.replace(/\[([^\]]+)\]\(([^)]+)\)/g, (_, label: string, url: string) => {
    return `<a class="md-link" href="${escape(safeUrl(url))}" target="_blank" rel="noreferrer">${label}</a>`;
  });
  // bold **text**
  out = out.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  // italic *text*
  out = out.replace(/(^|[^*])\*([^*]+)\*/g, "$1<em>$2</em>");
  return out;
}

export interface MarkdownProps {
  source: string;
  wikiResolve?: (title: string) => string | null;
}

export function Markdown({ source, wikiResolve }: MarkdownProps) {
  const lines = (source || "").replace(/\r\n/g, "\n").split("\n");
  const parts: string[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (/^```/.test(line)) {
      const lang = line.slice(3).trim();
      const buf: string[] = [];
      i += 1;
      while (i < lines.length && !/^```/.test(lines[i])) {
        buf.push(lines[i]);
        i += 1;
      }
      i += 1;
      parts.push(
        `<pre class="md-pre" data-lang="${escape(lang)}"><code class="md-code">${escape(buf.join("\n"))}</code></pre>`
      );
      continue;
    }
    if (/^### /.test(line)) {
      parts.push(`<h3 class="md-h3">${renderInline(line.slice(4), wikiResolve)}</h3>`);
      i += 1;
      continue;
    }
    if (/^## /.test(line)) {
      parts.push(`<h2 class="md-h2">${renderInline(line.slice(3), wikiResolve)}</h2>`);
      i += 1;
      continue;
    }
    if (/^# /.test(line)) {
      parts.push(`<h1 class="md-h1">${renderInline(line.slice(2), wikiResolve)}</h1>`);
      i += 1;
      continue;
    }
    if (/^>\s?/.test(line)) {
      const buf: string[] = [];
      while (i < lines.length && /^>\s?/.test(lines[i])) {
        buf.push(lines[i].replace(/^>\s?/, ""));
        i += 1;
      }
      parts.push(
        `<blockquote class="md-quote">${renderInline(buf.join(" "), wikiResolve)}</blockquote>`
      );
      continue;
    }
    if (/^\s*[-*]\s/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*[-*]\s/.test(lines[i])) {
        items.push(
          `<li>${renderInline(lines[i].replace(/^\s*[-*]\s/, ""), wikiResolve)}</li>`
        );
        i += 1;
      }
      parts.push(`<ul class="md-list">${items.join("")}</ul>`);
      continue;
    }
    if (/^\s*\d+\.\s/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*\d+\.\s/.test(lines[i])) {
        items.push(
          `<li>${renderInline(lines[i].replace(/^\s*\d+\.\s/, ""), wikiResolve)}</li>`
        );
        i += 1;
      }
      parts.push(`<ol class="md-list">${items.join("")}</ol>`);
      continue;
    }
    if (line.trim() === "") {
      i += 1;
      continue;
    }
    const buf: string[] = [line];
    i += 1;
    while (
      i < lines.length &&
      lines[i].trim() !== "" &&
      !/^(#{1,3} |```|\s*[-*]\s|\s*\d+\.\s|>\s?)/.test(lines[i])
    ) {
      buf.push(lines[i]);
      i += 1;
    }
    parts.push(
      `<p class="md-p">${renderInline(buf.join("\n"), wikiResolve)}</p>`
    );
  }
  return (
    <div
      className="md-root"
      style={{
        fontFamily: "var(--font)",
        fontSize: 13.5,
        lineHeight: 1.55,
        color: "var(--ink)",
      }}
      dangerouslySetInnerHTML={{ __html: parts.join("") }}
    />
  );
}
