import {
  Activity,
  Building,
  FlaskConical,
  Inbox,
  LayoutDashboard,
  ListChecks,
  Plug,
  ScanEye,
  ScrollText,
  Settings2,
  Target,
  Unlink,
  UsersRound,
  Waypoints,
  type LucideIcon,
} from "lucide-react";

export type NavItem = {
  to: string;
  label: string;
  icon: LucideIcon;
  group: "Workspace" | "Proof" | "Data" | "Configure";
  hint: string;
  admin?: boolean;
  badge?: "queue" | "approvals" | "setup";
};

// Grouped by the question a person brings: what needs me, does it work, what feeds it, how is it set up.
export const NAV: NavItem[] = [
  { to: "/", label: "Overview", icon: LayoutDashboard, group: "Workspace", hint: "Revenue at risk, approvals and data health at a glance" },
  { to: "/queue", label: "Queue", icon: Target, group: "Workspace", hint: "Open cases, ranked by revenue at stake", badge: "queue" },
  { to: "/approvals", label: "Approvals", icon: Inbox, group: "Workspace", hint: "Proposed actions waiting for a decision", badge: "approvals" },
  { to: "/accounts", label: "Accounts", icon: Building, group: "Workspace", hint: "Every customer, with renewal and activity" },
  { to: "/patterns", label: "Renewal patterns", icon: Waypoints, group: "Workspace", hint: "Each client's path compared with renewals that already ended" },
  { to: "/outcomes", label: "Outcomes", icon: Activity, group: "Proof", hint: "What happened after approved plays" },
  { to: "/backtest", label: "Back-test", icon: FlaskConical, group: "Proof", hint: "How Casefile would have done on past renewals" },
  { to: "/audit", label: "Audit log", icon: ScrollText, group: "Proof", hint: "A tamper-evident record of every step" },
  { to: "/connections", label: "Connections", icon: Plug, group: "Data", hint: "CRM, Google, Slack and product usage" },
  { to: "/blind-spots", label: "Blind spots", icon: ScanEye, group: "Data", hint: "What Casefile cannot see" },
  { to: "/identity", label: "Unmatched records", icon: Unlink, group: "Data", hint: "Records not tied to exactly one customer" },
  { to: "/setup", label: "Setup", icon: ListChecks, group: "Configure", hint: "From first connection to live", admin: true, badge: "setup" },
  { to: "/team", label: "Team", icon: UsersRound, group: "Configure", hint: "Who can log in", admin: true },
  { to: "/settings", label: "Settings", icon: Settings2, group: "Configure", hint: "Rules, scoring, AI usage and field mapping" },
];

export const GROUPS: NavItem["group"][] = ["Workspace", "Proof", "Data", "Configure"];

// The nav entry a path belongs to (longest matching prefix; "/" only matches itself).
export function navFor(pathname: string): NavItem | undefined {
  if (pathname === "/") return NAV[0];
  if (pathname.startsWith("/backtest")) return NAV.find((n) => n.to === "/backtest");
  return NAV.filter((n) => n.to !== "/" && pathname.startsWith(n.to)).sort((a, b) => b.to.length - a.to.length)[0];
}
