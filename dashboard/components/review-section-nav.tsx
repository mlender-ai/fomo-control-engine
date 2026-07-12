"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { BookOpenCheck, Bot, FileClock, LineChart } from "lucide-react";

const items = [
  { href: "/review", label: "요약", icon: BookOpenCheck },
  { href: "/engine", label: "엔진 트레이딩", icon: Bot },
  { href: "/trades", label: "거래 복기", icon: FileClock },
  { href: "/performance", label: "계좌 성적표", icon: LineChart }
];

export function ReviewSectionNav() {
  const pathname = usePathname();
  return (
    <nav className="reviewSectionNav" aria-label="복기와 성적표">
      {items.map(({ href, label, icon: Icon }) => (
        <Link className={pathname === href ? "active" : ""} href={href} key={href}>
          <Icon size={15} />
          {label}
        </Link>
      ))}
    </nav>
  );
}
