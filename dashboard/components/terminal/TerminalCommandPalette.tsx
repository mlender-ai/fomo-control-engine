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
      command("nav-live-positions", "Open Live Positions", "Navigation", "Live Position Intelligence Cockpit", ["positions", "live", "cockpit", "gp"], () => router.push("/"), "g p"),
      command("nav-trades", "Open Trade History", "Navigation", "Closed trade review and replay", ["journal", "trades", "history", "gt"], () => router.push("/trades"), "g t"),
      command("nav-settings", "Open Settings", "Navigation", "API and terminal configuration", ["settings", "config"], () => router.push("/settings"), "g ,"),
      command("sync-positions", "/sync positions", "Actions", "Read-only Bitget sync plus deterministic position analysis", ["bitget", "private", "positions", "sync"], async () => {
        const result = await api.syncLivePositions();
        onNotice(`Live sync ${result.status}: created ${result.created}, updated ${result.updated}, analyzed ${result.positions?.length ?? 0}`);
        router.refresh();
      }),
      command("test-bitget", "/test bitget", "Actions", "Check public market data and private read-only position access", ["bitget", "test", "connection"], async () => {
        const result = await api.testBitgetConnection();
        onNotice(`Bitget public ${result.public_market_data.ok ? "OK" : "ERROR"} · private ${result.private_positions.status}`);
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
