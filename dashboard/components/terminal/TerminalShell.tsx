"use client";

import { AppShell } from "@astryxdesign/core/AppShell";
import { usePathname, useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, type SystemStatus } from "@/lib/api";
import { TerminalCommandPalette } from "./TerminalCommandPalette";
import { TerminalSideNav } from "./TerminalSideNav";
import { TerminalTopBar } from "./TerminalTopBar";

const routeShortcuts: Record<string, string> = {
  d: "/",
  p: "/",
  l: "/",
  t: "/trades",
  h: "/trades",
  ",": "/settings"
};

export function TerminalShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [notice, setNotice] = useState("");
  const [currentTime, setCurrentTime] = useState("");
  const routeModeRef = useRef(false);

  const loadStatus = useCallback(async () => {
    try {
      setStatus(await api.systemStatus());
    } catch {
      setStatus(null);
    }
  }, []);

  useEffect(() => {
    void loadStatus();
    const interval = window.setInterval(loadStatus, 30000);
    return () => window.clearInterval(interval);
  }, [loadStatus]);

  useEffect(() => {
    function tick() {
      setCurrentTime(new Intl.DateTimeFormat("ko-KR", { hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(new Date()));
    }
    tick();
    const interval = window.setInterval(tick, 1000);
    return () => window.clearInterval(interval);
  }, []);

  useEffect(() => {
    if (!notice) return;
    const timeout = window.setTimeout(() => setNotice(""), 4200);
    return () => window.clearTimeout(timeout);
  }, [notice]);

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (isTypingTarget(event.target)) return;
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setPaletteOpen(true);
        return;
      }
      if (event.key === "/") {
        event.preventDefault();
        setPaletteOpen(true);
        return;
      }
      if (event.key.toLowerCase() === "g") {
        routeModeRef.current = true;
        setNotice("이동 모드: P 포지션 관제 · T 거래 복기 · , 설정");
        return;
      }
      if (routeModeRef.current) {
        const target = routeShortcuts[event.key.toLowerCase()] ?? routeShortcuts[event.key];
        routeModeRef.current = false;
        if (target) {
          event.preventDefault();
          router.push(target);
        }
        return;
      }
      if (event.key.toLowerCase() === "r") {
        event.preventDefault();
        void loadStatus();
        router.refresh();
      }
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [loadStatus, router]);

  return (
    <AppShell
      className="terminalShell"
      height="fill"
      variant="section"
      contentPadding={0}
      mobileNav={{ breakpoint: "md" }}
      topNav={
        <TerminalTopBar
          status={status}
          pathname={pathname}
          currentTime={currentTime}
          onCommand={() => setPaletteOpen(true)}
          onRefresh={() => {
            void loadStatus();
            router.refresh();
          }}
        />
      }
      sideNav={<TerminalSideNav pathname={pathname} status={status} />}
    >
      <div className="terminalWorkspace">
        {notice ? <div className="terminalNotice">{notice}</div> : null}
        {children}
      </div>
      <TerminalCommandPalette isOpen={paletteOpen} onOpenChange={setPaletteOpen} onNotice={setNotice} />
    </AppShell>
  );
}

function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tagName = target.tagName.toLowerCase();
  return tagName === "input" || tagName === "textarea" || tagName === "select" || target.isContentEditable;
}
