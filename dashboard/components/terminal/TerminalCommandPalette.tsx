"use client";

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { CommandPalette, CommandPaletteFooter } from "@astryxdesign/core/CommandPalette";
import { Kbd } from "@astryxdesign/core/Kbd";
import { createStaticSource, type SearchableItem } from "@astryxdesign/core/Typeahead";
import { api } from "@/lib/api";
import { connectionStatusLabel } from "@/lib/labels/marketStateLabels";

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
      command("nav-live-positions", "라이브 포지션 열기", "이동", "실시간 포지션 관제 화면", ["positions", "live", "cockpit", "gp", "포지션", "관제"], () => router.push("/"), "g p"),
      command("nav-scout", "스카우트 열기", "이동", "관심종목 스캔과 진입 전 분석", ["scout", "watchlist", "gs", "스카우트", "관심종목", "검색", "symbol"], () => router.push("/scout"), "g s"),
      command("nav-trades", "거래 복기 열기", "이동", "종료 거래 기록과 복기", ["journal", "trades", "history", "gt", "거래", "복기"], () => router.push("/trades"), "g t"),
      command("nav-settings", "설정 열기", "이동", "API와 터미널 설정", ["settings", "config", "설정"], () => router.push("/settings"), "g ,"),
      command("sync-positions", "/포지션 동기화", "실행", "Bitget read-only 포지션 동기화와 결정론적 분석", ["bitget", "private", "positions", "sync", "동기화"], async () => {
        const result = await api.syncLivePositions();
        onNotice(`라이브 동기화 ${connectionStatusLabel(result.status)}: 생성 ${result.created}, 갱신 ${result.updated}, 분석 ${result.positions?.length ?? 0}`);
        router.refresh();
      }),
      command("test-bitget", "/Bitget 테스트", "실행", "공개 시세와 read-only 포지션 접근 확인", ["bitget", "test", "connection", "테스트"], async () => {
        const result = await api.testBitgetConnection();
        onNotice(`Bitget 공개 시세 ${result.public_market_data.ok ? "OK" : "ERROR"} · 포지션 권한 ${connectionStatusLabel(result.private_positions.status)}`);
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
      onNotice(err instanceof Error ? err.message : "명령 실행에 실패했습니다.");
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
      label="FOMO Control 명령 팔레트"
      width={720}
      maxHeight={560}
      emptyBootstrapText="이동 경로, 심볼, 명령어를 입력하세요"
      emptySearchText="일치하는 명령이 없습니다"
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
          <span><Kbd keys="up" /> <Kbd keys="down" /> 이동</span>
          <span><Kbd keys="enter" /> 실행</span>
          <span><Kbd keys="escape" /> 닫기</span>
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
