import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { api, type QaAnswer } from "../api";
import { useEvidence } from "./EvidenceDrawer";
import { Badge } from "./ui";

const EXAMPLES = ["Who else have we spoken to besides the champion?", "How has usage changed recently?", "Has anyone mentioned a competitor?"];

const ERRORS: Record<string, string> = {
  budget_exceeded: "Today's AI budget is used up. Try again tomorrow or raise AI_DAILY_BUDGET_USD.",
  ai_disabled: "AI is switched off (AI_ENABLED=false).",
  no_api_key: "No Anthropic API key is configured.",
};

// Claude answers from read-only tools over this account; code checks every sentence before showing it.
export default function AskPanel({ accountId }: { accountId: string }) {
  const evidence = useEvidence();
  const [question, setQuestion] = useState("");
  const ask = useMutation({
    mutationFn: (q: string) => api<QaAnswer>(`/accounts/${accountId}/ask`, { json: { question: q } }),
  });
  const a = ask.data;
  const submit = (q: string) => {
    if (q.trim()) ask.mutate(q.trim());
  };
  return (
    <section className="mb-4 rounded-lg border border-line bg-panel">
      <header className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-4 py-2.5">
        <h2 className="text-sm font-semibold">Ask about this account</h2>
        <Badge title="Claude reads this account through read-only tools. Every sentence must cite the records it rests on, and code checks every number, date and name against them before you see it.">
          read-only · every sentence checked
        </Badge>
      </header>
      <div className="space-y-3 p-4">
        <form
          className="flex flex-col gap-2 sm:flex-row"
          onSubmit={(e) => {
            e.preventDefault();
            submit(question);
          }}
        >
          <input
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            maxLength={500}
            placeholder="e.g. Who else at this company have we spoken to?"
            className="min-w-0 flex-1 rounded-md border border-line bg-panel px-3 py-1.5 text-sm"
          />
          <button type="submit" disabled={ask.isPending || !question.trim()}
            className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50">
            {ask.isPending ? "Looking it up…" : "Ask"}
          </button>
        </form>
        <p className="-mt-1 text-[11px] text-faint">
          Each new question is one paid AI call (about $0.03); asking the same question again about an unchanged account is free.
        </p>
        {!a && !ask.isPending && (
          <div className="flex flex-wrap gap-1.5">
            {EXAMPLES.map((q) => (
              <button key={q} onClick={() => setQuestion(q)} title="Fills the box; nothing is sent until you press Ask"
                className="rounded-full border border-line px-2 py-0.5 text-xs text-muted hover:border-accent hover:text-accent">
                {q}
              </button>
            ))}
          </div>
        )}
        {ask.error && <p className="text-sm text-risk">{ask.error instanceof Error ? ask.error.message : "Something went wrong"}</p>}
        {a && a.error && <p className="text-sm text-warn">{ERRORS[a.error] ?? `No answer: ${a.error}`}</p>}
        {a && !a.error && (
          <div className="rounded-md border border-line bg-canvas px-3 py-2.5">
            <ol className="space-y-1.5 text-sm">
              {a.sentences.map((s, i) => (
                <li key={i} className="leading-relaxed">
                  {s.text}{" "}
                  {s.evidence.map((ev) => (
                    <button key={ev.eid} onClick={() => evidence.open(ev)} title={ev.label}
                      className="ml-0.5 rounded border border-line bg-panel px-1 py-px align-middle text-[11px] font-medium text-accent hover:border-accent">
                      {ev.eid}
                    </button>
                  ))}
                </li>
              ))}
            </ol>
            {a.gaps && <p className="mt-2 text-xs text-muted">Not in the data: {a.gaps}</p>}
            {!a.sentences.length && !a.gaps && <p className="text-sm text-muted">Nothing in the answer passed the checks.</p>}
            <p className="mt-2 text-[11px] text-faint">
              {a.model} · {a.prompt_version} · looked at {a.tools_used.join(", ") || "nothing"}
              {a.removed.length > 0 && ` · ${a.removed.length} sentence(s) removed by the checker`}
              {a.cached ? " · cached answer, no cost" : ` · $${a.cost_usd.toFixed(3)}`}
            </p>
          </div>
        )}
      </div>
    </section>
  );
}
