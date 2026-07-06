"use client";

import { useEffect, useMemo, useState } from "react";
import { Radar, RefreshCw, Search, Star, Trash2 } from "lucide-react";
import { TerminalWarning } from "@/components/terminal";
import { SymbolAnalysisView, useAnalysisWorkspace } from "@/components/symbol-analysis-view";
import { EntrySimulator } from "@/components/entry-simulator";
import {
  api,
  type ArmedSetup,
  type CatalogSymbolInfo,
  type ScoutAnalysisResponse,
  type ScoutScanRow,
  type WatchlistEntry
} from "@/lib/api";
import { formatPrice, signedPercent } from "@/lib/format";
import { plainifyTaText, taShortLabel } from "@/lib/labels/taGlossary";
import { phaseHintLabel, trendLabel, volumeStateLabel } from "@/lib/labels/marketStateLabels";

type SortKey = "setup_proximity_pct" | "long_score" | "short_score" | "prz_distance_pct" | "nearest_level_distance_pct" | "liquidity_pool_distance_pct" | "change_24h" | "crowding_score" | "funding_rate";
type AssetFilter = "all" | "crypto" | "stock_index" | "unknown";

const SORT_COLUMNS: Array<{ key: SortKey; label: string; direction: "asc" | "desc" }> = [
  { key: "setup_proximity_pct", label: "셋업 근접도", direction: "asc" },
  { key: "long_score", label: "롱 점수", direction: "desc" },
  { key: "short_score", label: "숏 점수", direction: "desc" },
  { key: "prz_distance_pct", label: "PRZ 거리", direction: "asc" },
  { key: "nearest_level_distance_pct", label: "레벨 거리", direction: "asc" },
  { key: "liquidity_pool_distance_pct", label: "유동성", direction: "asc" },
  { key: "change_24h", label: "24h 변동", direction: "desc" },
  { key: "crowding_score", label: "쏠림", direction: "desc" },
  { key: "funding_rate", label: "펀딩", direction: "desc" }
];

const STALE_SECONDS = 300;

const ASSET_FILTERS: Array<{ id: AssetFilter; label: string }> = [
  { id: "all", label: "전체" },
  { id: "crypto", label: "크립토" },
  { id: "stock_index", label: "주식·지수" },
  { id: "unknown", label: "미분류" }
];

export function ScoutShell() {
  const [watchlist, setWatchlist] = useState<WatchlistEntry[]>([]);
  const [scanRows, setScanRows] = useState<ScoutScanRow[]>([]);
  const [armedSetups, setArmedSetups] = useState<ArmedSetup[]>([]);
  const [scannedAt, setScannedAt] = useState<string>("");
  const [scanning, setScanning] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("setup_proximity_pct");
  const [sortAsc, setSortAsc] = useState(true);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<CatalogSymbolInfo[]>([]);
  const [activeSymbol, setActiveSymbol] = useState<string>("");
  const [assetFilter, setAssetFilter] = useState<AssetFilter>("all");

  async function loadWatchlist() {
    try {
      const response = await api.watchlist();
      setWatchlist(response.items);
    } catch (err) {
      setError(err instanceof Error ? err.message : "관심종목을 불러오지 못했습니다.");
    }
  }

  useEffect(() => {
    void loadWatchlist();
  }, []);

  useEffect(() => {
    if (!query.trim()) {
      setResults([]);
      return;
    }
    const handle = window.setTimeout(async () => {
      try {
        const response = await api.searchSymbols(query, 12);
        setResults(response.symbols);
      } catch {
        setResults([]);
      }
    }, 200);
    return () => window.clearTimeout(handle);
  }, [query]);

  async function runScan(force = false) {
    setScanning(true);
    setError("");
    try {
      const response = await api.scoutScan({ force });
      setScanRows(response.rows);
      setArmedSetups(response.armed_setups ?? []);
      setScannedAt(response.scanned_at);
    } catch (err) {
      setError(err instanceof Error ? err.message : "스캔에 실패했습니다.");
    } finally {
      setScanning(false);
    }
  }

  async function addSymbol(symbol: string) {
    try {
      await api.addWatchlistItem({ symbol });
      setNotice(`${symbol} 관심종목에 추가했습니다.`);
      setQuery("");
      setResults([]);
      await loadWatchlist();
    } catch (err) {
      setError(err instanceof Error ? err.message : "관심종목 추가에 실패했습니다.");
    }
  }

  async function removeSymbol(symbol: string) {
    try {
      await api.removeWatchlistItem(symbol);
      await loadWatchlist();
      setScanRows((rows) => rows.filter((row) => row.symbol !== symbol));
      setArmedSetups((items) => items.filter((item) => item.symbol !== symbol));
    } catch (err) {
      setError(err instanceof Error ? err.message : "관심종목 삭제에 실패했습니다.");
    }
  }

  const sortedRows = useMemo(() => {
    const rows = scanRows.filter((row) => assetMatchesFilter(row.asset_class, assetFilter));
    rows.sort((left, right) => {
      const a = left[sortKey];
      const b = right[sortKey];
      const av = typeof a === "number" ? a : sortAsc ? Number.POSITIVE_INFINITY : Number.NEGATIVE_INFINITY;
      const bv = typeof b === "number" ? b : sortAsc ? Number.POSITIVE_INFINITY : Number.NEGATIVE_INFINITY;
      return sortAsc ? av - bv : bv - av;
    });
    return rows;
  }, [scanRows, sortKey, sortAsc, assetFilter]);

  const filteredWatchlist = useMemo(
    () => watchlist.filter((item) => assetMatchesFilter(item.asset_class, assetFilter)),
    [watchlist, assetFilter]
  );

  function toggleSort(key: SortKey, defaultDir: "asc" | "desc") {
    if (sortKey === key) {
      setSortAsc((value) => !value);
    } else {
      setSortKey(key);
      setSortAsc(defaultDir === "asc");
    }
  }

  async function disarmSetup(setupId: string) {
    try {
      const response = await api.disarmScoutSetup(setupId);
      setArmedSetups((items) => items.map((item) => (item.id === setupId ? response.setup : item)));
      setNotice("셋업 알림을 해제했습니다.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "셋업 해제에 실패했습니다.");
    }
  }

  if (activeSymbol) {
    return <ScoutSymbolView symbol={activeSymbol} onBack={() => setActiveSymbol("")} />;
  }

  return (
    <div className="page scoutPage" data-testid="scout-page">
      <header className="cockpitToolbar">
        <div>
          <p className="eyebrow">진입 전 스카우트</p>
          <h1>관심종목 스캔</h1>
        </div>
        <div className="cockpitToolbarActions">
          <span className="lastSyncText">{scannedAt ? `마지막 스캔 ${new Date(scannedAt).toLocaleTimeString()}` : "스캔 전"}</span>
          <button className="button" onClick={() => void runScan(false)} disabled={scanning || !watchlist.length}>
            <Radar size={16} />
            {scanning ? "스캔 중" : "스캔"}
          </button>
          <button className="iconButton secondary" onClick={() => void runScan(true)} disabled={scanning || !watchlist.length} title="강제 재스캔">
            <RefreshCw size={16} />
          </button>
        </div>
      </header>

      {error ? <TerminalWarning tone="error">{error}</TerminalWarning> : null}
      {notice ? <TerminalWarning tone="info">{notice}</TerminalWarning> : null}

      <div className="scoutSearchBar">
        <Search size={16} />
        <input
          data-testid="scout-search-input"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="심볼 검색 (예: BTC, SOL) — 추가하면 관심종목에 담깁니다"
          aria-label="심볼 검색"
        />
        {results.length ? (
          <div className="scoutSearchResults">
            {results.map((item) => (
              <button key={item.symbol} onClick={() => void addSymbol(item.symbol)} type="button">
                <strong>{item.symbol}</strong>
                <span>
                  <AssetClassBadge assetClass={item.asset_class} />
                  {item.base_coin || "-"} · {item.quote_coin || "-"}
                </span>
                <Star size={13} />
              </button>
            ))}
          </div>
        ) : null}
      </div>

      <p className="scoutDisclaimer">셋업 근접도는 가장 가까운 트리거(반전 후보 구간·구조 레벨)까지의 거리입니다. 매수 추천이 아니라 &ldquo;지금 반응을 지켜볼 종목&rdquo;의 정렬 기준입니다.</p>

      <div className="assetClassTabs" role="group" aria-label="자산 클래스 필터">
        {ASSET_FILTERS.map((filter) => (
          <button
            aria-pressed={assetFilter === filter.id}
            className={assetFilter === filter.id ? "active" : ""}
            key={filter.id}
            onClick={() => setAssetFilter(filter.id)}
            type="button"
          >
            {filter.label}
          </button>
        ))}
      </div>

      {watchlist.length ? (
        scanRows.length ? (
          <div className="scoutTableWrap">
            <table className="scoutTable" data-testid="scout-table">
              <thead>
                <tr>
                  <th>심볼</th>
                  <th>클래스</th>
                  <th>무장</th>
                  {SORT_COLUMNS.map((column) => (
                    <th key={column.key}>
                      <button onClick={() => toggleSort(column.key, column.direction)} type="button">
                        {column.label}
                        {sortKey === column.key ? <span>{sortAsc ? " ▲" : " ▼"}</span> : null}
                      </button>
                    </th>
                  ))}
                  <th>와이코프</th>
                  <th>거래량</th>
                  <th>기준</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {sortedRows.map((row) => (
                  <ScanRow
                    key={row.symbol}
                    row={row}
                    armedSetups={armedSetups.filter((setup) => setup.symbol === row.symbol && setup.status === "armed")}
                    scanReference={scannedAt}
                    onOpen={() => setActiveSymbol(row.symbol)}
                    onRemove={() => void removeSymbol(row.symbol)}
                    onDisarm={disarmSetup}
                  />
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="scoutEmpty">
            <p>관심종목 {filteredWatchlist.length}개가 있습니다. 스캔을 눌러 6개 축으로 비교하세요.</p>
            <div className="scoutWatchChips">
              {filteredWatchlist.map((item) => (
                <span key={item.symbol}>
                  {item.symbol}
                  <em>{assetClassLabel(item.asset_class)}</em>
                  <button onClick={() => void removeSymbol(item.symbol)} type="button" aria-label={`${item.symbol} 삭제`}>
                    <Trash2 size={12} />
                  </button>
                </span>
              ))}
            </div>
          </div>
        )
      ) : (
        <div className="scoutEmpty">
          <p>관심종목이 비어 있습니다. 위 검색으로 심볼을 추가하세요.</p>
        </div>
      )}
    </div>
  );
}

function ScanRow({
  row,
  armedSetups,
  scanReference,
  onOpen,
  onRemove,
  onDisarm
}: {
  row: ScoutScanRow;
  armedSetups: ArmedSetup[];
  scanReference: string;
  onOpen: () => void;
  onRemove: () => void;
  onDisarm: (setupId: string) => void;
}) {
  // 렌더 순수성: 벽시계 대신 스캔 완료 시각(scanReference) 대비 나이로 신선도를 판정.
  // as_of가 스캔 시각보다 5분 이상 과거면 캐시에서 나온 오래된 값.
  const staleSeconds = row.as_of && scanReference ? (new Date(scanReference).getTime() - new Date(row.as_of).getTime()) / 1000 : null;
  const stale = staleSeconds !== null && staleSeconds > STALE_SECONDS;
  if (row.error) {
    return (
      <tr className="scoutRow error">
        <td><strong>{row.symbol}</strong></td>
        <td><AssetClassBadge assetClass={row.asset_class} /></td>
        <td colSpan={SORT_COLUMNS.length + 6}>스캔 실패: {row.error}</td>
        <td>
          <button className="iconButton secondary" onClick={onRemove} type="button" aria-label="삭제"><Trash2 size={13} /></button>
        </td>
      </tr>
    );
  }
  return (
    <tr className={`scoutRow ${stale ? "stale" : ""}`} data-testid="scout-row" onClick={onOpen}>
      <td><strong>{row.symbol}</strong></td>
      <td>
        <AssetClassBadge assetClass={row.asset_class} />
        {row.session?.label ? <small className="sessionMiniBadge">{row.session.label}</small> : null}
      </td>
      <td>
        {armedSetups.length ? (
          <div className="scoutArmedCell">
            <span title={armedSetups[0].basis}>🎯 {armedSetups[0].trigger_label} · {formatPct(armedSetups[0].distance_pct)}</span>
            <button
              type="button"
              onClick={(event) => {
                event.stopPropagation();
                onDisarm(armedSetups[0].id);
              }}
            >
              해제
            </button>
          </div>
        ) : row.setup_candidates?.length ? (
          <span className="scoutSetupMuted">후보 {row.setup_candidates.length}</span>
        ) : (
          <span className="scoutSetupMuted">-</span>
        )}
      </td>
      <td>{formatPct(row.setup_proximity_pct)}</td>
      <td className={scoreTone(row.long_score)}>{row.long_score ?? "-"}</td>
      <td className={scoreTone(row.short_score)}>{row.short_score ?? "-"}</td>
      <td>{row.harmonic_active ? formatPct(row.prz_distance_pct) : "패턴 없음"}</td>
      <td>{formatPct(row.nearest_level_distance_pct)}</td>
      <td title={row.liquidity_nearest_pool?.label ?? ""}>{row.liquidity_nearest_pool ? `${formatPct(row.liquidity_pool_distance_pct)} · ${row.liquidity_nearest_pool.grade ?? "-"}` : "-"}</td>
      <td className={typeof row.change_24h === "number" && row.change_24h >= 0 ? "successText" : "dangerText"}>
        {typeof row.change_24h === "number" ? signedPercent(row.change_24h) : "-"}
      </td>
      <td className={scoreTone(row.crowding_score)}>{typeof row.crowding_score === "number" ? row.crowding_score.toFixed(0) : "-"}</td>
      <td>{row.funding_state ?? formatFunding(row.funding_rate)}</td>
      <td>{phaseHintLabel(row.wyckoff_phase)}{row.top_event ? ` · ${taShortLabel(row.top_event.label)}` : ""}</td>
      <td>{volumeStateLabel(row.volume_state)}</td>
      <td className={stale ? "scoutStaleCell" : ""}>{row.as_of ? new Date(row.as_of).toLocaleTimeString() : "-"}{stale ? " · 오래됨" : ""}</td>
      <td>
        <button className="iconButton secondary" onClick={(event) => { event.stopPropagation(); onRemove(); }} type="button" aria-label="삭제"><Trash2 size={13} /></button>
      </td>
    </tr>
  );
}

function ScoutSymbolView({ symbol, onBack }: { symbol: string; onBack: () => void }) {
  const workspace = useAnalysisWorkspace();
  const [data, setData] = useState<ScoutAnalysisResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [timeframe, setTimeframe] = useState("4h");

  async function load(force = false) {
    setLoading(true);
    setError("");
    try {
      setData(await api.scoutAnalysis(symbol, timeframe, force));
    } catch (err) {
      setData(null);
      setError(err instanceof Error ? err.message : "분석 데이터를 불러오지 못했습니다.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbol, timeframe]);

  const analysis = data?.analysis ?? null;
  const scenarios = analysis?.scenarios ?? null;

  return (
    <div className="page positionDetailPage" data-testid="scout-analysis-view">
      <header className="cockpitToolbar positionDetailToolbar">
        <div>
          <p className="eyebrow">진입 전 분석 · 포지션 없음</p>
          <h1>{symbol} 차트 관제</h1>
        </div>
        <div className="cockpitToolbarActions">
          <label className="timeframeSelect">
            <span>봉 주기</span>
            <select value={timeframe} onChange={(event) => setTimeframe(event.target.value)}>
              <option value="15m">15분봉</option>
              <option value="1h">1시간봉</option>
              <option value="4h">4시간봉</option>
              <option value="1d">1일봉</option>
            </select>
          </label>
          <button className="button secondary" onClick={onBack} type="button">스캔 목록</button>
          <button className="button secondary" onClick={() => void load(true)} disabled={loading} type="button">
            <RefreshCw size={14} />
            재분석
          </button>
        </div>
      </header>

      {error ? <TerminalWarning tone="error">{error}</TerminalWarning> : null}

      <SymbolAnalysisView
        chartAnalysis={analysis}
        chartLoading={loading}
        chartError={error}
        onRetryChart={() => void load(true)}
        trendSummary={analysis ? trendLabel((analysis.wyckoff as { trend?: { direction?: string } })?.trend?.direction) : "구조 확인 중"}
        plan={null}
        analystBriefing={data?.analyst_briefing ?? null}
        workspace={workspace}
        gridClassName="positionDetailMain"
        sidePanel={
          <div className="scoutSidePanel">
            <EntrySimulator symbol={symbol} markPrice={analysis?.mark_price ?? null} timeframe={timeframe} />
            <ScenarioPanel scenarios={scenarios} asOf={data?.as_of} />
          </div>
        }
      />
    </div>
  );
}

function ScenarioPanel({ scenarios, asOf }: { scenarios: ScoutAnalysisResponse["analysis"]["scenarios"]; asOf?: string }) {
  if (!scenarios) {
    return (
      <section className="focusPanel actionPlanPanel">
        <div className="focusPanelHeader">
          <div><h2>양방향 시나리오</h2><p>차트 데이터를 기다리는 중입니다.</p></div>
        </div>
        <div className="terminalEmpty">시나리오 데이터가 준비되면 표시됩니다.</div>
      </section>
    );
  }
  return (
    <section className="focusPanel actionPlanPanel">
      <div className="focusPanelHeader">
        <div>
          <h2>양방향 시나리오</h2>
          <p>포지션이 없어 롱/숏 트리거를 모두 표시합니다 · 추천 아님</p>
        </div>
        <span>{asOf ? `기준 ${new Date(asOf).toLocaleTimeString()}` : "-"}</span>
      </div>
      <ScenarioBlock title="롱 진입 시" scenario={scenarios.long} />
      <ScenarioBlock title="숏 진입 시" scenario={scenarios.short} />
    </section>
  );
}

function ScenarioBlock({ title, scenario }: { title: string; scenario: { invalidation: { price: number | null; basis: string; distance_pct: number | null; action: string } | null; take_profit: Array<{ price: number | null; basis: string; distance_pct: number | null; action: string }>; watch_triggers: Array<{ condition: string; meaning: string }> } }) {
  return (
    <div className="scenarioBlock">
      <strong>{title}</strong>
      {scenario.invalidation ? (
        <div className="actionPlanRow tone-danger">
          <span>무효화</span>
          <strong>{scenario.invalidation.price === null ? "-" : formatPrice(scenario.invalidation.price)} · {formatPct(scenario.invalidation.distance_pct)}</strong>
          <em>{plainifyTaText(scenario.invalidation.action)}</em>
          <small>{plainifyTaText(scenario.invalidation.basis)}</small>
        </div>
      ) : null}
      {scenario.take_profit.map((target, index) => (
        <div className="actionPlanRow tone-positive" key={`tp-${index}`}>
          <span>익절{scenario.take_profit.length > 1 ? index + 1 : ""}</span>
          <strong>{target.price === null ? "-" : formatPrice(target.price)} · {formatPct(target.distance_pct)}</strong>
          <em>{plainifyTaText(target.action)}</em>
          <small>{plainifyTaText(target.basis)}</small>
        </div>
      ))}
      {scenario.watch_triggers.map((trigger, index) => (
        <div className="actionPlanRow tone-warning" key={`watch-${index}`}>
          <span>감시</span>
          <strong>{plainifyTaText(trigger.condition)}</strong>
          <em>확인</em>
          <small>{trigger.meaning}</small>
        </div>
      ))}
      {!scenario.invalidation && !scenario.take_profit.length && !scenario.watch_triggers.length ? (
        <div className="terminalEmpty">이 방향의 트리거 근거가 아직 부족합니다.</div>
      ) : null}
    </div>
  );
}

function formatPct(value: number | null | undefined): string {
  if (typeof value !== "number") return "-";
  return `${value.toFixed(2)}%`;
}

function scoreTone(score: number | null | undefined): string {
  if (typeof score !== "number") return "";
  if (score >= 70) return "successText";
  if (score <= 35) return "dangerText";
  return "";
}

function formatFunding(value: number | null | undefined): string {
  if (typeof value !== "number") return "-";
  return `${value > 0 ? "+" : ""}${(value * 100).toFixed(4)}%`;
}

function assetMatchesFilter(assetClass: string | null | undefined, filter: AssetFilter): boolean {
  if (filter === "all") return true;
  if (filter === "stock_index") return assetClass === "stock" || assetClass === "index";
  if (filter === "unknown") return !assetClass || assetClass === "unknown";
  return assetClass === filter;
}

function AssetClassBadge({ assetClass }: { assetClass?: string | null }) {
  const normalized = assetClass || "unknown";
  return <span className={`assetClassBadge asset-${normalized}`}>{assetClassLabel(normalized)}</span>;
}

function assetClassLabel(assetClass?: string | null): string {
  if (assetClass === "crypto") return "크립토";
  if (assetClass === "stock") return "주식";
  if (assetClass === "index") return "지수";
  return "미분류";
}
