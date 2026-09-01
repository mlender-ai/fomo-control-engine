"use client";

import { StatusDot } from "@astryxdesign/core/StatusDot";
import { Activity, Command, RotateCw } from "lucide-react";
import type { SystemStatus } from "@/lib/api";
import { connectionStatusLabel, sourceLabel } from "@/lib/labels/marketStateLabels";
import type { WorkerStatus } from "./TerminalShell";

export function TerminalTopBar({
  status,
  workerStatus,
  pathname,
  currentTime,
  onCommand,
  onRefresh
}: {
  status: SystemStatus | null;
  workerStatus: WorkerStatus | null;
  pathname: string;
  currentTime: string;
  onCommand: () => void;
  onRefresh: () => void;
}) {
  const provider = status?.market_data_provider ?? "loading";
  const symbol = extractSymbol(pathname);
  const publicOk = status?.bitget_public_api === "ok" || status?.bitget_public_api === "available";
  const privateOk = status?.bitget_private_api === "ok" || status?.bitget_private_api === "configured";
  const worker = workerSummary(workerStatus);

  return (
    <header className="terminalTopBar" aria-label="FOMO Control 터미널">
      <div className="terminalBrand">
        <span className="terminalBrandMark"><Activity size={17} /></span>
        <span>
          <strong>FOMO Control</strong>
          <small>Position Intelligence</small>
        </span>
      </div>

      <button className="terminalCommandButton" type="button" onClick={onCommand}>
        <Command size={15} />
        <span>{symbol ? `${symbol} 빠른 명령` : "심볼, 포지션, 기능 검색"}</span>
        <kbd>⌘ K</kbd>
      </button>

      <div className="terminalTopActions">
        <div className="terminalProviderStrip" aria-label="시스템 상태">
          <span className="providerPrimary" title={`공개 시세 ${connectionStatusLabel(status?.bitget_public_api)} · 포지션 ${connectionStatusLabel(status?.bitget_private_api)}`}>
            <StatusDot variant={provider === "bitget" && publicOk && privateOk ? "success" : "warning"} label={`데이터 제공자 ${sourceLabel(provider)}`} />
            {sourceLabel(provider)} Live
          </span>
          {status?.demo_mode ? <span className="demoModeBadge" data-testid="demo-mode-badge">DEMO</span> : null}
          <span className="workerState" title={worker.label}>
            <StatusDot variant={worker.ok ? "success" : "error"} label={worker.label} />
            {worker.ok ? "자동 관제" : "관제 점검"}
          </span>
          <time>{currentTime}</time>
        </div>
        <button className="topBarIconButton" type="button" onClick={onRefresh} aria-label="새로고침" title="새로고침">
          <RotateCw size={16} />
        </button>
      </div>
    </header>
  );
}

function extractSymbol(pathname: string): string {
  const match = pathname.match(/\/dashboard\/([^/?]+)/);
  return match?.[1]?.toUpperCase() ?? "";
}

function workerSummary(status: WorkerStatus | null): { ok: boolean; label: string } {
  if (!status) {
    return { ok: false, label: "워커 확인 중" };
  }
  const jobs = status.jobs ?? {};
  const syncJob = jobs.sync_positions ?? jobs.position_sync;
  const failing = Object.values(jobs).some((job) => job.status === "error" || Number(job.consecutive_failures ?? 0) > 0);
  // 굶은 잡을 생존 판정에 넣는다. `status: running` 은 스케줄러가 살아 있다는 뜻일 뿐이고,
  // 그 안에서 잡이 굶어도 running 이다 — 2026-09-01 침묵이 정확히 그 모양이었다.
  const starved = Number(status.job_starvation?.starved_count ?? 0);
  const ok = status.status === "running" && !failing && starved === 0;
  const lastSync = timeAgo(syncJob?.last_success_at);
  if (starved > 0) {
    const names = (status.job_starvation?.starved ?? []).slice(0, 2).join(", ");
    return { ok: false, label: `잡 굶음 ${starved}건${names ? ` (${names})` : ""} · 마지막 sync ${lastSync}` };
  }
  // **"워커 정상 · 마지막 sync 성공 이력 없음" 은 모순이다.** 포지션 동기화가 한 번도
  // 성공하지 않았으면 워커가 정상일 수 없다 — 원장이 비어 있고 알림도 나갈 수 없다.
  // 재기동 직후 몇 분간은 이 상태가 정상이므로 "대기" 로 쓰고, 첫 성공에 저절로 걷힌다.
  if (syncJob && !syncJob.last_success_at) {
    return { ok: false, label: "sync 첫 실행 대기 — 포지션 동기화 성공 이력 없음" };
  }
  return { ok, label: ok ? `워커 정상 · 마지막 sync ${lastSync}` : `워커 점검 · 마지막 sync ${lastSync}` };
}

function timeAgo(value: string | null | undefined): string {
  // `null` 을 "-" 로 뭉개면 **"한 번도 성공한 적 없음"이 "값 없음"처럼 보인다.**
  // `sync_positions.last_success_at` 이 15시간 내내 null 이었는데 화면은 "-" 였다.
  if (value === null) return "성공 이력 없음";
  if (!value) return "-";
  const timestamp = new Date(value).getTime();
  if (!Number.isFinite(timestamp)) return "-";
  const seconds = Math.max(0, Math.floor((Date.now() - timestamp) / 1000));
  if (seconds < 60) return `${seconds}초 전`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}분 전`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}시간 전`;
  return `${Math.floor(hours / 24)}일 전`;
}
