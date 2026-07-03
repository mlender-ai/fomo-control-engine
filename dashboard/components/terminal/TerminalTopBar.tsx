"use client";

import { Button } from "@astryxdesign/core/Button";
import { Kbd } from "@astryxdesign/core/Kbd";
import { StatusDot } from "@astryxdesign/core/StatusDot";
import { TopNav } from "@astryxdesign/core/TopNav";
import { Command, RotateCw } from "lucide-react";
import type { SystemStatus } from "@/lib/api";

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
      label="FOMO Control terminal"
      className="terminalTopBar"
      heading={
        <div className="terminalBrand">
          <span className="terminalBrandMark">FC</span>
          <span>
            <strong>FOMO Control Engine</strong>
            <small>Live Position Cockpit</small>
          </span>
        </div>
      }
      centerContent={
        <button className="terminalCommandButton" type="button" onClick={onCommand}>
          <Command size={15} />
          <span>{symbol ? `/${symbol}` : "Command / Position / Sync"}</span>
          <Kbd keys="mod+k" />
        </button>
      }
      endContent={
        <div className="terminalTopActions">
          <div className="terminalProviderStrip" aria-label="System status">
            <span>
              <StatusDot variant={provider === "bitget" ? "success" : "warning"} label={`Provider ${provider}`} />
              {provider.toUpperCase()}
            </span>
            <span>
              <StatusDot variant={publicOk ? "success" : "neutral"} label="Public market data" />
              PUB {status?.bitget_public_api ?? "..."}
            </span>
            <span>
              <StatusDot variant={privateOk ? "success" : "neutral"} label="Private read-only API" />
              POS {status?.bitget_private_api ?? "..."}
            </span>
            <time>{currentTime}</time>
          </div>
          <Button label="Refresh" variant="ghost" size="sm" icon={<RotateCw size={16} />} onClick={onRefresh} />
        </div>
      }
    />
  );
}

function extractSymbol(pathname: string): string {
  const match = pathname.match(/\/(?:dashboard|markets)\/([^/?]+)/);
  return match?.[1]?.toUpperCase() ?? "";
}
