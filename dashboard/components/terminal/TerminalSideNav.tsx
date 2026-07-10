"use client";

import { Badge } from "@astryxdesign/core/Badge";
import { SideNav, SideNavItem, SideNavSection } from "@astryxdesign/core/SideNav";
import Link from "next/link";
import {
  Activity,
  BookOpenCheck,
  FileClock,
  Gauge,
  LineChart,
  Radar,
  Settings,
  SlidersHorizontal,
  ShieldCheck
} from "lucide-react";
import type { SystemStatus } from "@/lib/api";
import { sourceLabel } from "@/lib/labels/marketStateLabels";

const sections = [
  {
    heading: "관제",
    items: [
      { href: "/", label: "라이브 포지션", icon: Activity, shortcut: "G P" },
      { href: "/scout", label: "스카우트", icon: Radar, shortcut: "G S" }
    ]
  },
  {
    heading: "복기",
    items: [
      { href: "/review", label: "복기 센터", icon: BookOpenCheck, shortcut: "G R" },
      { href: "/trades", label: "거래 복기", icon: FileClock, shortcut: "G T" },
      { href: "/calibration", label: "판단 성적표", icon: SlidersHorizontal, shortcut: "G C" },
      { href: "/performance", label: "계좌 성적표", icon: LineChart, shortcut: "G A" }
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
              as={Link}
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
  if (href === "/review") return pathname === "/review";
  return pathname === href || pathname.startsWith(`${href}/`);
}
