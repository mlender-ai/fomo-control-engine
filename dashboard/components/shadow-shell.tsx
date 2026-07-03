"use client";

import { Play } from "lucide-react";
import { useEffect, useState } from "react";
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
      setProfile(result.shadow_profiles[0] ?? null);
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

  return (
    <div className="page">
      <header className="pageHeader">
        <div>
          <p className="eyebrow">Shadow Journal</p>
          <h1>Shadow Account</h1>
          <p className="subtle">완료 거래에서 좋은 거래의 공통점과 FOMO 손실 기여도를 추출합니다.</p>
        </div>
        <button className="button" onClick={extract} disabled={loading}>
          <Play size={16} />
          Extract Shadow Profile
        </button>
      </header>
      {error ? <div className="panel dangerText">{error}</div> : null}
      {profile ? (
        <>
          <section className="grid three">
            <div className="panel"><span className="subtle">Total</span><h2>{profile.total_trades}</h2></div>
            <div className="panel"><span className="subtle">Profitable</span><h2>{profile.profitable_trades}</h2></div>
            <div className="panel"><span className="subtle">Losing</span><h2>{profile.losing_trades}</h2></div>
          </section>
          <section className="panel">
            <h2>Profile</h2>
            <p className="reportText">{profile.profile_text}</p>
          </section>
          <section className="grid two">
            <div className="panel">
              <h2>Rules</h2>
              <pre className="reportText">{JSON.stringify(profile.rules, null, 2)}</pre>
            </div>
            <div className="panel">
              <h2>Attribution</h2>
              <pre className="reportText">{JSON.stringify(profile.attribution, null, 2)}</pre>
            </div>
          </section>
          <section className="panel">
            <div className="panelHeader">
              <h2>Profile History</h2>
            </div>
            {profiles.length ? (
              <table className="table">
                <thead>
                  <tr>
                    <th>ID</th>
                    <th>Total</th>
                    <th>Profitable</th>
                    <th>Created</th>
                  </tr>
                </thead>
                <tbody>
                  {profiles.map((item) => (
                    <tr key={item.shadow_id} onClick={() => setProfile(item)}>
                      <td>{item.shadow_id}</td>
                      <td>{item.total_trades}</td>
                      <td>{item.profitable_trades}</td>
                      <td>{new Date(item.created_at).toLocaleString()}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <div className="empty">No saved shadow profiles</div>
            )}
          </section>
        </>
      ) : (
        <div className="empty">No shadow profile yet. Minimum sample rules apply.</div>
      )}
    </div>
  );
}
