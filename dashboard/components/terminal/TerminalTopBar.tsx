"use client";

import { Button } from "@astryxdesign/core/Button";
import { Kbd } from "@astryxdesign/core/Kbd";
import { StatusDot } from "@astryxdesign/core/StatusDot";
import { TopNav } from "@astryxdesign/core/TopNav";
import { Command, RotateCw } from "lucide-react";
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
    <TopNav
      label="FOMO Control 터미널"
      className="terminalTopBar"
      heading={
        <div className="terminalBrand">
          <span className="terminalBrandMark">FC</span>
          <span>
            <strong>FOMO Control Engine</strong>
            <small>라이브 포지션 관제</small>
          </span>
        </div>
      }
      centerContent={
        <button className="terminalCommandButton" type="button" onClick={onCommand}>
          <Command size={15} />
          <span>{symbol ? `/${symbol}` : "명령 / 포지션 / 동기화"}</span>
          <Kbd keys="mod+k" />
        </button>
      }
      endContent={
        <div className="terminalTopActions">
          <div className="terminalProviderStrip" aria-label="시스템 상태">
            <span>
              <StatusDot variant={provider === "bitget" ? "success" : "warning"} label={`데이터 제공자 ${sourceLabel(provider)}`} />
              {sourceLabel(provider)}
            </span>
            {status?.demo_mode ? <span className="demoModeBadge" data-testid="demo-mode-badge">DEMO</span> : null}
            <span>
              <StatusDot variant={publicOk ? "success" : "neutral"} label="공개 시세 데이터" />
              시세 {connectionStatusLabel(status?.bitget_public_api)}
            </span>
            <span>
              <StatusDot variant={privateOk ? "success" : "neutral"} label="포지션 read-only API" />
              포지션 {connectionStatusLabel(status?.bitget_private_api)}
            </span>
            <span>
              <StatusDot variant={worker.ok ? "success" : "error"} label={worker.label} />
              {worker.label}
            </span>
            <time>{currentTime}</time>
          </div>
          <Button label="새로고침" variant="ghost" size="sm" icon={<RotateCw size={16} />} onClick={onRefresh} />
        </div>
      }
    />
  );
}

function extractSymbol(pathname: string): string {
  const match = pathname.match(/\/(?:dashboard|markets)\/([^/?]+)/);
  return match?.[1]?.toUpperCase() ?? "";
}

function workerSummary(status: WorkerStatus | null): { ok: boolean; label: string } {
  if (!status) {
    return { ok: false, label: "워커 확인 중" };
  }
  const jobs = status.jobs ?? {};
  const syncJob = jobs.sync_positions ?? jobs.position_sync;
  const failing = Object.values(jobs).some((job) => job.status === "error" || Number(job.consecutive_failures ?? 0) > 0);
  const ok = status.status === "running" && !failing;
  const lastSync = timeAgo(syncJob?.last_success_at);
  return { ok, label: ok ? `워커 정상 · 마지막 sync ${lastSync}` : `워커 점검 · 마지막 sync ${lastSync}` };
}

function timeAgo(value: string | null | undefined): string {
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
