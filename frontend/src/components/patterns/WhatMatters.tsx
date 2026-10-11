import type { PatternFeature, PatternStage } from "../../api";
import { cn } from "../../lib/cn";
import { fmtValue } from "./shared";

// Which numbers told renewals and losses apart, and how far before the decision. One hue, light to dark;
// an empty cell means "no more than luck". Hover a cell for the renewed and lost averages behind it.
export function WhatMatters({ stages, features }: { stages: PatternStage[]; features: PatternFeature[] }) {
  const cols = [...stages].sort((a, b) => b.stage - a.stage);
  const earliest = cols.find((s) => s.ready);
  const strongest = [...cols].sort((a, b) => b.separation - a.separation)[0];
  const best = strongest && strongest.separation > 0 ? strongest : null;
  const peak = (f: PatternFeature) => Math.max(0, ...cols.map((s) => s.weights[f.key] ?? 0));
  const rows = [...features].sort((a, b) => peak(b) - peak(a));
  const cell = (w: number) => (w > 0 ? `color-mix(in oklab, var(--c-accent) ${Math.round(14 + w * 76)}%, transparent)` : undefined);
  const tip = (f: PatternFeature, s: PatternStage) =>
    `${f.label} · ${s.stage} days before\nrenewed ${fmtValue(s.means.kept?.[f.key], f.unit, { mean: true })} · lost ${fmtValue(s.means.lost?.[f.key], f.unit, { mean: true })}`;

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[640px] border-separate border-spacing-[3px] text-[11.5px]">
        <thead>
          <tr>
            <th className="w-48" />
            {cols.map((s, i) => (
              <th key={s.stage} className="h-20 align-bottom">
                <div className="relative mx-auto flex h-16 w-full max-w-7 items-end">
                  {earliest?.stage === s.stage && (
                    <span className="absolute -top-5 left-1/2 -translate-x-1/2 rounded-full bg-accent px-1.5 py-px text-[9.5px] font-semibold whitespace-nowrap text-white animate-fade-in" style={{ animationDelay: "900ms" }}>
                      from here
                    </span>
                  )}
                  <div
                    className={cn("w-full origin-bottom rounded-t-[4px] animate-grow-y", s.ready ? "bg-accent" : "bg-faint/25")}
                    style={{ height: `${Math.max(4, s.separation * 100)}%`, animationDelay: `${i * 55}ms` }}
                    title={`${s.stage} days before · separation ${s.separation.toFixed(2)}`}
                  />
                </div>
              </th>
            ))}
            {best && (
              <th className="w-36 pl-3 text-left align-bottom text-[11px] font-medium whitespace-nowrap text-faint">
                <span className="text-ok-fg">renewed</span> · <span className="text-risk-fg">lost</span>
              </th>
            )}
          </tr>
        </thead>
        <tbody>
          {rows.map((f, r) => (
            <tr key={f.key} className={cn("animate-fade-in", peak(f) === 0 && "opacity-50")} style={{ animationDelay: `${150 + r * 50}ms` }}>
              <th scope="row" className="pr-2 text-left font-normal whitespace-nowrap text-muted">{f.label}</th>
              {cols.map((s, i) => {
                const w = s.weights[f.key] ?? 0;
                return (
                  <td
                    key={s.stage}
                    tabIndex={0}
                    title={tip(f, s)}
                    aria-label={tip(f, s)}
                    className={cn("h-8 rounded-[5px] transition-transform animate-scale-in hover:scale-110 hover:ring-2 hover:ring-ink/30 focus:ring-2 focus:ring-ink/30", w > 0 ? "" : "bg-hover/70")}
                    style={{ background: cell(w), animationDelay: `${300 + i * 35 + r * 25}ms` }}
                  />
                );
              })}
              {best && (
                <td className="pl-3 whitespace-nowrap tabular-nums">
                  <span className="font-medium text-ok-fg">{fmtValue(best.means.kept?.[f.key], f.unit, { mean: true })}</span>
                  <span className="mx-1 text-faint">·</span>
                  <span className="font-medium text-risk-fg">{fmtValue(best.means.lost?.[f.key], f.unit, { mean: true })}</span>
                </td>
              )}
            </tr>
          ))}
          <tr>
            <td />
            {cols.map((s) => (
              <td key={s.stage} className="pt-1 text-center text-[10.5px] text-faint tabular-nums">{s.stage}d</td>
            ))}
            {best && <td className="pt-1 pl-3 text-[10.5px] text-faint">at {best.stage}d</td>}
          </tr>
        </tbody>
      </table>
    </div>
  );
}
