import { Check, Link2, Quote, ShieldCheck } from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { Logo } from "../brand";

// The right half of every auth screen: slow-moving light on a deep field, with a glimpse of the product.
function GradientFlow() {
  return (
    <div className="relative hidden overflow-hidden bg-[#07071a] lg:block" aria-hidden>
      {/* drifting light */}
      <div className="absolute inset-0">
        <div className="absolute top-[-15%] left-[-10%] size-[70%] rounded-full bg-[#4f46e5] opacity-70 mix-blend-screen blur-[90px] animate-blob-a will-change-transform" />
        <div className="absolute top-[20%] right-[-20%] size-[65%] rounded-full bg-[#a855f7] opacity-55 mix-blend-screen blur-[100px] animate-blob-b will-change-transform" />
        <div className="absolute bottom-[-25%] left-[10%] size-[70%] rounded-full bg-[#06b6d4] opacity-45 mix-blend-screen blur-[110px] animate-blob-c will-change-transform" />
        <div className="absolute right-[15%] bottom-[10%] size-[35%] rounded-full bg-[#ec4899] opacity-35 mix-blend-screen blur-[90px] animate-blob-d will-change-transform" />
      </div>
      {/* a slow aurora sweep */}
      <div className="absolute -inset-1/2 opacity-40 blur-3xl animate-aurora will-change-transform [background:conic-gradient(from_0deg,transparent_0deg,rgba(129,140,248,0.35)_60deg,transparent_120deg,rgba(34,211,238,0.25)_200deg,transparent_260deg,rgba(192,132,252,0.3)_320deg,transparent_360deg)]" />
      {/* faint grid, fading out at the edges */}
      <div className="absolute inset-0 opacity-[0.07] [background-image:linear-gradient(rgba(255,255,255,1)_1px,transparent_1px),linear-gradient(90deg,rgba(255,255,255,1)_1px,transparent_1px)] [background-size:56px_56px] [mask-image:radial-gradient(ellipse_at_center,black_30%,transparent_75%)]" />
      <div className="grain absolute inset-0 opacity-[0.14] mix-blend-overlay" />
      <div className="absolute inset-x-0 bottom-0 h-1/2 bg-gradient-to-t from-[#07071a]/80 to-transparent" />

      <div className="relative z-10 flex h-full flex-col justify-between p-12 xl:p-16">
        <div className="inline-flex w-fit items-center gap-2 rounded-full border border-white/15 bg-white/5 px-3 py-1 text-xs text-white/80 backdrop-blur-md">
          <span className="size-1.5 rounded-full bg-emerald-400 shadow-[0_0_10px_rgba(52,211,153,0.9)]" />
          Evidence-first revenue agent
        </div>

        <div className="mx-auto w-full max-w-[460px]">
          <h2 className="text-[40px] leading-[1.08] font-semibold tracking-[-0.035em] text-white xl:text-[46px]">
            Know which renewals are slipping.{" "}
            <span className="bg-gradient-to-r from-indigo-200 via-fuchsia-200 to-cyan-200 bg-clip-text text-transparent">And exactly why.</span>
          </h2>
          <p className="mt-4 max-w-md text-[15px] leading-relaxed text-white/65">
            Casefile reads your CRM, inbox, calendar and product usage, explains every risk with evidence you can click, and
            proposes the next steps for a person to approve.
          </p>

          {/* product glimpse */}
          <div className="relative mt-10 h-[300px] [@media(max-height:760px)]:hidden">
            <div className="absolute inset-x-0 top-0 rounded-2xl border border-white/15 bg-white/[0.07] p-5 shadow-2xl backdrop-blur-xl animate-float">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <span className="grid size-9 place-items-center rounded-lg bg-indigo-400/25 text-sm font-semibold text-indigo-100">A</span>
                  <div>
                    <div className="text-sm font-semibold text-white">Acme Corp</div>
                    <div className="text-xs text-white/55">Renewal in 41 days · ₹38L ARR</div>
                  </div>
                </div>
                <div className="relative grid size-11 place-items-center">
                  <svg viewBox="0 0 44 44" className="absolute inset-0 -rotate-90">
                    <circle cx="22" cy="22" r="19" fill="none" stroke="rgba(255,255,255,0.15)" strokeWidth="4" />
                    <circle cx="22" cy="22" r="19" fill="none" stroke="#fb7185" strokeWidth="4" strokeLinecap="round" strokeDasharray="119.4" strokeDashoffset="33.4" />
                  </svg>
                  <span className="text-[13px] font-semibold text-white">72</span>
                </div>
              </div>
              <p className="mt-4 text-[13.5px] leading-relaxed text-white/85">
                The champion has gone quiet as the renewal approaches, and usage is down a third on last quarter.
              </p>
              <div className="mt-3 flex flex-wrap gap-1.5">
                {[["Champion silent · 3 emails", "E1"], ["Usage −34% vs quarter", "E2"], ["No next step", "E3"]].map(([t, e]) => (
                  <span key={e} className="inline-flex items-center gap-1.5 rounded-md bg-white/10 px-2 py-1 text-[11.5px] text-white/85 ring-1 ring-white/10">
                    {t}
                    <span className="rounded bg-white/15 px-1 text-[10px] font-semibold text-white">{e}</span>
                  </span>
                ))}
              </div>
            </div>
            <div className="absolute -right-3 bottom-0 w-[260px] rounded-xl border border-white/15 bg-[#0d0d24]/80 p-4 shadow-2xl backdrop-blur-xl animate-float [animation-delay:-3.5s]">
              <div className="text-xs font-medium text-white/60">Approved by Priya · 3 actions</div>
              <ul className="mt-2 space-y-1.5 text-[12.5px] text-white/85">
                {["Task on the HubSpot deal", "Follow-up saved as a Gmail draft", "Slack note to the owner"].map((t) => (
                  <li key={t} className="flex items-center gap-2">
                    <span className="grid size-4 place-items-center rounded-full bg-emerald-400/20 text-emerald-300">
                      <Check className="size-3" strokeWidth={3} />
                    </span>
                    {t}
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </div>

        <ul className="grid grid-cols-3 gap-6 text-[12.5px] text-white/65">
          {[
            { Icon: Quote, text: "Evidence behind every sentence" },
            { Icon: ShieldCheck, text: "Nothing changes without approval" },
            { Icon: Link2, text: "A tamper-evident audit trail" },
          ].map(({ Icon, text }) => (
            <li key={text} className="flex items-start gap-2">
              <Icon className="mt-px size-4 shrink-0 text-white/80" />
              {text}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

export function AuthLayout({ children }: { children: ReactNode }) {
  return (
    <div className="grid min-h-dvh bg-panel lg:grid-cols-2">
      <div className="relative flex flex-col px-6 py-6 sm:px-10 lg:px-14 xl:px-20">
        <div className="absolute inset-x-0 top-0 h-1 bg-gradient-to-r from-indigo-500 via-violet-500 to-cyan-400 lg:hidden" aria-hidden />
        <Link to="/login" className="w-fit rounded-lg">
          <Logo />
        </Link>
        <main className="flex flex-1 items-center justify-center py-12">
          <div className="w-full max-w-[400px] animate-fade-up">{children}</div>
        </main>
        <footer className="flex flex-wrap justify-between gap-2 text-xs text-faint">
          <span>© Casefile</span>
          <span>Read-only until you approve. Every step on the record.</span>
        </footer>
      </div>
      <GradientFlow />
    </div>
  );
}

export function AuthHeading({ title, subtitle }: { title: ReactNode; subtitle?: ReactNode }) {
  return (
    <div className="mb-8">
      <h1 className="text-[28px] leading-tight font-semibold tracking-[-0.03em] text-ink">{title}</h1>
      {subtitle && <p className="mt-2 text-[14.5px] leading-relaxed text-muted">{subtitle}</p>}
    </div>
  );
}
