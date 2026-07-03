import type { Metadata } from "next";
import Link from "next/link";
import { Activity, BookOpen, BriefcaseBusiness, LayoutDashboard } from "lucide-react";
import "./globals.css";

export const metadata: Metadata = {
  title: "FOMO Control Engine",
  description: "Personal trading decision engine"
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="ko">
      <body>
        <aside className="sidebar">
          <Link href="/" className="brand" aria-label="FOMO Control Engine home">
            <span className="brandMark">F</span>
            <span>
              <strong>FOMO Control</strong>
              <small>Decision Engine</small>
            </span>
          </Link>
          <nav className="nav">
            <Link href="/">
              <LayoutDashboard size={18} />
              Dashboard
            </Link>
            <Link href="/positions">
              <BriefcaseBusiness size={18} />
              Positions
            </Link>
            <Link href="/journal">
              <BookOpen size={18} />
              Journal
            </Link>
          </nav>
          <div className="sidebarStatus">
            <Activity size={16} />
            Read-only v0.1
          </div>
        </aside>
        <main className="main">{children}</main>
      </body>
    </html>
  );
}

