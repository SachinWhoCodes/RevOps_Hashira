import { useMutation } from "@tanstack/react-query";
import { ArrowUp, BadgeCheck, Sparkles } from "lucide-react";
import { useState } from "react";
import { api, type QaAnswer } from "../api";
import { cn } from "../lib/cn";
import { EvidenceChip } from "./domain";
import { Callout, Card, Skeleton, Tooltip } from "./ui";

const EXAMPLES = ["Who else have we spoken to besides the champion?", "How has usage changed recently?", "Has anyone mentioned a competitor?"];

const ERRORS: Record<string, string> = {
  budget_exceeded: "Today's AI budget is used up. Try again tomorrow, or raise the daily cap.",
  ai_disabled: "AI is switched off on this server, so questions can't be answered.",
  no_api_key: "No Anthropic API key is configured on this server.",
};

// Claude answers from read-only tools over this account; code checks every sentence before showing it.
export default function AskPanel({ accountId }: { accountId: string }) {
  const [question, setQuestion] = useState("");
  const ask = useMutation({ mutationFn: (q: string) => api<QaAnswer>(`/accounts/${accountId}/ask`, { json: { question: q } }) });
  const a = ask.data;
  const submit = (q: string) => {
    if (q.trim()) ask.mutate(q.trim());
  };
  return (
    <Card
      title="Ask about this account"
      description="Claude reads this account through read-only tools; code checks every sentence"
      icon={Sparkles}
    >
      <form
        onSubmit={(e) => {
          e.preventDefault();
          submit(question);
        }}
        className="group relative rounded-2xl border border-line bg-subtle p-1.5 shadow-xs transition-[border-color,box-shadow] focus-within:border-accent focus-within:shadow-ring"
      >
        <textarea
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              submit(question);
            }
          }}
          rows={2}
          maxLength={500}
          placeholder="e.g. Who else at this company have we spoken to?"
          aria-label="Your question"
          className="block w-full resize-none bg-transparent px-3 py-2 text-[14px] text-ink outline-none placeholder:text-faint"
        />
        <div className="flex items-center justify-between gap-2 px-2 pb-1">
          <span className="text-[11.5px] text-faint">About $0.03 per new question · repeats on an unchanged account are free</span>
          <button
            type="submit"
            disabled={ask.isPending || !question.trim()}
            aria-label="Ask"
            className="grid size-8 place-items-center rounded-full bg-accent text-white shadow-sm transition-[transform,opacity] hover:scale-105 disabled:scale-100 disabled:opacity-40"
          >
            <ArrowUp className="size-4" />
          </button>
        </div>
      </form>

      {!a && !ask.isPending && (
        <div className="mt-3 flex flex-wrap gap-1.5">
          {EXAMPLES.map((q) => (
            <Tooltip key={q} content="Fills the box. Nothing is sent until you ask.">
              <button
                onClick={() => setQuestion(q)}
                className="rounded-full border border-line bg-panel px-3 py-1 text-xs text-muted transition-colors hover:border-accent/40 hover:text-accent-fg"
              >
                {q}
              </button>
            </Tooltip>
          ))}
        </div>
      )}

      {ask.isPending && (
        <div className="mt-4 space-y-2.5 rounded-xl border border-line p-4" role="status" aria-label="Looking it up">
          <div className="flex items-center gap-2 text-[13px] text-muted">
            <Sparkles className="size-4 animate-pulse text-accent-fg" /> Reading the timeline, usage and people…
          </div>
          <Skeleton className="h-3.5 w-11/12" />
          <Skeleton className="h-3.5 w-4/5" />
          <Skeleton className="h-3.5 w-2/3" />
        </div>
      )}
      {ask.error && <Callout className="mt-4" tone="risk" title="No answer">{ask.error instanceof Error ? ask.error.message : "Something went wrong"}</Callout>}
      {a && a.error && <Callout className="mt-4" tone="warn" title="No answer">{ERRORS[a.error] ?? a.error}</Callout>}
      {a && !a.error && (
        <div className="mt-4 rounded-xl border border-line p-4 animate-fade-up">
          <div className="mb-2 text-xs font-medium text-muted">{a.question}</div>
          <div className="space-y-2">
            {a.sentences.map((s, i) => (
              <p key={i} className="text-[14px] leading-relaxed text-ink">
                {s.text}
                {s.evidence.map((ev) => <EvidenceChip key={ev.eid} e={ev} />)}
              </p>
            ))}
          </div>
          {a.gaps && <p className="mt-3 text-[13px] text-muted"><span className="font-medium text-ink">Not in the data:</span> {a.gaps}</p>}
          {!a.sentences.length && !a.gaps && <p className="text-sm text-muted">Nothing in the answer passed the checks.</p>}
          <div className={cn("mt-3 flex flex-wrap items-center gap-x-2 gap-y-1 border-t border-line pt-3 text-[11.5px] text-faint")}>
            <BadgeCheck className="size-3.5 text-ok-fg" aria-hidden />
            <span>Every sentence checked</span>
            {a.removed.length > 0 && <span>· {a.removed.length} removed by the checker</span>}
            <span>· looked at {a.tools_used.join(", ") || "nothing"}</span>
            <span>· {a.cached ? "cached answer, no cost" : `$${a.cost_usd.toFixed(3)}`}</span>
            <span className="ml-auto">{a.model}</span>
          </div>
        </div>
      )}
    </Card>
  );
}
