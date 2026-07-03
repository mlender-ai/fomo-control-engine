"use client";

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { CommandPalette, CommandPaletteFooter } from "@astryxdesign/core/CommandPalette";
import { Kbd } from "@astryxdesign/core/Kbd";
import { createStaticSource, type SearchableItem } from "@astryxdesign/core/Typeahead";
import { api } from "@/lib/api";

type TerminalCommand = SearchableItem & {
  action: () => Promise<void> | void;
  shortcut?: string;
  group: string;
  detail: string;
  keywords: string[];
};

export function TerminalCommandPalette({
  isOpen,
  onOpenChange,
  onNotice
}: {
  isOpen: boolean;
  onOpenChange: (open: boolean) => void;
  onNotice: (message: string) => void;
}) {
  const router = useRouter();
  const [value, setValue] = useState("");

  const commands = useMemo<TerminalCommand[]>(
    () => [
      command("nav-dashboard", "Open Dashboard", "Navigation", "Home workspace", ["dashboard", "home", "gd"], () => router.push("/"), "g d"),
      command("nav-markets", "Open Markets", "Navigation", "Market report monitor", ["markets", "watch", "gm"], () => router.push("/markets"), "g m"),
      command("nav-research", "Open Research Runs", "Navigation", "Agentic research timeline", ["research", "agents", "gr"], () => router.push("/research"), "g r"),
      command("nav-positions", "Open Positions", "Navigation", "Read-only position monitor", ["positions", "gp"], () => router.push("/positions"), "g p"),
      command("nav-journal", "Open Journal", "Navigation", "Closed trade review", ["journal", "trades", "gj"], () => router.push("/journal"), "g j"),
      command("nav-shadow", "Open Shadow Account", "Navigation", "Behavior pattern extraction", ["shadow", "gs"], () => router.push("/shadow"), "g s"),
      command("nav-validation", "Open Validation Lab", "Navigation", "Monte Carlo and walk-forward checks", ["validation", "gv"], () => router.push("/validation"), "g v"),
      command("nav-settings", "Open Settings", "Navigation", "API and terminal configuration", ["settings", "config"], () => router.push("/settings"), "g ,"),
      command("report-btc", "/report BTCUSDT", "Market Data", "Open latest BTC report", ["btc", "btcusdt", "report"], () => router.push("/dashboard/BTCUSDT"), "enter"),
      command("report-eth", "/report ETHUSDT", "Market Data", "Open latest ETH report", ["eth", "ethusdt", "report"], () => router.push("/dashboard/ETHUSDT"), "enter"),
      command("run-research-btc", "/research BTCUSDT", "Actions", "Create a deterministic snapshot review", ["agent", "run", "btc"], async () => {
        const result = await api.createResearchRun({ symbol: "BTCUSDT", timeframe: "4h" });
        router.push(`/research/${result.research_run_id}`);
        onNotice(`Research run created for ${result.symbol}`);
      }),
      command("sync-positions", "/sync positions", "Actions", "Read-only Bitget position sync", ["bitget", "private", "positions"], async () => {
        const result = await api.syncBitgetPositions();
        onNotice(`Position sync ${result.status}: created ${result.created}, updated ${result.updated}`);
        router.refresh();
      }),
      command("extract-shadow", "/extract shadow", "Actions", "Extract behavior profile from closed trades", ["shadow", "journal", "trades"], async () => {
        const result = await api.extractShadow();
        onNotice(`Shadow profile ${result.shadow_id} created`);
        router.push("/shadow");
      }),
      command("run-validation", "/validate BTCUSDT", "Actions", "Run validation lab with deterministic seed", ["validation", "monte", "bootstrap"], async () => {
        const result = await api.runValidation({ symbol: "BTCUSDT", timeframe: "4h" });
        onNotice(`Validation run created for ${result.symbol}`);
        router.push("/validation");
      })
    ],
    [onNotice, router]
  );

  const searchSource = useMemo(
    () => createStaticSource(commands, { keywords: (item) => item.keywords }),
    [commands]
  );

  async function selectCommand(id: string) {
    const selected = commands.find((item) => item.id === id);
    if (!selected) return;
    setValue(id);
    onOpenChange(false);
    try {
      await selected.action();
    } catch (err) {
      onNotice(err instanceof Error ? err.message : "Command failed");
    } finally {
      setValue("");
    }
  }

  return (
    <CommandPalette<TerminalCommand>
      isOpen={isOpen}
      onOpenChange={onOpenChange}
      searchSource={searchSource}
      value={value}
      onValueChange={selectCommand}
      label="FOMO Control command palette"
      width={720}
      maxHeight={560}
      emptyBootstrapText="Type a route, symbol, or slash command"
      emptySearchText="No matching command"
      renderItem={(item) => (
        <div className="terminalCommandItem">
          <div>
            <strong>{item.label}</strong>
            <span>{item.detail}</span>
          </div>
          {item.shortcut ? <Kbd keys={item.shortcut.replaceAll(" ", "+")} /> : null}
        </div>
      )}
      footer={
        <CommandPaletteFooter>
          <span><Kbd keys="up" /> <Kbd keys="down" /> Navigate</span>
          <span><Kbd keys="enter" /> Run</span>
          <span><Kbd keys="escape" /> Close</span>
        </CommandPaletteFooter>
      }
    />
  );
}

function command(
  id: string,
  label: string,
  group: string,
  detail: string,
  keywords: string[],
  action: () => Promise<void> | void,
  shortcut?: string
): TerminalCommand {
  return {
    id,
    label,
    action,
    shortcut,
    group,
    detail,
    keywords,
    auxiliaryData: { group }
  };
}
