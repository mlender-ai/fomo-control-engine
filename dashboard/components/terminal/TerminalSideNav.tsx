"use client";

import { Badge } from "@astryxdesign/core/Badge";
import { SideNav, SideNavItem, SideNavSection } from "@astryxdesign/core/SideNav";
import {
  Activity,
  FileClock,
  Gauge,
  Settings,
  ShieldCheck
} from "lucide-react";
import type { SystemStatus } from "@/lib/api";

const sections = [
  {
    heading: "MONITOR",
    items: [
      { href: "/", label: "Live Positions", icon: Activity, shortcut: "G P" }
    ]
  },
  {
    heading: "REVIEW",
    items: [
      { href: "/trades", label: "Trade History", icon: FileClock, shortcut: "G T" }
    ]
  },
  {
    heading: "SYSTEM",
    items: [
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
            <strong>Position Cockpit</strong>
            <small>MVP Reset</small>
          </div>
        </div>
      }
      footer={
        <div className="terminalRailFooter">
          <ShieldCheck size={16} />
          <div>
            <strong>No order execution</strong>
            <span>Read-only position intelligence</span>
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
  if (href === "/") return pathname === "/" || pathname.startsWith("/positions");
  return pathname === href || pathname.startsWith(`${href}/`);
}
