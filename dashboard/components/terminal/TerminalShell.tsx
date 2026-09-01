"use client";

import { AppShell } from "@astryxdesign/core/AppShell";
import { usePathname, useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { API_BASE_URL, api, type SystemStatus } from "@/lib/api";
import { TerminalCommandPalette } from "./TerminalCommandPalette";
import { TerminalSideNav } from "./TerminalSideNav";
import { TerminalTopBar } from "./TerminalTopBar";

const routeShortcuts: Record<string, string> = {
  d: "/",
  p: "/",
  l: "/",
  s: "/scout",
  r: "/review",
  t: "/trades",
  h: "/trades",
  c: "/engine?tab=status",
  e: "/engine",
  ",": "/settings"
};

export function TerminalShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [workerStatus, setWorkerStatus] = useState<WorkerStatus | null>(null);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [notice, setNotice] = useState("");
  const [currentTime, setCurrentTime] = useState("");
  const routeModeRef = useRef(false);

  const loadStatus = useCallback(async () => {
    try {
      const [system, worker] = await Promise.all([api.systemStatus(), fetchWorkerStatus()]);
      setStatus(system);
      setWorkerStatus(worker);
    } catch {
      setStatus(null);
      setWorkerStatus(null);
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
        setNotice("이동 모드: P 포지션 관제 · S 스카우트 · E 엔진 트레이딩 · R 복기 센터 · , 설정");
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
        window.dispatchEvent(new Event("fce:refresh"));
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
          workerStatus={workerStatus}
          pathname={pathname}
          currentTime={currentTime}
          onCommand={() => setPaletteOpen(true)}
          onRefresh={() => {
            void loadStatus();
            window.dispatchEvent(new Event("fce:refresh"));
          }}
        />
      }
      sideNav={<TerminalSideNav pathname={pathname} status={status} />}
    >
      <div className="terminalWorkspace" data-route={pathname}>
        {notice ? <div className="terminalNotice">{notice}</div> : null}
        {children}
      </div>
      <TerminalCommandPalette isOpen={paletteOpen} onOpenChange={setPaletteOpen} onNotice={setNotice} />
    </AppShell>
  );
}

export type WorkerStatus = {
  status: string;
  scheduler_running?: boolean;
  jobs?: Record<
    string,
    {
      status?: string;
      runs?: number;
      failures?: number;
      consecutive_failures?: number;
      skipped?: number;
      last_success_at?: string | null;
      next_run_at?: string | null;
      current_interval_seconds?: number;
      last_error?: string | null;
    }
  >;
  // 2026-09-01: 잡 기아 판정은 API 에 있었지만 **화면이 읽지 않았다.** 생존·사망 푸시가
  // 강등된 뒤(사용자 지시 2026-08-16) 화면이 유일한 통로인데 그 통로가 없어서,
  // `sync_positions` 가 15시간 죽어 있는 동안 상단 바는 "워커 점검" 한 줄만 보여줬고
  // 포지션 2건의 진입 알림이 통째로 누락됐다. 푸시를 막았으면 화면은 말해야 한다.
  job_starvation?: {
    starved?: string[];
    starved_count?: number;
    healthy_count?: number;
    detail?: Record<string, { reason?: string; effective_age_seconds?: number | null; base_interval_seconds?: number | null; runs?: number }>;
  };
};

async function fetchWorkerStatus(): Promise<WorkerStatus> {
  const response = await fetch(`${API_BASE_URL}/api/system/worker`, { cache: "no-store" });
  if (!response.ok) {
    throw new Error("worker status unavailable");
  }
  return response.json();
}

function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tagName = target.tagName.toLowerCase();
  return tagName === "input" || tagName === "textarea" || tagName === "select" || target.isContentEditable;
}
