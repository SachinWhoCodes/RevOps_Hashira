import { cn } from "../../lib/cn";

// Guidance only: the server's rule is 12+ characters and not the email address.
export function strength(pw: string): { score: 0 | 1 | 2 | 3 | 4; label: string } {
  if (!pw) return { score: 0, label: "" };
  if (pw.length < 12) return { score: 1, label: `Too short · ${12 - pw.length} more character${12 - pw.length === 1 ? "" : "s"}` };
  let s = 2;
  if (/[a-z]/.test(pw) && /[A-Z]/.test(pw)) s++;
  if (/\d/.test(pw) && /[^A-Za-z0-9]/.test(pw)) s++;
  if (pw.length >= 16) s = Math.min(4, s + 1);
  const score = Math.min(4, s) as 2 | 3 | 4;
  return { score, label: score === 4 ? "Strong" : score === 3 ? "Good" : "Fair" };
}

export function PasswordStrength({ password }: { password: string }) {
  const { score, label } = strength(password);
  if (!password) return <p className="text-xs text-faint">At least 12 characters. A short phrase is easy to remember and hard to guess.</p>;
  const color = score <= 1 ? "bg-risk" : score === 2 ? "bg-warn" : "bg-ok";
  return (
    <div className="space-y-1.5" aria-live="polite">
      <div className="grid grid-cols-4 gap-1">
        {[1, 2, 3, 4].map((i) => (
          <span key={i} className={cn("h-1 rounded-full transition-colors duration-300", i <= score ? color : "bg-line")} />
        ))}
      </div>
      <p className={cn("text-xs", score <= 1 ? "text-risk-fg" : "text-muted")}>{label}</p>
    </div>
  );
}
