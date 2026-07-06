"use client";

import { useEffect, useMemo, useState } from "react";
import { MapPin, Radar, RefreshCw, Search, Star, Trash2 } from "lucide-react";
import { TerminalWarning } from "@/components/terminal";
import { SymbolAnalysisView, useAnalysisWorkspace } from "@/components/symbol-analysis-view";
import { EntrySimulator } from "@/components/entry-simulator";
import {
  api,
  type ArmedSetup,
  type CatalogSymbolInfo,
  type EntryIntent,
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
  const [entryIntents, setEntryIntents] = useState<EntryIntent[]>([]);
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
      const intents = await api.entryIntents(undefined, "active");
      setEntryIntents(intents.intents);
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
      setEntryIntents((current) => response.entry_intents ?? current);
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
                    entryIntents={entryIntents.filter((intent) => intent.symbol === row.symbol && intent.status === "active")}
                    scanReference={scannedAt}
                    onOpen={() => setActiveSymbol(row.symbol)}
                    onRemove={() => void removeSymbol(row.symbol)}
                    onDisarm={disarmSetup}
                    onCancelIntent={cancelIntent}
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
  const [intents, setIntents] = useState<EntryIntent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [timeframe, setTimeframe] = useState("4h");
  const [zonePickEnabled, setZonePickEnabled] = useState(false);
  const [zoneDraft, setZoneDraft] = useState<{ lower: number | null; upper: number | null }>({ lower: null, upper: null });

  async function load(force = false) {
    setLoading(true);
    setError("");
    try {
      const [analysisResponse, intentResponse] = await Promise.all([
        api.scoutAnalysis(symbol, timeframe, force),
        api.entryIntents(symbol, "active")
      ]);
      setData(analysisResponse);
      setIntents(intentResponse.intents);
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
      {notice ? <TerminalWarning tone="info">{notice}</TerminalWarning> : null}

      <SymbolAnalysisView
        chartAnalysis={analysis}
        chartLoading={loading}
        chartError={error}
        onRetryChart={() => void load(true)}
        trendSummary={analysis ? trendLabel((analysis.wyckoff as { trend?: { direction?: string } })?.trend?.direction) : "구조 확인 중"}
        plan={null}
        analystBriefing={data?.analyst_briefing ?? null}
        workspace={workspace}
        intentZoneSelector={{
          enabled: zonePickEnabled,
          draft: zoneDraft,
          onDraftChange: (lower, upper) => setZoneDraft({ lower, upper }),
          onComplete: (lower, upper) => {
            setZoneDraft({ lower, upper });
            setZonePickEnabled(false);
          }
        }}
        gridClassName="positionDetailMain"
        sidePanel={
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
        }
      />
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
