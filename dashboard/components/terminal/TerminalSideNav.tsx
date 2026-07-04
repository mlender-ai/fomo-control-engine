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
import { sourceLabel } from "@/lib/labels/marketStateLabels";

const sections = [
  {
    heading: "관제",
    items: [
      { href: "/", label: "라이브 포지션", icon: Activity, shortcut: "G P" }
    ]
  },
  {
    heading: "복기",
    items: [
      { href: "/trades", label: "거래 복기", icon: FileClock, shortcut: "G T" }
    ]
  },
  {
    heading: "시스템",
    items: [
      { href: "/settings", label: "설정", icon: Settings, shortcut: "G ," }
    ]
  }
];

export function TerminalSideNav({ pathname, status }: { pathname: string; status: SystemStatus | null }) {
  return (
    <SideNav
      className="terminalSideNav"
      collapsible={{ defaultIsCollapsed: false, buttonLabel: "사이드바 접기" }}
      header={
        <div className="terminalSideHeader">
          <Gauge size={18} />
          <div>
            <strong>포지션 관제</strong>
            <small>MVP 리셋</small>
          </div>
        </div>
      }
      footer={
        <div className="terminalRailFooter">
          <ShieldCheck size={16} />
          <div>
            <strong>주문 실행 없음</strong>
            <span>읽기 전용 포지션 분석</span>
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
      <SideNavSection title="상태">
        <SideNavItem
          icon={ShieldCheck}
          isSelected={false}
          label={`데이터 ${sourceLabel(status?.market_data_provider)}`}
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
