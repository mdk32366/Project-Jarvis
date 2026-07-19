import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api.js";

// Exception-first status page (health TDD §8.2). Surfaces what's WRONG; healthy
// state collapses to one line. `unknown` is its own visual state — never quietly
// green. Polls /api/status/full every 30s and shows if a poll went stale.

const ORDER = { down: 0, degraded: 1, unknown: 2, ok: 3 };

function fmt(ts) {
  if (!ts) return "—";
  try { return new Date(ts).toLocaleString(); } catch { return ts; }
}

function CheckCard({ c }) {
  return (
    <li className={`status-item status-${c.status}`}>
      <div className="row space">
        <strong>{c.component}</strong>
        <span className={`status-badge status-${c.status}`}>{c.status}</span>
      </div>
      {c.detail && <div className="status-detail">{c.detail}</div>}
      {c.remediation ? (
        <div className="runbook">
          <span className="runbook-label">Runbook · {c.remediation.severity}</span>
          <pre>{c.remediation.runbook}</pre>
        </div>
      ) : (
        c.status !== "ok" && c.status !== "unknown" && (
          <div className="hint">No stored runbook for this fault — check the logs.</div>
        )
      )}
      {c.evidence?.length > 0 && (
        <div className="evidence">
          <span className="hint">Recent failing calls:</span>
          <ul>
            {c.evidence.map((e, i) => (
              <li key={i}>
                <code>{e.tool}</code> — <span className="tag down">{e.status}</span>{" "}
                <span className="hint">{e.detail}</span>{" "}
                <span className="hint">({fmt(e.at)})</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </li>
  );
}

export default function DashboardPage() {
  const me = useQuery({ queryKey: ["me"], queryFn: () => api.me() });
  const status = useQuery({
    queryKey: ["status-full"],
    queryFn: () => api.get("/status/full"),
    refetchInterval: 30000,
  });
  const [showOk, setShowOk] = useState(false);

  const data = status.data;
  const checks = data?.checks ?? [];
  const notOk = checks
    .filter((c) => c.status !== "ok")
    .sort((a, b) => ORDER[a.status] - ORDER[b.status]);
  const ok = checks.filter((c) => c.status === "ok");
  // A poll that failed while we still hold prior data: the page is frozen, so say
  // so rather than showing confident-but-stale state.
  const stale = status.isError && !!data;

  return (
    <div className="status-page">
      <div className="row space">
        <h1>Status</h1>
        <span className="hint">
          Signed in as <strong>{me.data?.username ?? "…"}</strong>
        </span>
      </div>

      {status.isLoading && <p>Checking…</p>}
      {status.isError && !data && <p className="error">Could not reach the status endpoint.</p>}

      {data && (
        <>
          {stale && (
            <div className="stale-banner">
              ⚠ Last poll failed — showing state from {fmt(data.generated_at)}. Retrying…
            </div>
          )}
          <p className="hint">
            Updated {fmt(data.generated_at)} · {data.summary.down} down · {data.summary.degraded} degraded
            · {data.summary.unknown} unknown · {data.summary.ok} ok
          </p>

          {notOk.length === 0 ? (
            <div className="card all-clear">✓ All {ok.length} checks OK.</div>
          ) : (
            <ul className="status-list">
              {notOk.map((c) => <CheckCard key={c.component} c={c} />)}
            </ul>
          )}

          {ok.length > 0 && (
            <div className="card">
              <button className="link-btn" onClick={() => setShowOk((v) => !v)}>
                {showOk ? "▾" : "▸"} {ok.length} checks OK
              </button>
              {showOk && (
                <ul className="ok-list">
                  {ok.map((c) => (
                    <li key={c.component}>
                      <strong>{c.component}</strong> <span className="hint">{c.detail}</span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}
