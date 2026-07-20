"use client";

import Link from "next/link";
import {
  Activity,
  BookOpenCheck,
  Bot,
  FileClock,
  Gauge,
  LineChart,
  Radar,
  Settings,
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
      { href: "/engine", label: "엔진 트레이딩", icon: Bot, shortcut: "G E" },
      { href: "/review", label: "복기 센터", icon: BookOpenCheck, shortcut: "G R" },
      { href: "/trades", label: "거래 복기", icon: FileClock, shortcut: "G T" },
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
    <aside className="terminalSideNav" aria-label="주요 탐색">
      <div className="terminalSideHeader">
        <Gauge size={18} />
        <div>
          <strong>트레이딩 워크스페이스</strong>
          <small>Read-only intelligence</small>
        </div>
      </div>
      <nav className="terminalNavGroups">
      {sections.map((section) => (
        <section className="terminalNavSection" key={section.heading}>
          <span className="terminalNavHeading">{section.heading}</span>
          {section.items.map((item) => (
            <Link className={`terminalNavItem ${isSelected(pathname, item.href) ? "active" : ""}`} href={item.href} key={item.label}>
              <item.icon size={17} />
              <span>{item.label}</span>
              <kbd>{item.shortcut}</kbd>
            </Link>
          ))}
        </section>
      ))}
      </nav>
      <div className="terminalRailFooter">
        <ShieldCheck size={17} />
        <div>
          <strong>Read only</strong>
          <span>{sourceLabel(status?.market_data_provider)} 데이터 · 주문 실행 없음</span>
        </div>
        <i className={status?.market_data_provider === "bitget" ? "online" : ""} />
      </div>
    </aside>
  );
}

function isSelected(pathname: string, href: string): boolean {
  if (href === "/") return pathname === "/";
  if (href === "/review") return pathname === "/review";
  return pathname === href || pathname.startsWith(`${href}/`);
}
