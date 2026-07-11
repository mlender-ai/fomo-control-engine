"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { MapPin, Radar, RefreshCw, Search, Star, Trash2 } from "lucide-react";
import { CompactChartWorkspace, type CompactNextPrice } from "@/components/position/CompactChartWorkspace";
import { TerminalWarning } from "@/components/terminal";
import { SymbolAnalysisView, useAnalysisWorkspace } from "@/components/symbol-analysis-view";
import { EntrySimulator } from "@/components/entry-simulator";
import {
  api,
  type AnalystConfluence,
  type ArmedSetup,
  type CatalogSymbolInfo,
  type CatalogStatus,
  type EntryIntent,
  type HistoricalBacktest,
  type OneLinerLine,
  type OneLinerSummary,
  type ScoutAnalysisResponse,
  type ScoutScanRow,
  type UniverseDiscovery,
  type WatchlistEntry
} from "@/lib/api";
import { type MinimalEvidenceLayer } from "@/lib/chartLayers";
import { formatPrice, signedPercent } from "@/lib/format";
import { plainifyTaText, taShortLabel } from "@/lib/labels/taGlossary";
import { phaseHintLabel, trendLabel, volumeStateLabel } from "@/lib/labels/marketStateLabels";
import { loadFceViewMode, saveFceViewMode, type FceViewMode } from "@/lib/viewMode";

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
const SCOUT_ENTRY_TOOLS_VISIBLE = false;

type ScoutMinimalEvidence = {
  key: string;
  text: string;
  layer: MinimalEvidenceLayer;
  label: string;
  price?: number | null;
  time?: number | null;
};

type ScoutVerdictTone = "long" | "short" | "neutral" | "conflicted" | "insufficient";

type ScoutAnalysisVerdict = {
  tone: ScoutVerdictTone;
  label: string;
  why: string;
  counter: string;
  trigger: string;
  position: number;
  counts: { up: number; down: number; neutral: number; unknown: number };
  evidence: ScoutMinimalEvidence;
};

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
  const [entryIntents, setEntryIntents] = useState<EntryIntent[]>([]);
  const [discoveries, setDiscoveries] = useState<UniverseDiscovery[]>([]);
  const [scannedAt, setScannedAt] = useState<string>("");
  const [scanning, setScanning] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("setup_proximity_pct");
  const [sortAsc, setSortAsc] = useState(true);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<CatalogSymbolInfo[]>([]);
  const [catalogStatus, setCatalogStatus] = useState<CatalogStatus | null>(null);
  const [catalogRefreshing, setCatalogRefreshing] = useState(false);
  const [quickSymbol, setQuickSymbol] = useState("");
  const [quickAnswer, setQuickAnswer] = useState<ScoutAnalysisResponse | null>(null);
  const [quickLoading, setQuickLoading] = useState(false);
  const [quickError, setQuickError] = useState("");
  const [activeSymbol, setActiveSymbol] = useState<string>("");
  const [assetFilter, setAssetFilter] = useState<AssetFilter>("all");
  const [viewMode, setViewMode] = useState<FceViewMode>("minimal");
  const quickRequestRef = useRef("");
  const autoScanKeyRef = useRef("");

  useEffect(() => {
    setViewMode(loadFceViewMode());
  }, []);

  function updateViewMode(mode: FceViewMode) {
    setViewMode(mode);
    saveFceViewMode(mode);
  }

  async function loadWatchlist() {
    try {
      const response = await api.watchlist();
      setWatchlist(response.items);
      setError("");
      const [intentsResult, discoveryResult] = await Promise.allSettled([
        SCOUT_ENTRY_TOOLS_VISIBLE ? api.entryIntents(undefined, "active") : Promise.resolve({ intents: [] as EntryIntent[] }),
        api.universeDiscoveries({ limit: 20 })
      ]);
      if (intentsResult.status === "fulfilled") setEntryIntents(intentsResult.value.intents);
      if (discoveryResult.status === "fulfilled") setDiscoveries(discoveryResult.value.discoveries);
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
      setQuickSymbol("");
      setQuickAnswer(null);
      setQuickError("");
      return;
    }
    const handle = window.setTimeout(async () => {
      try {
        const response = await api.searchSymbols(query, 12);
        setCatalogStatus(response.catalog_status);
        const symbols = mergeSearchResultsWithDirectCandidate(response.symbols, query);
        setResults(symbols);
        const first = symbols[0];
        if (first) void loadQuickAnswer(first.symbol);
        else {
          setQuickSymbol("");
          setQuickAnswer(null);
        }
      } catch (error) {
        setCatalogStatus({
          count: 0,
          updated_at: null,
          last_error: error instanceof Error ? error.message : "심볼 카탈로그를 확인하지 못했습니다."
        });
        const fallback = directSymbolCandidate(query);
        setResults(fallback ? [fallback] : []);
        if (fallback) void loadQuickAnswer(fallback.symbol);
        else setQuickAnswer(null);
      }
    }, 200);
    return () => window.clearTimeout(handle);
  }, [query]);

  async function retryCatalog() {
    setCatalogRefreshing(true);
    try {
      const response = await api.refreshSymbolCatalog();
      setCatalogStatus(response.catalog_status);
      if (query.trim()) {
        const searched = await api.searchSymbols(query, 12);
        setCatalogStatus(searched.catalog_status);
        setResults(mergeSearchResultsWithDirectCandidate(searched.symbols, query));
      }
    } catch (error) {
      setCatalogStatus({
        count: 0,
        updated_at: null,
        last_error: error instanceof Error ? error.message : "심볼 카탈로그 재수집에 실패했습니다."
      });
    } finally {
      setCatalogRefreshing(false);
    }
  }

  async function loadQuickAnswer(symbol: string, force = false) {
    const normalized = symbol.trim().toUpperCase();
    if (!normalized) return;
    const requestKey = `${normalized}:${Date.now()}`;
    quickRequestRef.current = requestKey;
    setQuickSymbol(normalized);
    setQuickLoading(true);
    setQuickError("");
    try {
      const response = await api.scoutAnalysis(normalized, "4h", force, false);
      if (quickRequestRef.current !== requestKey) return;
      setQuickAnswer(response);
      setError("");
    } catch (err) {
      if (quickRequestRef.current !== requestKey) return;
      setQuickAnswer(null);
      setQuickError(err instanceof Error ? err.message : "즉답 분석을 불러오지 못했습니다.");
    } finally {
      if (quickRequestRef.current === requestKey) setQuickLoading(false);
    }
  }

  async function runScan(force = false) {
    setScanning(true);
    setError("");
    try {
      const response = await api.scoutScan({ force });
      setScanRows(response.rows);
      setArmedSetups(response.armed_setups ?? []);
      if (SCOUT_ENTRY_TOOLS_VISIBLE) {
        setEntryIntents((current) => response.entry_intents ?? current);
      }
      setScannedAt(response.scanned_at);
      setError("");
      try {
        const discoveryResponse = await api.universeDiscoveries({ limit: 20 });
        setDiscoveries(discoveryResponse.discoveries);
      } catch {
        // Universe history is supplementary; a failure must not hide a
        // successful watchlist scan or quick symbol answer.
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "스캔에 실패했습니다.");
    } finally {
      setScanning(false);
    }
  }

  useEffect(() => {
    if (!watchlist.length || scanRows.length || scanning) return;
    const key = watchlist.map((item) => item.symbol).sort().join("|");
    if (!key || autoScanKeyRef.current === key) return;
    autoScanKeyRef.current = key;
    void runScan(false);
    // runScan intentionally stays out of deps; this is a one-shot scan per watchlist composition.
  }, [watchlist, scanRows.length, scanning]);

  async function addSymbol(symbol: string) {
    try {
      await api.addWatchlistItem({ symbol });
      setNotice(`${symbol} 관심종목에 추가했습니다.`);
      setQuery("");
      setResults([]);
      await loadWatchlist();
      setScanRows([]);
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
      setEntryIntents((items) => items.filter((item) => item.symbol !== symbol));
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

  async function cancelIntent(intentId: string) {
    try {
      const response = await api.cancelEntryIntent(intentId);
      setEntryIntents((items) => items.map((item) => (item.id === intentId ? response.intent : item)));
      setNotice("진입 의도를 해제했습니다.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "진입 의도 해제에 실패했습니다.");
    }
  }

  async function runUniverseScan() {
    setScanning(true);
    setError("");
    try {
      const response = await api.universeScan({ force: true });
      setDiscoveries((items) => mergeUniverseDiscoveries(response.discoveries, items).slice(0, 50));
      const passed = response.discoveries.filter((item) => item.gate_passed).length;
      setNotice(`유니버스 스캔 완료 · 게이트 통과 ${passed}건 / 전체 기록 ${response.discoveries.length}건`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "유니버스 스캔에 실패했습니다.");
    } finally {
      setScanning(false);
    }
  }

  if (activeSymbol) {
    return <ScoutSymbolView symbol={activeSymbol} viewMode={viewMode} onViewModeChange={updateViewMode} onBack={() => setActiveSymbol("")} />;
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
          <ViewModeToggle mode={viewMode} onChange={updateViewMode} />
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
              <div className="scoutSearchResultRow" key={item.symbol}>
                <button onClick={() => void loadQuickAnswer(item.symbol)} type="button">
                  <strong>{item.symbol}</strong>
                  <span>
                    <AssetClassBadge assetClass={item.asset_class} />
                    {item.base_coin || "-"} · {item.quote_coin || "-"}
                  </span>
                </button>
                <button className="scoutSearchAdd" onClick={() => void addSymbol(item.symbol)} type="button" aria-label={`${item.symbol} 관심종목 추가`}>
                  <Star size={13} />
                </button>
              </div>
            ))}
          </div>
        ) : null}
      </div>

      {catalogStatus?.count === 0 ? (
        <div className="catalogStatusBanner" data-testid="catalog-status-banner" role="status">
          <div>
            <strong>심볼 카탈로그 미수집</strong>
            <span>{catalogStatus.last_error || "백그라운드 워커가 심볼 목록을 준비하고 있습니다."}</span>
          </div>
          <button className="button secondary" type="button" onClick={() => void retryCatalog()} disabled={catalogRefreshing}>
            <RefreshCw size={15} />
            {catalogRefreshing ? "재수집 중" : "재시도"}
          </button>
        </div>
      ) : null}

      <p className="scoutDisclaimer">셋업 근접도는 가장 가까운 트리거(반전 후보 구간·구조 레벨)까지의 거리입니다. 매수 판단 문구가 아니라 &ldquo;지금 반응을 지켜볼 종목&rdquo;의 정렬 기준입니다.</p>

      <ScoutQuickAnswerCard
        symbol={quickSymbol}
        data={quickAnswer}
        loading={quickLoading}
        error={quickError}
        onRefresh={(symbol) => void loadQuickAnswer(symbol, true)}
        onOpen={(symbol) => setActiveSymbol(symbol)}
      />

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

      <UniverseDiscoveryPanel discoveries={discoveries} assetFilter={assetFilter} onOpen={(symbol) => setActiveSymbol(symbol)} onScan={() => void runUniverseScan()} scanning={scanning} />

      {watchlist.length ? (
        scanRows.length ? (
          sortedRows.length ? (
            <div className="scoutTableWrap">
              {viewMode === "minimal" ? (
                <ScoutMinimalTable
                  rows={sortedRows}
                  armedSetups={armedSetups}
                  entryIntents={SCOUT_ENTRY_TOOLS_VISIBLE ? entryIntents : []}
                  onOpen={(symbol) => setActiveSymbol(symbol)}
                  onRemove={(symbol) => void removeSymbol(symbol)}
                />
              ) : (
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
                      entryIntents={SCOUT_ENTRY_TOOLS_VISIBLE ? entryIntents.filter((intent) => intent.symbol === row.symbol && intent.status === "active") : []}
                      scanReference={scannedAt}
                      onOpen={() => setActiveSymbol(row.symbol)}
                      onRemove={() => void removeSymbol(row.symbol)}
                      onDisarm={disarmSetup}
                      onCancelIntent={cancelIntent}
                    />
                  ))}
                </tbody>
              </table>
              )}
            </div>
          ) : (
            <div className="scoutEmpty">
              <p>현재 필터에 맞는 스캔 결과가 없습니다. 전체 필터로 바꾸거나 관심종목을 추가하세요.</p>
            </div>
          )
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

function mergeSearchResultsWithDirectCandidate(items: CatalogSymbolInfo[], query: string): CatalogSymbolInfo[] {
  const candidate = directSymbolCandidate(query);
  if (!candidate) return items;
  const seen = new Set(items.map((item) => item.symbol));
  return seen.has(candidate.symbol) ? items : [candidate, ...items];
}

function directSymbolCandidate(query: string): CatalogSymbolInfo | null {
  const clean = query.trim().toUpperCase().replace(/[^A-Z0-9]/g, "");
  if (clean.length < 2) return null;
  const symbol = clean.endsWith("USDT") ? clean : `${clean}USDT`;
  if (symbol.length < 6 || symbol.length > 24) return null;
  return {
    symbol,
    base_coin: symbol.replace(/USDT$/u, ""),
    quote_coin: "USDT",
    status: "direct",
    asset_class: "unknown",
    source_category: "direct",
    funding_rate_interval_hours: null,
    updated_at: new Date().toISOString()
  };
}

function ScoutQuickAnswerCard({
  symbol,
  data,
  loading,
  error,
  onRefresh,
  onOpen
}: {
  symbol: string;
  data: ScoutAnalysisResponse | null;
  loading: boolean;
  error: string;
  onRefresh: (symbol: string) => void;
  onOpen: (symbol: string) => void;
}) {
  if (!symbol && !loading && !error) return null;
  const tilt = quickTilt(data);
  return (
    <section className={`scoutQuickAnswer ${loading ? "loading" : ""}`} data-testid="scout-quick-answer" data-budget-numbers-max="6">
      <div className="scoutQuickHeader">
        <div>
          <strong>{symbol || "검색 중"}</strong>
          <span>기준 {formatQuickAsOf(data?.as_of)} · {data?.timeframe ?? "4h"}</span>
        </div>
        <div className="scoutQuickActions">
          <button className="button secondary" type="button" onClick={() => symbol && onRefresh(symbol)} disabled={!symbol || loading}>
            <RefreshCw size={14} />
            갱신
          </button>
          {SCOUT_ENTRY_TOOLS_VISIBLE ? (
            <button className="button secondary" type="button" onClick={() => symbol && onOpen(symbol)} disabled={!symbol}>
              의도 등록
            </button>
          ) : null}
          <button className="button" type="button" onClick={() => symbol && onOpen(symbol)} disabled={!symbol}>
            자세히
          </button>
        </div>
      </div>

      {error ? <div className="scoutQuickError">{error}</div> : null}
      {loading && !data ? (
        <div className="scoutQuickSkeleton">
          <span />
          <span />
          <span />
        </div>
      ) : null}
      {data ? (
        <>
          <CompactChartWorkspace
            analysis={data.analysis}
            loading={loading}
            error={error}
            onRetry={() => onRefresh(symbol)}
            trendSummary={tilt.label}
            plan={null}
            gauges={data.gauges ?? null}
            nextPrice={scoutNextPrice(data.analysis, "조건 도달 시 구조 재확인")}
          />
        </>
      ) : null}
    </section>
  );
}

function ViewModeToggle({ mode, onChange }: { mode: FceViewMode; onChange: (mode: FceViewMode) => void }) {
  return (
    <div className="viewModeToggle" role="group" aria-label="화면 모드">
      <button className={mode === "minimal" ? "active" : ""} onClick={() => onChange("minimal")} type="button">
        미니멀
      </button>
      <button className={mode === "pro" ? "active" : ""} onClick={() => onChange("pro")} type="button">
        프로
      </button>
    </div>
  );
}

function ScoutMinimalTable({
  rows,
  armedSetups,
  entryIntents,
  onOpen,
  onRemove
}: {
  rows: ScoutScanRow[];
  armedSetups: ArmedSetup[];
  entryIntents: EntryIntent[];
  onOpen: (symbol: string) => void;
  onRemove: (symbol: string) => void;
}) {
  return (
    <table className="scoutTable scoutMinimalTable" data-budget-columns-max="4" data-testid="scout-minimal-table">
      <thead>
        <tr>
          <th>심볼</th>
          <th>기울기</th>
          <th>근거</th>
          <th>트리거까지</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <ScoutMinimalRow
            key={row.symbol}
            row={row}
            armedCount={armedSetups.filter((setup) => setup.symbol === row.symbol && setup.status === "armed").length}
            intentCount={entryIntents.filter((intent) => intent.symbol === row.symbol && intent.status === "active").length}
            onOpen={() => onOpen(row.symbol)}
            onRemove={() => onRemove(row.symbol)}
          />
        ))}
      </tbody>
    </table>
  );
}

function ScoutMinimalRow({
  row,
  armedCount,
  intentCount,
  onOpen,
  onRemove
}: {
  row: ScoutScanRow;
  armedCount: number;
  intentCount: number;
  onOpen: () => void;
  onRemove: () => void;
}) {
  const tilt = scoutTilt(row);
  const reasons = scoutMinimalReasons(row);
  if (row.error) {
    return (
      <tr className="scoutRow error">
        <td><strong>{row.symbol}</strong></td>
        <td colSpan={2}>스캔 실패: {row.error}</td>
        <td>
          <button className="iconButton secondary" onClick={onRemove} type="button" aria-label="삭제"><Trash2 size={13} /></button>
        </td>
      </tr>
    );
  }
  return (
    <tr className="scoutRow scoutMinimalRow" data-testid="scout-row" onClick={onOpen}>
      <td>
        <strong>{row.symbol}</strong>
        <span className="scoutSymbolMeta">
          <AssetClassBadge assetClass={row.asset_class} />
          {intentCount ? <em>의도 {intentCount}</em> : armedCount ? <em>무장 {armedCount}</em> : null}
        </span>
      </td>
      <td>
        <div className={`tiltGauge ${tilt.tone}`}>
          <span>숏</span>
          <i>
            <b style={{ left: `${tilt.position}%` }} />
          </i>
          <span>롱</span>
        </div>
        <small>{tilt.label}</small>
      </td>
      <td>
        <div className="minimalReasonBadges">
          {reasons.map((reason) => <span key={reason}>{reason}</span>)}
        </div>
      </td>
      <td>
        <strong>{formatTriggerDistance(row)}</strong>
        <button className="iconButton secondary" onClick={(event) => { event.stopPropagation(); onRemove(); }} type="button" aria-label="삭제"><Trash2 size={13} /></button>
      </td>
    </tr>
  );
}

function ScanRow({
  row,
  armedSetups,
  entryIntents,
  scanReference,
  onOpen,
  onRemove,
  onDisarm,
  onCancelIntent
}: {
  row: ScoutScanRow;
  armedSetups: ArmedSetup[];
  entryIntents: EntryIntent[];
  scanReference: string;
  onOpen: () => void;
  onRemove: () => void;
  onDisarm: (setupId: string) => void;
  onCancelIntent: (intentId: string) => void;
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
        {/* 헤더 16열 = 심볼+클래스+무장 + 정렬 9 + 와이코프/거래량/기준 + 액션 → 나머지 13열 병합 */}
        <td colSpan={SORT_COLUMNS.length + 4}>스캔 실패: {row.error}</td>
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
        {armedSetups.length || entryIntents.length ? (
          <div className="scoutArmedStack">
            {entryIntents.slice(0, 1).map((intent) => (
              <div className="scoutArmedCell intent" key={intent.id}>
                <span title={`${directionLabel(intent.direction)} ${formatPrice(intent.zone_lower)}-${formatPrice(intent.zone_upper)}`}>
                  📍 {directionLabel(intent.direction)} 존 · {formatPct(row.entry_intent_distance_pct ?? intentDistanceFromMark(intent, row.mark_price))}
                </span>
                <button
                  type="button"
                  onClick={(event) => {
                    event.stopPropagation();
                    onCancelIntent(intent.id);
                  }}
                >
                  해제
                </button>
              </div>
            ))}
            {armedSetups.slice(0, 1).map((setup) => (
              <div className="scoutArmedCell" key={setup.id}>
                <span title={setup.basis}>🎯 {setup.trigger_label} · {formatPct(setup.distance_pct)}</span>
                <button
                  type="button"
                  onClick={(event) => {
                    event.stopPropagation();
                    onDisarm(setup.id);
                  }}
                >
                  해제
                </button>
              </div>
            ))}
          </div>
        ) : row.setup_candidates?.length ? (
          <span className="scoutSetupMuted">후보 {row.setup_candidates.length}</span>
        ) : (
          <span className="scoutSetupMuted">-</span>
        )}
      </td>
      <td>{formatPct(row.setup_proximity_pct)}</td>
      <td className={scoreTone(row.long_score)}>{formatDirectionScore(row.long_score, row.long_evidence_count)}</td>
      <td className={scoreTone(row.short_score)}>{formatDirectionScore(row.short_score, row.short_evidence_count)}</td>
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

function UniverseDiscoveryPanel({
  discoveries,
  assetFilter,
  scanning,
  onOpen,
  onScan
}: {
  discoveries: UniverseDiscovery[];
  assetFilter: AssetFilter;
  scanning: boolean;
  onOpen: (symbol: string) => void;
  onScan: () => void;
}) {
  const latest = useMemo(() => visibleUniverseDiscoveries(discoveries, assetFilter).slice(0, 6), [assetFilter, discoveries]);
  return (
    <section className="universeDiscoveryPanel" data-testid="universe-discoveries">
      <div className="universeDiscoveryHeader">
        <div>
          <p className="eyebrow">유니버스 발견</p>
          <h2>게이트 통과 시그니처</h2>
        </div>
        <button className="button secondary" type="button" onClick={onScan} disabled={scanning}>
          <Radar size={14} />
          유니버스 스캔
        </button>
      </div>
      {latest.length ? (
        <div className="universeDiscoveryGrid">
          {latest.map((item) => (
            <button className={`universeDiscoveryCard ${item.status}`} key={item.id} type="button" onClick={() => onOpen(item.symbol)}>
              <span className="universeDiscoveryTopline">
                <strong>{item.symbol}</strong>
                <AssetClassBadge assetClass={item.asset_class} />
                <em>{discoveryStatusLabel(item)}</em>
              </span>
              <span className="universeDiscoverySignal">{String(item.signature.label ?? item.signature.event_type ?? "시그니처")}</span>
              <span className="universeDiscoveryStats">
                신뢰도 {item.confidence ?? "-"} · N {item.sample_size ?? "-"} · 1R {formatWinCi(item.win_1r_pct, item.win_1r_ci)}
              </span>
              <span className="universeDiscoveryReasons">{gateReasonSummary(item.gate_reasons)}</span>
            </button>
          ))}
        </div>
      ) : (
        <div className="universeDiscoveryEmpty">현재 필터에서 게이트를 통과한 발견이 없습니다. 차단 기록은 저장되지만 메인 화면에는 반복 노출하지 않습니다.</div>
      )}
    </section>
  );
}

function ScoutSymbolView({
  symbol,
  viewMode,
  onViewModeChange,
  onBack
}: {
  symbol: string;
  viewMode: FceViewMode;
  onViewModeChange: (mode: FceViewMode) => void;
  onBack: () => void;
}) {
  const workspace = useAnalysisWorkspace();
  const [data, setData] = useState<ScoutAnalysisResponse | null>(null);
  const [intents, setIntents] = useState<EntryIntent[]>([]);
  const loadRequestRef = useRef("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [timeframe, setTimeframe] = useState("4h");
  const [zonePickEnabled, setZonePickEnabled] = useState(false);
  const [zoneDraft, setZoneDraft] = useState<{ lower: number | null; upper: number | null }>({ lower: null, upper: null });

  async function load(force = false) {
    setLoading(true);
    setError("");
    // 심볼/타임프레임 연속 전환 시 늦게 도착한 이전 응답이 최신 선택을 덮어쓰지 않게,
    // 마지막으로 발행한 요청만 화면에 반영한다.
    const requestKey = `${symbol}:${timeframe}:${Date.now()}`;
    loadRequestRef.current = requestKey;
    try {
      const [analysisResponse, intentResponse] = await Promise.all([
        api.scoutAnalysis(symbol, timeframe, force, true),
        SCOUT_ENTRY_TOOLS_VISIBLE ? api.entryIntents(symbol, "active") : Promise.resolve({ intents: [] as EntryIntent[] })
      ]);
      if (loadRequestRef.current !== requestKey) return;
      setData(analysisResponse);
      setIntents(intentResponse.intents);
    } catch (err) {
      if (loadRequestRef.current !== requestKey) return;
      setData(null);
      setError(err instanceof Error ? err.message : "분석 데이터를 불러오지 못했습니다.");
    } finally {
      if (loadRequestRef.current === requestKey) setLoading(false);
    }
  }

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbol, timeframe]);

  const analysis = data?.analysis ?? null;
  const scenarios = analysis?.scenarios ?? null;
  const verdict = scoutAnalysisVerdict(symbol, data);

  async function createIntent(payload: {
    direction: "long" | "short";
    zone_lower?: number | null;
    zone_upper?: number | null;
    price?: number | null;
    conditions?: string[];
    tolerance?: "tight" | "normal" | "loose";
    note?: string;
  }) {
    setError("");
    const response = await api.createEntryIntent(symbol, { ...payload, timeframe });
    setIntents((items) => [response.intent, ...items.filter((item) => item.id !== response.intent.id)]);
    setZonePickEnabled(false);
    setZoneDraft({ lower: null, upper: null });
    setNotice("진입 의도를 등록했습니다. 스카우트 워커가 존 접근과 조건 충족을 감시합니다.");
  }

  async function cancelIntent(intentId: string) {
    setError("");
    const response = await api.cancelEntryIntent(intentId);
    setIntents((items) => items.filter((item) => item.id !== response.intent.id));
    setNotice("진입 의도를 해제했습니다.");
  }

  return (
    <div className="page positionDetailPage scoutDetailPage" data-testid="scout-analysis-view">
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
          <ViewModeToggle mode={viewMode} onChange={onViewModeChange} />
        </div>
      </header>

      {error ? <TerminalWarning tone="error">{error}</TerminalWarning> : null}
      {notice ? <TerminalWarning tone="info">{notice}</TerminalWarning> : null}
      {viewMode === "pro" ? <ScoutDirectionBanner verdict={verdict} loading={loading} /> : null}

      {viewMode === "minimal" ? (
        <ScoutMinimalAnalysisView
          symbol={symbol}
          data={data}
          analysis={analysis}
          loading={loading}
          error={error}
          timeframe={timeframe}
          workspace={workspace}
          onRetry={() => void load(true)}
          onShowPro={() => onViewModeChange("pro")}
        />
      ) : (
        <SymbolAnalysisView
          chartAnalysis={analysis}
          chartLoading={loading}
          chartError={error}
          onRetryChart={() => void load(true)}
          trendSummary={verdict.label}
          plan={null}
          analystBriefing={data?.analyst_briefing ?? null}
          workspace={workspace}
          intentZoneSelector={SCOUT_ENTRY_TOOLS_VISIBLE ? {
            enabled: zonePickEnabled,
            draft: zoneDraft,
            onDraftChange: (lower, upper) => setZoneDraft({ lower, upper }),
            onComplete: (lower, upper) => {
              setZoneDraft({ lower, upper });
              setZonePickEnabled(false);
            }
          } : undefined}
          gridClassName={SCOUT_ENTRY_TOOLS_VISIBLE ? "positionDetailMain" : "scoutDetailMain"}
          sidePanel={SCOUT_ENTRY_TOOLS_VISIBLE ? (
            <div className="scoutSidePanel">
              <EntryIntentPanel
                intents={intents}
                markPrice={analysis?.mark_price ?? null}
                zoneDraft={zoneDraft}
                chartPickEnabled={zonePickEnabled}
                onToggleChartPick={() => setZonePickEnabled((enabled) => !enabled)}
                onCancel={(intentId) => void cancelIntent(intentId)}
                onCreate={(payload) => void createIntent(payload)}
              />
              <EntrySimulator symbol={symbol} markPrice={analysis?.mark_price ?? null} timeframe={timeframe} />
              <ScenarioPanel scenarios={scenarios} asOf={data?.as_of} />
            </div>
          ) : null}
          historyExtras={<HistoricalBacktestPanel context={data?.historical_backtest ?? analysis?.historical_backtest ?? null} />}
        />
      )}
    </div>
  );
}

function ScoutMinimalAnalysisView({
  symbol,
  data,
  analysis,
  loading,
  error,
  timeframe,
  workspace,
  onRetry,
  onShowPro
}: {
  symbol: string;
  data: ScoutAnalysisResponse | null;
  analysis: ScoutAnalysisResponse["analysis"] | null;
  loading: boolean;
  error: string;
  timeframe: string;
  workspace: ReturnType<typeof useAnalysisWorkspace>;
  onRetry: () => void;
  onShowPro: () => void;
}) {
  const verdict = scoutAnalysisVerdict(symbol, data);
  void workspace;
  return (
    <section className="minimalPositionWorkspace scoutMinimalWorkspace" data-testid="scout-one-question" data-budget-numbers-max="7">
      <CompactChartWorkspace
        analysis={analysis}
        loading={loading}
        error={error}
        onRetry={onRetry}
        trendSummary={verdict.label}
        plan={null}
        gauges={data?.gauges ?? null}
        nextPrice={analysis ? scoutNextPrice(analysis, verdict.trigger) : null}
      />
      <div className="compactScoutActions">
        <button className="button secondary" onClick={onRetry} type="button">재분석</button>
        <button className="button" onClick={onShowPro} type="button">자세히 →</button>
      </div>
    </section>
  );
}

function scoutNextPrice(analysis: ScoutAnalysisResponse["analysis"], detail: string): CompactNextPrice | null {
  const levels = [...analysis.price_levels.support, ...analysis.price_levels.resistance]
    .filter((level) => Number.isFinite(level.price));
  if (!levels.length) return null;
  const nearest = [...levels].sort((left, right) => Math.abs(left.price - analysis.mark_price) - Math.abs(right.price - analysis.mark_price))[0];
  return {
    label: nearest.kind === "resistance" ? "저항" : "지지",
    price: nearest.price,
    detail
  };
}

function ScoutDirectionBanner({ verdict, loading }: { verdict: ScoutAnalysisVerdict; loading: boolean }) {
  return (
    <section className={`scoutDirectionBanner tone-${verdict.tone}`} data-testid="scout-direction-banner">
      <div className="scoutDirectionMain">
        <span>스카우트 결론</span>
        <strong>{loading ? "판단 갱신 중" : verdict.label}</strong>
      </div>
      <ScoutDirectionGauge verdict={verdict} />
      <div className="scoutDirectionReasons">
        <p><b>왜</b>{verdict.why}</p>
        <p><b>반대</b>{verdict.counter}</p>
      </div>
    </section>
  );
}

function ScoutDirectionGauge({ verdict }: { verdict: ScoutAnalysisVerdict }) {
  return (
    <div className={`scoutDirectionGauge tone-${verdict.tone}`}>
      <span>숏</span>
      <i aria-hidden="true">
        <b style={{ left: `${verdict.position}%` }} />
      </i>
      <span>롱</span>
      <em>
        상방 {verdict.counts.up} · 하방 {verdict.counts.down} · 중립 {verdict.counts.neutral} · 판단불가 {verdict.counts.unknown}
      </em>
    </div>
  );
}

function EntryIntentPanel({
  intents,
  markPrice,
  zoneDraft,
  chartPickEnabled,
  onToggleChartPick,
  onCreate,
  onCancel
}: {
  intents: EntryIntent[];
  markPrice: number | null;
  zoneDraft: { lower: number | null; upper: number | null };
  chartPickEnabled: boolean;
  onToggleChartPick: () => void;
  onCreate: (payload: {
    direction: "long" | "short";
    zone_lower?: number | null;
    zone_upper?: number | null;
    price?: number | null;
    conditions?: string[];
    tolerance?: "tight" | "normal" | "loose";
    note?: string;
  }) => void;
  onCancel: (intentId: string) => void;
}) {
  const [direction, setDirection] = useState<"long" | "short">("long");
  const [zoneLower, setZoneLower] = useState("");
  const [zoneUpper, setZoneUpper] = useState("");
  const [tolerance, setTolerance] = useState<"tight" | "normal" | "loose">("normal");
  const [note, setNote] = useState("");
  const [conditions, setConditions] = useState<string[]>(["price_in_zone"]);
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState("");

  useEffect(() => {
    if (zoneDraft.lower === null || zoneDraft.upper === null) return;
    setZoneLower(formatInputPrice(zoneDraft.lower));
    setZoneUpper(formatInputPrice(zoneDraft.upper));
  }, [zoneDraft.lower, zoneDraft.upper]);

  function fillAroundMark() {
    if (!markPrice) return;
    setZoneLower((markPrice * 0.995).toPrecision(8));
    setZoneUpper((markPrice * 1.005).toPrecision(8));
  }

  function toggleCondition(condition: string) {
    setConditions((items) => {
      const next = new Set(items);
      if (next.has(condition)) next.delete(condition);
      else next.add(condition);
      next.add("price_in_zone");
      return Array.from(next);
    });
  }

  async function submit() {
    const lower = Number(zoneLower);
    const upper = Number(zoneUpper);
    setFormError("");
    if (!Number.isFinite(lower) || !Number.isFinite(upper) || lower <= 0 || upper <= 0 || lower >= upper) {
      setFormError("존 하단/상단 가격을 올바르게 입력하세요.");
      return;
    }
    setSubmitting(true);
    try {
      await onCreate({
        direction,
        zone_lower: lower,
        zone_upper: upper,
        conditions,
        tolerance,
        note
      });
      setNote("");
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "진입 의도 등록에 실패했습니다.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section className="focusPanel entryIntentPanel" data-testid="entry-intent-panel">
      <div className="focusPanelHeader">
        <div>
          <h2>진입 의도</h2>
          <p>내가 보고 싶은 가격 존을 등록하고 조건 충족 시 알림을 받습니다.</p>
        </div>
        <span>{intents.length}/3</span>
      </div>

      {intents.length ? (
        <div className="entryIntentList">
          {intents.map((intent) => (
            <div className="entryIntentItem" key={intent.id}>
              <div>
                <strong>{directionLabel(intent.direction)} · {formatPrice(intent.zone_lower)}-{formatPrice(intent.zone_upper)}</strong>
                <span>{intent.conditions.map(conditionLabel).join(" · ")} · {toleranceLabel(intent.tolerance)}</span>
              </div>
              <button type="button" onClick={() => onCancel(intent.id)}>해제</button>
            </div>
          ))}
        </div>
      ) : (
        <div className="terminalEmpty">활성 진입 의도가 없습니다.</div>
      )}

      <div className="entryIntentForm">
        <div className="simInputGrid">
          <label>
            <span>방향</span>
            <select value={direction} onChange={(event) => setDirection(event.target.value as "long" | "short")}>
              <option value="long">롱</option>
              <option value="short">숏</option>
            </select>
          </label>
          <label>
            <span>접근 민감도</span>
            <select value={tolerance} onChange={(event) => setTolerance(event.target.value as "tight" | "normal" | "loose")}>
              <option value="tight">타이트 0.5%</option>
              <option value="normal">보통 1.5%</option>
              <option value="loose">느슨함 3.0%</option>
            </select>
          </label>
          <label>
            <span>존 하단</span>
            <input value={zoneLower} onChange={(event) => setZoneLower(event.target.value)} placeholder="240" inputMode="decimal" />
          </label>
          <label>
            <span>존 상단</span>
            <input value={zoneUpper} onChange={(event) => setZoneUpper(event.target.value)} placeholder="250" inputMode="decimal" />
          </label>
        </div>
        <button className="button secondary" type="button" onClick={fillAroundMark} disabled={!markPrice}>
          <MapPin size={14} />
          현재가 ±0.5%
        </button>
        <button className={`button secondary ${chartPickEnabled ? "active" : ""}`} type="button" onClick={onToggleChartPick}>
          <MapPin size={14} />
          {chartPickEnabled ? "차트 지정 중" : "차트에서 존 지정"}
        </button>
        {chartPickEnabled ? <p className="entryIntentHint">차트 위에서 원하는 가격 범위를 위아래로 드래그하세요.</p> : null}
        <div className="entryIntentConditions">
          {["price_in_zone", "sweep_confirmed", "wyckoff_event", "volume_spike", "briefing_aligned"].map((condition) => (
            <label key={condition}>
              <input
                checked={conditions.includes(condition)}
                disabled={condition === "price_in_zone"}
                onChange={() => toggleCondition(condition)}
                type="checkbox"
              />
              <span>{conditionLabel(condition)}</span>
            </label>
          ))}
        </div>
        <label className="entryIntentNote">
          <span>메모</span>
          <input value={note} onChange={(event) => setNote(event.target.value)} placeholder="예: 실적 전 돌파 실패 확인" />
        </label>
        {formError ? <div className="actionPlanWarning">{formError}</div> : null}
        <button className="button" disabled={submitting || intents.length >= 3} onClick={() => void submit()} type="button">
          {submitting ? "등록 중" : "의도 등록"}
        </button>
      </div>
    </section>
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
          <p>포지션이 없어 롱/숏 트리거를 모두 표시합니다 · 판단 유보</p>
        </div>
        <span>{asOf ? `기준 ${new Date(asOf).toLocaleTimeString()}` : "-"}</span>
      </div>
      <ScenarioBlock title="롱 진입 시" scenario={scenarios.long} />
      <ScenarioBlock title="숏 진입 시" scenario={scenarios.short} />
    </section>
  );
}

function HistoricalBacktestPanel({ context }: { context: HistoricalBacktest | null }) {
  if (!context) {
    return <div className="terminalEmpty">과거 사례 통계를 불러오는 중입니다.</div>;
  }
  const stats = context.stats ?? [];
  return (
    <section className="historicalBacktestPanel" data-testid="historical-backtest-panel">
      <div className="focusPanelHeader compact">
        <div>
          <h3>과거 사례</h3>
          <p>{context.disclaimer}</p>
        </div>
        <span>{context.source === "cache" ? "캐시" : context.source}</span>
      </div>
      {stats.length ? (
        <div className="historicalBacktestStats">
          {stats.slice(0, 3).map((stat) => (
            <div className="historicalBacktestStat" key={stat.signature_key}>
              <div>
                <strong>{plainifyTaText(stat.label ?? "동일 시그니처")}</strong>
                <span>{stat.sample_warning ?? `N=${stat.sample_size}`}</span>
              </div>
              <div className="historicalBacktestMetrics">
                <span>1R {formatWinCi(stat.win_1r_pct, stat.win_1r_ci)}</span>
                <span>2R {formatNullablePct(stat.win_2r_pct)}</span>
                <span>중앙 {formatNullableR(stat.median_rr)}</span>
                {stat.unstable ? <span className="historicalBacktestUnstable">OOS 불안정</span> : null}
              </div>
              {stat.lifecycle_note ? <p className="historicalLifecycleNote">{stat.lifecycle_note}</p> : null}
              {stat.cases?.length ? (
                <div className="historicalCaseGrid">
                  {stat.cases.slice(0, 3).map((item, index) => (
                    <div className="historicalCaseCard" key={`${stat.signature_key}-${item.as_of}-${index}`}>
                      <MiniPricePath points={item.price_path} />
                      <span>{new Date(item.as_of).toLocaleDateString()} · {item.outcome.win_1r ? "1R 도달" : "무효화 우선"}</span>
                      <em>MFE {item.outcome.mfe_r}R · MAE {item.outcome.mae_r}R</em>
                    </div>
                  ))}
                </div>
              ) : null}
            </div>
          ))}
        </div>
      ) : (
        <div className="terminalEmpty">현재 활성 신호와 매칭되는 과거 사례가 부족합니다.</div>
      )}
      <p className="tabExplanation">{context.notes.join(" · ")}</p>
    </section>
  );
}

function MiniPricePath({ points }: { points: Array<{ close: number }> }) {
  const values = points.map((point) => point.close).filter((value) => Number.isFinite(value));
  if (values.length < 2) return <div className="miniBacktestChart empty" />;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const d = values
    .map((value, index) => {
      const x = (index / Math.max(1, values.length - 1)) * 100;
      const y = 30 - ((value - min) / span) * 28;
      return `${index === 0 ? "M" : "L"}${x.toFixed(2)},${y.toFixed(2)}`;
    })
    .join(" ");
  return (
    <svg className="miniBacktestChart" viewBox="0 0 100 32" role="img" aria-label="과거 사례 미니 차트">
      <path d={d} />
    </svg>
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

function scoutTilt(row: ScoutScanRow): { position: number; label: string; tone: "long" | "short" | "neutral" | "insufficient" } {
  const longEvidence = row.long_evidence_count ?? 0;
  const shortEvidence = row.short_evidence_count ?? 0;
  const longScore = typeof row.long_score === "number" ? row.long_score : null;
  const shortScore = typeof row.short_score === "number" ? row.short_score : null;
  if (longEvidence + shortEvidence < 3 || longScore === null || shortScore === null) {
    return { position: 50, label: "근거 부족", tone: "insufficient" };
  }
  const diff = longScore - shortScore;
  if (Math.abs(diff) < 10) return { position: 50, label: "충돌", tone: "neutral" };
  const position = Math.max(8, Math.min(92, 50 + diff / 2));
  return diff > 0
    ? { position, label: "롱 근거 우세", tone: "long" }
    : { position, label: "숏 근거 우세", tone: "short" };
}

function quickTilt(data: ScoutAnalysisResponse | null): { position: number; label: string; tone: "long" | "short" | "neutral" | "insufficient" } {
  const stance = data?.analysis?.one_liners?.overall_stance;
  if (stance === "상방") return { position: 78, label: "상방 근거 우세", tone: "long" };
  if (stance === "하방") return { position: 22, label: "하방 근거 우세", tone: "short" };
  if (stance === "횡보") return { position: 50, label: "중립", tone: "neutral" };
  if (stance === "판단불가") return { position: 50, label: "근거 부족", tone: "insufficient" };
  return data?.summary ? scoutTilt(data.summary) : { position: 50, label: "분석 대기", tone: "insufficient" };
}

function quickOneLinerRows(oneLiners: OneLinerSummary | null): Array<{ module: OneLinerLine["module"]; label: string; stance: string; phrase: string }> {
  const preferred: OneLinerLine["module"][] = ["wyckoff", "liquidity", "volume", "harmonic"];
  const fallback = [
    { module: "wyckoff", label: "와이코프", stance: "판단불가", phrase: "판정 대기" },
    { module: "liquidity", label: "유동성", stance: "판단불가", phrase: "판정 대기" },
    { module: "volume", label: "볼륨", stance: "판단불가", phrase: "판정 대기" },
    { module: "harmonic", label: "하모닉", stance: "판단불가", phrase: "패턴 없음" }
  ] satisfies Array<{ module: OneLinerLine["module"]; label: string; stance: string; phrase: string }>;
  if (!oneLiners?.lines?.length) return fallback;
  const byModule = new Map(oneLiners.lines.map((line) => [line.module, line]));
  return preferred.map((module) => {
    const line = byModule.get(module);
    if (!line) return fallback.find((item) => item.module === module)!;
    return {
      module,
      label: line.module_label,
      stance: line.stance,
      phrase: clampScoutText(plainifyTaText(line.phrase), 28)
    };
  });
}

function quickOneLinerCounts(oneLiners: OneLinerSummary | null): { up: number; down: number; neutral: number; unknown: number } {
  return {
    up: oneLiners?.counts?.["상방"] ?? 0,
    down: oneLiners?.counts?.["하방"] ?? 0,
    neutral: oneLiners?.counts?.["횡보"] ?? 0,
    unknown: oneLiners?.counts?.["판단불가"] ?? (oneLiners ? 0 : 4)
  };
}

function formatQuickAsOf(value: string | undefined): string {
  if (!value) return "분석 중";
  const diff = Date.now() - new Date(value).getTime();
  if (!Number.isFinite(diff) || diff < 0) return "방금";
  const minutes = Math.floor(diff / 60000);
  if (minutes < 1) return "방금";
  if (minutes < 60) return `${minutes}분 전`;
  return new Date(value).toLocaleTimeString();
}

function scoutMinimalReasons(row: ScoutScanRow): string[] {
  const reasons = [
    row.top_event?.label ? taShortLabel(row.top_event.label) : "",
    row.liquidity_nearest_pool?.label ? plainifyTaText(row.liquidity_nearest_pool.label) : "",
    row.harmonic_active ? "반전 후보 구간" : "",
    row.funding_state ? `펀딩 ${plainifyTaText(row.funding_state)}` : "",
    row.wyckoff_phase ? phaseHintLabel(row.wyckoff_phase) : "",
    row.volume_state ? volumeStateLabel(row.volume_state) : ""
  ]
    .map((item) => item.trim())
    .filter(Boolean);
  return Array.from(new Set(reasons)).slice(0, 2).map((item) => clampScoutText(item, 24));
}

function formatTriggerDistance(row: ScoutScanRow): string {
  if (SCOUT_ENTRY_TOOLS_VISIBLE && typeof row.entry_intent_distance_pct === "number") return `의도 ${formatPct(row.entry_intent_distance_pct)}`;
  if (typeof row.setup_proximity_pct === "number") return formatPct(row.setup_proximity_pct);
  if (typeof row.liquidity_pool_distance_pct === "number") return `유동성 ${formatPct(row.liquidity_pool_distance_pct)}`;
  if (typeof row.nearest_level_distance_pct === "number") return `구조 ${formatPct(row.nearest_level_distance_pct)}`;
  return "대기";
}

function scoutAnalysisVerdict(symbol: string, data: ScoutAnalysisResponse | null): ScoutAnalysisVerdict {
  const confluence = data?.analyst_briefing?.confluence;
  const summary = data?.summary;
  const tilt = summary ? scoutTilt(summary) : { position: 50, label: "근거 부족", tone: "insufficient" as const };
  const tone = confluence ? scoutToneFromStance(confluence.stance) : tilt.tone;
  const position = confluence ? scoutPositionFromConfluence(confluence, tone) : tilt.position;
  const label = confluence?.stance_label
    ? scoutStanceLabel(confluence.stance, confluence.stance_label)
    : tilt.label;
  const primary = confluence ? scoutPrimaryEvidence(confluence, tone) : undefined;
  const primaryEvidence = primary?.[0];
  const why = primary?.slice(0, 2).map((item) => item.claim).join(". ")
    || (summary ? scoutMinimalReasons(summary).join(" · ") : "")
    || `${symbol}은 아직 방향 근거가 충분하지 않습니다.`;
  const counter = confluence?.counter_evidence?.[0]?.claim || "반대 근거는 아직 강하게 확인되지 않았습니다.";
  return {
    tone,
    label,
    why: clampScoutText(plainifyTaText(why), 104),
    counter: clampScoutText(plainifyTaText(counter), 82),
    trigger: summary ? formatTriggerDistance(summary) : "대기",
    position,
    counts: scoutVerdictCounts(data, confluence),
    evidence: scoutEvidenceFromClaim(primaryEvidence?.engine, primaryEvidence?.claim || why, primaryEvidence?.as_of)
  };
}

function scoutToneFromStance(stance: string): ScoutVerdictTone {
  if (stance === "long_leaning") return "long";
  if (stance === "short_leaning") return "short";
  if (stance === "conflicted") return "conflicted";
  if (stance === "insufficient") return "insufficient";
  return "neutral";
}

function scoutPositionFromConfluence(confluence: AnalystConfluence, tone: ScoutVerdictTone): number {
  if (tone === "insufficient") return 50;
  const diff = confluence.long_score - confluence.short_score;
  if (tone === "conflicted" || Math.abs(diff) < 1) return 50;
  return Math.max(8, Math.min(92, 50 + diff / 2));
}

function scoutPrimaryEvidence(
  confluence: AnalystConfluence,
  tone: ScoutVerdictTone
) {
  if (tone === "short") return confluence.short_evidence;
  if (tone === "long") return confluence.long_evidence;
  const longTop = confluence.long_evidence[0];
  const shortTop = confluence.short_evidence[0];
  if (!longTop) return confluence.short_evidence;
  if (!shortTop) return confluence.long_evidence;
  return confluence.long_score >= confluence.short_score ? confluence.long_evidence : confluence.short_evidence;
}

function scoutVerdictCounts(
  data: ScoutAnalysisResponse | null,
  confluence: AnalystConfluence | undefined
): ScoutAnalysisVerdict["counts"] {
  const counts = data?.analysis?.one_liners?.counts;
  if (counts) {
    return {
      up: counts["상방"] ?? 0,
      down: counts["하방"] ?? 0,
      neutral: counts["횡보"] ?? 0,
      unknown: counts["판단불가"] ?? 0
    };
  }
  return {
    up: confluence?.long_evidence.length ?? 0,
    down: confluence?.short_evidence.length ?? 0,
    neutral: confluence?.neutral_evidence?.length ?? 0,
    unknown: confluence ? Math.max(0, confluence.evidence_count - confluence.long_evidence.length - confluence.short_evidence.length - (confluence.neutral_evidence?.length ?? 0)) : 0
  };
}

function scoutEvidenceFromClaim(engine: string | undefined, claim: string, asOf?: string | null): ScoutMinimalEvidence {
  const text = plainifyTaText(claim);
  return {
    key: `${engine ?? "summary"}:${text}`,
    text,
    layer: scoutEvidenceLayer(engine, text),
    label: text.split(/[.·]/)[0]?.trim() || "스카우트 근거",
    price: scoutEvidencePrice(text),
    time: asOf ? Math.floor(new Date(asOf).getTime() / 1000) : null
  };
}

function scoutEvidenceLayer(engine: string | undefined, claim: string): MinimalEvidenceLayer {
  const value = `${engine ?? ""} ${claim}`.toLowerCase();
  if (value.includes("wyckoff") || value.includes("spring") || value.includes("utad") || value.includes("와이코프")) return "wyckoff";
  if (value.includes("liquidity") || value.includes("sweep") || value.includes("스윕") || value.includes("유동성")) return "liquidity";
  if (value.includes("harmonic") || value.includes("prz") || value.includes("하모닉") || value.includes("반전 후보")) return "harmonic";
  if (value.includes("flow") || value.includes("funding") || value.includes("oi") || value.includes("체결") || value.includes("수급") || value.includes("펀딩")) return "flow";
  if (value.includes("level") || value.includes("support") || value.includes("resistance") || value.includes("지지") || value.includes("저항")) return "levels";
  return "plan";
}

function scoutEvidencePrice(claim: string): number | null {
  const match = claim.match(/(?:\d{1,3}(?:,\d{3})+|\d+\.\d+|\d{4,})/);
  if (!match) return null;
  const parsed = Number(match[0].replace(/,/g, ""));
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
}

function scoutStanceLabel(stance: string, fallback: string): string {
  if (stance === "long_leaning") return "롱 근거 우세";
  if (stance === "short_leaning") return "숏 근거 우세";
  if (stance === "conflicted") return "근거 충돌";
  if (stance === "insufficient") return "근거 부족";
  return plainifyTaText(fallback);
}

function clampScoutText(value: string, maxLength: number): string {
  const normalized = value.replace(/\s+/g, " ").trim();
  if (normalized.length <= maxLength) return normalized;
  return `${normalized.slice(0, maxLength - 1).trim()}…`;
}

function directionLabel(direction: "long" | "short" | string): string {
  return direction === "short" ? "숏" : "롱";
}

function toleranceLabel(tolerance: "tight" | "normal" | "loose" | string): string {
  if (tolerance === "tight") return "타이트";
  if (tolerance === "loose") return "느슨함";
  return "보통";
}

function conditionLabel(condition: string): string {
  const labels: Record<string, string> = {
    price_in_zone: "가격 존 진입",
    sweep_confirmed: "스윕 확인",
    wyckoff_event: "와이코프 이벤트",
    volume_spike: "거래량 급증",
    briefing_aligned: "브리핑 방향 일치"
  };
  return labels[condition] ?? condition;
}

function intentDistanceFromMark(intent: EntryIntent, markPrice: number | null | undefined): number | null {
  if (typeof markPrice !== "number" || markPrice <= 0) return null;
  if (intent.zone_lower <= markPrice && markPrice <= intent.zone_upper) return 0;
  const target = markPrice < intent.zone_lower ? intent.zone_lower : intent.zone_upper;
  return Math.abs(((target - markPrice) / markPrice) * 100);
}

function formatPct(value: number | null | undefined): string {
  if (typeof value !== "number") return "-";
  return `${value.toFixed(2)}%`;
}

function formatNullablePct(value: number | null | undefined): string {
  if (typeof value !== "number") return "-";
  return `${value.toFixed(1)}%`;
}

// WO-36 §7: CI 없는 승률 표기 금지 — CI가 없으면 결론 유보로 낮춰 표기한다.
function formatWinCi(pct: number | null | undefined, ci: [number, number] | null | undefined): string {
  if (typeof pct !== "number") return "-";
  if (!Array.isArray(ci) || ci.length !== 2) return "표본 부족";
  return `${pct.toFixed(0)}% (CI ${ci[0].toFixed(0)}~${ci[1].toFixed(0)}%)`;
}

function formatNullableR(value: number | null | undefined): string {
  if (typeof value !== "number") return "-";
  return `${value.toFixed(2)}R`;
}

function formatInputPrice(value: number): string {
  if (!Number.isFinite(value)) return "";
  if (Math.abs(value) >= 1000) return value.toFixed(2);
  if (Math.abs(value) >= 1) return value.toPrecision(8).replace(/\.?0+$/, "");
  return value.toPrecision(8).replace(/\.?0+$/, "");
}

function scoreTone(score: number | null | undefined): string {
  if (typeof score !== "number") return "";
  if (score >= 70) return "successText";
  if (score <= 35) return "dangerText";
  return "";
}

function formatDirectionScore(score: number | null | undefined, evidenceCount: number | null | undefined): string {
  if (typeof evidenceCount === "number" && evidenceCount < 3) return "표본 부족";
  return typeof score === "number" ? String(score) : "표본 부족";
}

function formatFunding(value: number | null | undefined): string {
  if (typeof value !== "number") return "-";
  return `${value > 0 ? "+" : ""}${(value * 100).toFixed(4)}%`;
}

function discoveryStatusLabel(item: UniverseDiscovery): string {
  if (item.status === "alerted") return "알림 발송";
  if (item.status === "stored") return "탭 적재";
  return "게이트 차단";
}

function mergeUniverseDiscoveries(next: UniverseDiscovery[], current: UniverseDiscovery[]): UniverseDiscovery[] {
  const seen = new Set<string>();
  const merged: UniverseDiscovery[] = [];
  for (const item of [...next, ...current]) {
    const key = item.id || `${item.symbol}:${item.signature_key}:${item.timeframe}`;
    if (seen.has(key)) continue;
    seen.add(key);
    merged.push(item);
  }
  return merged;
}

function visibleUniverseDiscoveries(discoveries: UniverseDiscovery[], assetFilter: AssetFilter): UniverseDiscovery[] {
  const seen = new Set<string>();
  const visible: UniverseDiscovery[] = [];
  for (const item of discoveries) {
    if (!item.gate_passed || item.status === "rejected") continue;
    if (!assetMatchesFilter(item.asset_class, assetFilter)) continue;
    const key = `${item.symbol}:${item.signature_key}:${item.timeframe}`;
    if (seen.has(key)) continue;
    seen.add(key);
    visible.push(item);
  }
  return visible;
}

function gateReasonSummary(reasons: UniverseDiscovery["gate_reasons"]): string {
  const failed = reasons.filter((reason) => !reason.passed);
  if (!failed.length) return "게이트 통과";
  return failed.slice(0, 2).map((reason) => gateReasonLabel(reason.code)).join(" · ");
}

function gateReasonLabel(code: string): string {
  const labels: Record<string, string> = {
    confidence: "신뢰도 부족",
    backtest_sample: "백테스트 N 부족",
    backtest_win_1r: "1R 기준 미달",
    backtest_win_1r_ci_low: "1R CI 하한 미달",
    live_backtest_divergence: "라이브 괴리",
    liquidity_floor: "거래대금 부족",
    earnings_window: "실적 구간",
    daily_alert_limit: "일 상한",
    symbol_cooldown: "쿨다운"
  };
  return labels[code] ?? code;
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
