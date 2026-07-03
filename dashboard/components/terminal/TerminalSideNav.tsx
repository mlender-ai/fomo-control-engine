"use client";

import { Badge } from "@astryxdesign/core/Badge";
import { SideNav, SideNavItem, SideNavSection } from "@astryxdesign/core/SideNav";
import {
  Activity,
  BarChart3,
  BookOpen,
  FlaskConical,
  Gauge,
  History,
  LayoutDashboard,
  Microscope,
  Orbit,
  Settings,
  ShieldCheck
} from "lucide-react";
import type { SystemStatus } from "@/lib/api";

const sections = [
  {
    heading: "MONITOR",
    items: [
      { href: "/", label: "Dashboard", icon: LayoutDashboard, shortcut: "G D" },
      { href: "/markets", label: "Markets", icon: BarChart3, shortcut: "G M" },
      { href: "/research", label: "Research Runs", icon: Microscope, shortcut: "G R" },
      { href: "/positions", label: "Positions", icon: Activity, shortcut: "G P" }
    ]
  },
  {
    heading: "JOURNAL",
    items: [
      { href: "/journal", label: "Trade Journal", icon: BookOpen, shortcut: "G J" },
      { href: "/shadow", label: "Shadow Account", icon: Orbit, shortcut: "G S" },
      { href: "/research", label: "Decision Memory", icon: History, shortcut: "G R" }
    ]
  },
  {
    heading: "LAB",
    items: [
      { href: "/validation", label: "Validation Lab", icon: FlaskConical, shortcut: "G V" },
      { href: "/settings", label: "Settings", icon: Settings, shortcut: "G ," }
    ]
  }
];

export function TerminalSideNav({ pathname, status }: { pathname: string; status: SystemStatus | null }) {
  return (
    <SideNav
      className="terminalSideNav"
      collapsible={{ defaultIsCollapsed: false, buttonLabel: "Toggle terminal rail" }}
      header={
        <div className="terminalSideHeader">
          <Gauge size={18} />
          <div>
            <strong>Decision OS</strong>
            <small>v0.4 Agentic Research</small>
          </div>
        </div>
      }
      footer={
        <div className="terminalRailFooter">
          <ShieldCheck size={16} />
          <div>
            <strong>No order execution</strong>
            <span>API boundary is read-only</span>
          </div>
        </div>
      }
    >
      {sections.map((section) => (
        <SideNavSection title={section.heading} key={section.heading}>
          {section.items.map((item) => (
            <SideNavItem
              href={item.href}
              icon={item.icon}
              isSelected={isSelected(pathname, item.href)}
              key={item.label}
              label={item.label}
              endContent={<Badge variant="neutral" label={item.shortcut} />}
            />
          ))}
        </SideNavSection>
      ))}
      <SideNavSection title="STATUS">
        <SideNavItem
          icon={ShieldCheck}
          isSelected={false}
          label={`Provider ${status?.market_data_provider ?? "..."}`}
          endContent={<Badge variant={status?.market_data_provider === "bitget" ? "success" : "warning"} label="RO" />}
        />
      </SideNavSection>
    </SideNav>
  );
}

function isSelected(pathname: string, href: string): boolean {
  if (href === "/") return pathname === "/";
  if (href === "/markets") return pathname.startsWith("/markets") || pathname.startsWith("/dashboard");
  return pathname === href || pathname.startsWith(`${href}/`);
}
