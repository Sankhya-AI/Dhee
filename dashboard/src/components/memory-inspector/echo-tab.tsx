"use client";

import type { Memory } from "@/lib/types/memory";
import { NEURAL } from "@/lib/utils/neural-palette";

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <h4 className="text-xs font-medium text-slate-300 mb-2">{title}</h4>
      {children}
    </div>
  );
}

function TagList({ items }: { items: string[] }) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {items.map((item, i) => (
        <span
          key={i}
          className="inline-flex rounded-full px-2.5 py-0.5 text-xs"
          style={{
            backgroundColor: 'rgba(124, 58, 237, 0.1)',
            color: '#c4b5fd',
          }}
        >
          {item}
        </span>
      ))}
    </div>
  );
}

function StringList({ items }: { items: string[] }) {
  return (
    <ul className="space-y-1.5">
      {items.map((item, i) => (
        <li key={i} className="text-sm leading-relaxed" style={{ color: '#cbd5e1' }}>
          {item}
        </li>
      ))}
    </ul>
  );
}

export function EchoTab({ memory }: { memory: Memory }) {
  const meta = memory.metadata || {};
  const depth = meta.echo_depth || "none";
  const paraphrases = meta.echo_paraphrases || [];
  const keywords = meta.echo_keywords || [];
  const implications = meta.echo_implications || [];
  const questions = meta.echo_questions || [];
  const importance = meta.echo_importance;

  const hasEchoData =
    paraphrases.length > 0 ||
    keywords.length > 0 ||
    implications.length > 0 ||
    questions.length > 0;

  return (
    <div className="space-y-5">
      {/* Echo depth badge */}
      <div className="flex items-center gap-3">
        <span className="text-xs" style={{ color: NEURAL.shallow }}>Echo Depth</span>
        <span
          className="inline-flex rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 capitalize"
          style={{
            backgroundColor: `${NEURAL.deep}15`,
            color: NEURAL.deep,
            borderColor: `${NEURAL.deep}30`,
          }}
        >
          {depth}
        </span>
        {importance !== undefined && (
          <>
            <span className="text-xs ml-2" style={{ color: NEURAL.shallow }}>Importance</span>
            <div className="flex items-center gap-1.5">
              <div className="w-16 h-1.5 rounded-full" style={{ backgroundColor: 'rgba(124,58,237,0.1)' }}>
                <div
                  className="h-1.5 rounded-full"
                  style={{
                    width: `${(importance as number) * 100}%`,
                    backgroundColor: NEURAL.deep,
                    boxShadow: `0 0 6px ${NEURAL.deep}50`,
                  }}
                />
              </div>
              <span className="text-xs" style={{ color: NEURAL.shallow }}>
                {((importance as number) * 100).toFixed(0)}%
              </span>
            </div>
          </>
        )}
      </div>

      {!hasEchoData && (
        <p className="text-sm" style={{ color: NEURAL.shallow }}>No echo encoding data available.</p>
      )}

      {paraphrases.length > 0 && (
        <Section title="Paraphrases">
          <StringList items={paraphrases} />
        </Section>
      )}

      {keywords.length > 0 && (
        <Section title="Keywords">
          <TagList items={keywords} />
        </Section>
      )}

      {implications.length > 0 && (
        <Section title="Implications">
          <StringList items={implications} />
        </Section>
      )}

      {questions.length > 0 && (
        <Section title="Questions">
          <StringList items={questions} />
        </Section>
      )}
    </div>
  );
}
