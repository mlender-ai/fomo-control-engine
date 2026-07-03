"use client";

import { Play } from "lucide-react";
import { useEffect, useState } from "react";
import { TerminalMetric, TerminalPanel, TerminalRawJson, TerminalTable, TerminalWarning } from "@/components/terminal";
import { api, type ShadowProfile } from "@/lib/api";

export function ShadowShell() {
  const [profile, setProfile] = useState<ShadowProfile | null>(null);
  const [profiles, setProfiles] = useState<ShadowProfile[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function load() {
    setError("");
    try {
      const result = await api.shadowProfiles();
      setProfiles(result.shadow_profiles);
      setProfile((current) => current ?? result.shadow_profiles[0] ?? null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load shadow profiles");
    }
  }

  useEffect(() => {
    void load();
  }, []);

  async function extract() {
    setLoading(true);
    setError("");
    try {
      const nextProfile = await api.extractShadow();
      setProfile(nextProfile);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to extract shadow profile");
    } finally {
      setLoading(false);
    }
  }

  const winRate = profile?.total_trades ? (profile.profitable_trades / profile.total_trades) * 100 : 0;

  return (
    <div className="page">
      <header className="pageHeader">
        <div>
          <p className="eyebrow">Shadow Journal</p>
          <h1>Shadow Account</h1>
          <p className="subtle">완료 거래에서 좋은 거래의 공통점과 FOMO 거래의 손실 기여도를 추출합니다.</p>
        </div>
        <button className="button" onClick={extract} disabled={loading}>
          <Play size={16} />
          {loading ? "Extracting" : "Extract Shadow Profile"}
        </button>
      </header>

      {error ? <TerminalWarning tone="error">{error}</TerminalWarning> : null}

      {profile ? (
        <>
          <section className="grid four">
            <TerminalMetric label="Total Trades" value={profile.total_trades} tone="info" />
            <TerminalMetric label="Profitable" value={profile.profitable_trades} tone="positive" />
            <TerminalMetric label="Losing" value={profile.losing_trades} tone="negative" />
            <TerminalMetric label="Win Rate" value={`${winRate.toFixed(1)}%`} tone={winRate >= 50 ? "positive" : "warning"} />
          </section>

          <section className="grid two">
            <TerminalPanel title="Shadow Profile" subtitle={`Profile ${profile.shadow_id}`} status="accent">
              <p className="reportText">{profile.profile_text}</p>
            </TerminalPanel>
            <TerminalPanel title="Attribution" subtitle="Behavior contribution summary" status="warning">
              <TerminalRawJson data={profile.attribution} label="attribution" />
            </TerminalPanel>
          </section>

          <section className="grid two">
            <TerminalPanel title="Winning Rules" subtitle="Common structure of profitable trades" status="ok">
              <TerminalRawJson data={profile.rules} label="rules" />
            </TerminalPanel>
            <TerminalPanel title="FOMO Patterns" subtitle="Noise trade and overtrading markers" status="warning">
              <TerminalRawJson data={{ fomo_patterns: profile.fomo_patterns, common_mistakes: profile.common_mistakes }} label="patterns" />
            </TerminalPanel>
          </section>

          <TerminalPanel title="Profile History" subtitle="Click a profile id to inspect it" status={profiles.length ? "ok" : "neutral"}>
            <TerminalTable<ShadowProfile>
              data={profiles}
              idKey="shadow_id"
              emptyLabel="No saved shadow profiles"
              columns={[
                {
                  key: "shadow_id",
                  header: "ID",
                  render: (item) => (
                    <button className="terminalDisclosure" type="button" onClick={() => setProfile(item)}>
                      {item.shadow_id}
                    </button>
                  )
                },
                { key: "total_trades", header: "Total", align: "end", render: (item) => item.total_trades },
                { key: "profitable_trades", header: "Profitable", align: "end", render: (item) => item.profitable_trades },
                { key: "losing_trades", header: "Losing", align: "end", render: (item) => item.losing_trades },
                { key: "created_at", header: "Created", render: (item) => new Date(item.created_at).toLocaleString() }
              ]}
            />
          </TerminalPanel>
        </>
      ) : (
        <TerminalWarning tone="info">No shadow profile yet. Minimum sample rules apply before extraction succeeds.</TerminalWarning>
      )}
    </div>
  );
}
