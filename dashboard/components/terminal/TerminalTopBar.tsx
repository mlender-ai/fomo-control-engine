"use client";

import { Button } from "@astryxdesign/core/Button";
import { Kbd } from "@astryxdesign/core/Kbd";
import { StatusDot } from "@astryxdesign/core/StatusDot";
import { TopNav } from "@astryxdesign/core/TopNav";
import { Command, RotateCw } from "lucide-react";
import type { SystemStatus } from "@/lib/api";
import { connectionStatusLabel, sourceLabel } from "@/lib/labels/marketStateLabels";

export function TerminalTopBar({
  status,
  pathname,
  currentTime,
  onCommand,
  onRefresh
}: {
  status: SystemStatus | null;
  pathname: string;
  currentTime: string;
  onCommand: () => void;
  onRefresh: () => void;
}) {
  const provider = status?.market_data_provider ?? "loading";
  const symbol = extractSymbol(pathname);
  const publicOk = status?.bitget_public_api === "ok" || status?.bitget_public_api === "available";
  const privateOk = status?.bitget_private_api === "ok" || status?.bitget_private_api === "configured";

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
            <span>
              <StatusDot variant={publicOk ? "success" : "neutral"} label="공개 시세 데이터" />
              시세 {connectionStatusLabel(status?.bitget_public_api)}
            </span>
            <span>
              <StatusDot variant={privateOk ? "success" : "neutral"} label="포지션 read-only API" />
              포지션 {connectionStatusLabel(status?.bitget_private_api)}
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
