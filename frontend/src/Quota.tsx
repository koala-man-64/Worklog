import { useEffect, useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ApiError, getDataContext, renewSession, request } from "./api";
import { commands } from "./commands";
import { ErrorMessage } from "./components";
import { useCommand, useData } from "./hooks";
import { localStore } from "./storage";
import type { ChatOverview, List } from "./types";
import type {
  QuotaCard,
  QuotaCreditPlanReport,
  QuotaDashboard,
  QuotaPlanModelOption,
  QuotaRecentText,
} from "./quotaTypes";

const PAUSED_NOTE =
  "Live updates paused while Worklog reconnects; these figures may be out of date.";

function fmtCompact(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const abs = Math.abs(value);
  if (abs >= 1_000_000_000) return `${(value / 1_000_000_000).toFixed(2)}B`;
  if (abs >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (abs >= 1_000) return `${Math.round(value / 1_000)}K`;
  return String(Math.round(value));
}

function fmtDuration(minutes: number): string {
  const total = Math.max(0, Math.round(minutes));
  const days = Math.floor(total / 1440);
  const rem = total % 1440;
  const hours = Math.floor(rem / 60);
  const mins = rem % 60;
  if (days > 0) return `${days}d ${hours}h`;
  if (hours > 0) return `${hours}h ${String(mins).padStart(2, "0")}m`;
  return `${mins}m`;
}

function toLocalInputValue(date: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` +
    `T${pad(date.getHours())}:${pad(date.getMinutes())}`
  );
}

function computeTargetOverride(
  card: QuotaCard,
  targetDate: Date | null,
  now: Date,
  hasActiveCreditPlan: boolean,
) {
  if (
    !targetDate ||
    hasActiveCreditPlan ||
    card.status === "idle" ||
    card.status === "stale" ||
    !card.start
  ) {
    return null;
  }
  const start = new Date(card.start);
  const msLeft = targetDate.getTime() - now.getTime();
  if (Number.isNaN(start.getTime()) || msLeft <= 0) return null;

  const totalTargetMs = targetDate.getTime() - start.getTime();
  const elapsedMs = now.getTime() - start.getTime();
  const targetPace =
    totalTargetMs > 0
      ? 100.0 * Math.min(1.0, Math.max(0.0, elapsedMs / totalTargetMs))
      : 100.0;
  const delta = card.used - targetPace;

  let status = "on-pace";
  if (card.used >= 100) status = "exhausted";
  else if (delta > 2.0) status = "over";
  else if (delta < -2.0) status = "under";

  let badge = "on target pace";
  if (status === "exhausted") badge = "budget exhausted";
  else if (status === "over")
    badge = `▲ ${Math.round(Math.abs(delta))} pts over target pace`;
  else if (status === "under")
    badge = `▼ ${Math.round(Math.abs(delta))} pts under target pace`;

  const minLeft = msLeft / 60_000;
  const hoursLeft = msLeft / 3_600_000;
  const remPct = Math.max(0, 100 - card.used);
  const reqRate = hoursLeft > 0 ? remPct / hoursLeft : 0;
  const tokensPerPt = card.token_efficiency?.tokens_per_point ?? 0;

  let projValue = "—";
  let projLabel = "target date reached";
  if (card.rate_per_hour > 0 && remPct > 0 && remPct / card.rate_per_hour < hoursLeft) {
    const exhaustTime = new Date(
      now.getTime() + (remPct / card.rate_per_hour) * 3_600_000,
    );
    const beforeTargetMin = (hoursLeft - remPct / card.rate_per_hour) * 60;
    projValue = `hits 100% ${exhaustTime.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
    projLabel = `${fmtDuration(beforeTargetMin)} before target date`;
  } else if (hoursLeft > 0) {
    const tokRate =
      tokensPerPt > 0 ? ` (~${fmtCompact(Math.round(reqRate * tokensPerPt))} tok/h)` : "";
    projValue = `${reqRate.toFixed(2)} pp/h`;
    projLabel = `needed for 100% at target (curr: ${card.rate_per_hour.toFixed(2)} pp/h${tokRate})`;
  }

  return {
    status,
    badge,
    paceValue: `${Math.round(targetPace)}%`,
    paceLabel: `target pace (std: ${Math.round(card.pace)}%)`,
    leftValue: fmtDuration(minLeft),
    leftLabel: `until target${card.resets_local ? `, resets ${card.resets_local}` : ""}`,
    projValue,
    projLabel,
  };
}

export function Quota() {
  const client = useQueryClient();
  const dashboard = useData<QuotaDashboard>("/quota/dashboard");
  const chats = useData<List<ChatOverview>>("/chats?limit=200");

  const [targetEnabled, setTargetEnabled] = useState(
    () => localStore.read("qb-target-enabled").value === "true",
  );
  const [targetInput, setTargetInput] = useState(
    () => localStore.read("qb-target-date").value ?? "",
  );
  const [activePointByCard, setActivePointByCard] = useState<Record<string, number>>({});
  const [openTexts, setOpenTexts] = useState<Record<string, QuotaRecentText | "loading">>({});

  const collectAct = useCommand(commands.collectQuota);
  const policyAct = useCommand(commands.saveQuotaPolicy);

  // Sync default target input once dashboard loads
  useEffect(() => {
    if (!targetInput && dashboard.data?.default_target_local) {
      setTargetInput(dashboard.data.default_target_local);
    }
  }, [dashboard.data?.default_target_local, targetInput]);

  // Subscribe to live SSE capacity revisions when EventSource is available. The browser reconnects
  // a dropped stream by itself but gives up on an error response, such as the 401 from a server that
  // restarted and forgot this page's session: renew the session and open a new stream, backing off
  // while that fails, and stop when the server now holds other data. A server without quota
  // collectors ends each stream after one snapshot and the browser reopens it within seconds, so
  // updates count as paused only once the stream stays down.
  const [paused, setPaused] = useState<string | null>(null);
  useEffect(() => {
    if (typeof EventSource === "undefined") return;
    let closed = false;
    let source: EventSource;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let pause: ReturnType<typeof setTimeout> | undefined;
    let delay = 1000;
    const connect = () => {
      source = new EventSource("/api/v1/quota/capacity/events");
      source.onopen = () => {
        delay = 1000;
        clearTimeout(pause);
        pause = undefined;
        setPaused(null);
      };
      source.onmessage = () => {
        if (!closed) {
          client.invalidateQueries({ queryKey: [getDataContext(), "/quota/dashboard"] });
        }
      };
      source.onerror = () => {
        if (closed) return;
        pause ??= setTimeout(() => setPaused(PAUSED_NOTE), 10_000);
        if (source.readyState === EventSource.CLOSED) reconnect();
      };
    };
    const reconnect = () => {
      timer = setTimeout(() => {
        renewSession().then(
          () => {
            if (!closed) connect();
          },
          (error: unknown) => {
            if (closed) return;
            if (error instanceof ApiError && error.code === "data_context") {
              clearTimeout(pause);
              setPaused(error.message);
            } else reconnect();
          },
        );
      }, delay);
      delay = Math.min(delay * 2, 30_000);
    };
    connect();
    return () => {
      closed = true;
      clearTimeout(timer);
      clearTimeout(pause);
      source.close();
    };
  }, [client]);

  // Map external session_id -> Worklog chat ID so recent requests & sessions link directly to #chats/{id}
  const chatIdBySession = useMemo(() => {
    const map = new Map<string, string>();
    for (const chat of chats.data?.items ?? []) {
      map.set(chat.id, chat.id);
      if (chat.chatId) {
        map.set(chat.chatId, chat.id);
      }
    }
    return map;
  }, [chats.data]);

  const applyPreset = (preset: { days?: number; weekday?: number; hour?: number }) => {
    const target = new Date();
    target.setMinutes(0, 0, 0);
    if (preset.days !== undefined) {
      target.setDate(target.getDate() + preset.days);
    } else if (preset.weekday !== undefined && preset.hour !== undefined) {
      const day = target.getDay();
      let diff = (preset.weekday - day + 7) % 7;
      if (diff === 0 && target.getHours() >= preset.hour) diff = 7;
      target.setDate(target.getDate() + diff);
      target.setHours(preset.hour, 0, 0, 0);
    }
    const formatted = toLocalInputValue(target);
    setTargetInput(formatted);
    setTargetEnabled(true);
    localStore.write("qb-target-date", formatted);
    localStore.write("qb-target-enabled", "true");
  };

  const toggleTarget = (enabled: boolean) => {
    setTargetEnabled(enabled);
    localStore.write("qb-target-enabled", enabled ? "true" : "false");
    if (targetInput) {
      localStore.write("qb-target-date", targetInput);
    }
  };

  const loadRecentText = async (rowId: string) => {
    if (openTexts[rowId]) {
      const copy = { ...openTexts };
      delete copy[rowId];
      setOpenTexts(copy);
      return;
    }
    setOpenTexts((prev) => ({ ...prev, [rowId]: "loading" }));
    try {
      const res = await request<QuotaRecentText>(
        `/quota/recent-text/${encodeURIComponent(rowId)}`,
      );
      setOpenTexts((prev) => ({ ...prev, [rowId]: res }));
    } catch (err) {
      setOpenTexts((prev) => ({
        ...prev,
        [rowId]: {
          status: "unavailable",
          note: err instanceof Error ? err.message : "Could not load request text",
        },
      }));
    }
  };

  if (dashboard.isPending) return <p>Loading quota burndown…</p>;
  if (dashboard.error) return <ErrorMessage error={dashboard.error} />;

  const data = dashboard.data!;
  const now = new Date();
  const parsedTarget =
    targetEnabled && targetInput ? new Date(targetInput) : null;
  const validTarget =
    parsedTarget && !Number.isNaN(parsedTarget.getTime()) && parsedTarget.getTime() > now.getTime()
      ? parsedTarget
      : null;

  const weeklyCards = data.cards.filter((c) => c.window_min !== 300);
  const shortCards = data.cards.filter((c) => c.window_min === 300);
  const reservePct = data.capacity?.policy?.reserve_pct ?? 10;

  return (
    <div className="quota-page">
      <header className="page-header quota-header">
        <div>
          <h1>Quota Burndown</h1>
          <p className="muted">
            Live capacity gates, two-cycle burndown curves, and local token accounting across Claude,
            Codex, and Antigravity ({data.sample_count.toLocaleString()} samples in view).
          </p>
        </div>
        <div className="quota-header-actions">
          <label className="quota-reserve-select">
            <span>Reserve buffer</span>
            <select
              aria-label="Reserve buffer percentage"
              value={reservePct}
              disabled={policyAct.isPending}
              onChange={(e) =>
                policyAct.mutate({
                  reserve_pct: Number(e.target.value) as 5 | 10 | 20,
                })
              }
            >
              <option value={5}>5%</option>
              <option value={10}>10%</option>
              <option value={20}>20%</option>
            </select>
          </label>
          <button
            type="button"
            onClick={() =>
              collectAct.mutate({ since_days: 14, full: false, usage: true })
            }
            disabled={collectAct.isPending}
          >
            {collectAct.isPending ? "Collecting…" : "Collect now"}
          </button>
        </div>
      </header>

      {paused && (
        <div className="quota-banner" role="status">
          {paused}
        </div>
      )}

      {data.warnings.length > 0 && (
        <div className="quota-banner" role="status">
          {data.warnings.map((w, idx) => (
            <div key={idx}>{w}</div>
          ))}
        </div>
      )}

      {/* Target completion date bar */}
      <section className="card quota-target-bar" aria-label="Target completion date">
        <div className="quota-target-controls">
          <label className="quota-target-toggle">
            <input
              type="checkbox"
              checked={targetEnabled}
              onChange={(e) => toggleTarget(e.target.checked)}
            />
            <span>Target 100% by</span>
          </label>
          <input
            type="datetime-local"
            aria-label="Target date and time"
            value={targetInput}
            onChange={(e) => {
              setTargetInput(e.target.value);
              localStore.write("qb-target-date", e.target.value);
              if (!targetEnabled) toggleTarget(true);
            }}
          />
          <div className="quota-target-presets" role="group" aria-label="Target date presets">
            <button type="button" className="ghost" onClick={() => applyPreset({ days: 1 })}>
              +1d
            </button>
            <button type="button" className="ghost" onClick={() => applyPreset({ days: 2 })}>
              +2d
            </button>
            <button type="button" className="ghost" onClick={() => applyPreset({ days: 3 })}>
              +3d
            </button>
            <button type="button" className="ghost" onClick={() => applyPreset({ days: 5 })}>
              +5d
            </button>
            <button
              type="button"
              className="ghost"
              onClick={() => applyPreset({ weekday: 5, hour: 17 })}
            >
              Fri 17:00
            </button>
            <button
              type="button"
              className="ghost"
              onClick={() => applyPreset({ weekday: 1, hour: 9 })}
            >
              Mon 09:00
            </button>
          </div>
          {targetEnabled && (
            <button
              type="button"
              className="ghost"
              onClick={() => toggleTarget(false)}
            >
              Standard reset
            </button>
          )}
        </div>
        <div className="quota-target-summary muted" role="status" aria-live="polite">
          {!targetEnabled ? (
            "Standard reset pace active: each model paces to reach 100% by its regular reset."
          ) : !validTarget ? (
            <span className="quota-text-danger">
              Target date is in the past or invalid. Select a future date to pace your burndown.
            </span>
          ) : (
            <span>
              Targeting 100% quota by{" "}
              <strong>{validTarget.toLocaleString([], { weekday: "short", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}</strong>{" "}
              ({fmtDuration((validTarget.getTime() - now.getTime()) / 60_000)} left); providers with
              active credit plans use their saved goals.
            </span>
          )}
        </div>
      </section>

      {/* Short 5-hour session windows summary strip */}
      {shortCards.length > 0 && (
        <section className="quota-short-strip" aria-label="5-hour session windows">
          {shortCards.map((c) => (
            <div key={c.id} className={`card quota-short-card status-${c.status}`}>
              <div className="quota-short-head">
                <strong>
                  {c.group_title} · {c.title}
                </strong>
                <span className={`quota-badge status-${c.status}`}>{c.badge}</span>
              </div>
              <div className="quota-short-metrics">
                <span>
                  <strong>{Math.round(c.used)}%</strong> used ({Math.round(c.remaining_pct)}% left)
                </span>
                <span>
                  <strong>{c.left_value}</strong> {c.left_label}
                </span>
              </div>
            </div>
          ))}
        </section>
      )}

      {/* Weekly provider burndown cards */}
      <section aria-label="Weekly quota burndown history">
        {weeklyCards.length === 0 ? (
          <div className="card">
            <p className="muted">
              No weekly quota readings recorded yet. Click <strong>Collect now</strong> to scan local
              provider logs.
            </p>
          </div>
        ) : (
          <div className="quota-history-grid">
            {weeklyCards.map((card) => {
              const hasPlan = Boolean(data.plans?.plans?.[card.provider]);
              const override = computeTargetOverride(card, validTarget, now, hasPlan);
              const effStatus = override?.status ?? card.status;
              const effBadge = override?.badge ?? card.badge;
              const activeIdx = activePointByCard[card.id];
              const activePt =
                activeIdx !== undefined ? card.chart_points[activeIdx] : undefined;

              return (
                <div key={card.id} className="quota-provider-card-wrap">
                  <article
                    className={`card quota-card status-${effStatus}${card.stale ? " stale" : ""}`}
                  >
                    <header className="quota-card-header">
                      <div>
                        <span className="quota-provider-eyebrow">{card.group_title}</span>
                        <h2>{card.title}</h2>
                      </div>
                      <div className="quota-badges">
                        <span className={`quota-badge status-${effStatus}`}>{effBadge}</span>
                        {card.reset_credits_count > 0 ? (
                          <span className="quota-badge reset">
                            {card.reset_credits_count} reset credit
                            {card.reset_credits_count !== 1 ? "s" : ""}
                          </span>
                        ) : card.reset_credits_known && card.provider === "codex" ? (
                          <span className="quota-badge reset-none">no reset credits left</span>
                        ) : null}
                      </div>
                    </header>

                    <div className="quota-stats-grid">
                      <div>
                        <b>
                          {card.status === "expired" ? "—" : `${Math.round(card.used)}%`}
                        </b>
                        <span>
                          {card.status === "stale" && card.sample_local
                            ? `used as of ${card.sample_local}`
                            : card.status === "expired"
                              ? "no reading for new window yet"
                              : `used, ${Math.round(card.remaining_pct)}% left`}
                        </span>
                      </div>
                      <div>
                        <b>
                          {override
                            ? override.paceValue
                            : card.status === "idle" || card.status === "expired"
                              ? "—"
                              : `${Math.round(card.pace)}%`}
                        </b>
                        <span>{override ? override.paceLabel : "linear pace"}</span>
                      </div>
                      <div>
                        <b>{override ? override.leftValue : card.left_value}</b>
                        <span>{override ? override.leftLabel : card.left_label}</span>
                      </div>
                      <div>
                        <b>{override ? override.projValue : card.proj_value}</b>
                        <span>{override ? override.projLabel : card.proj_label}</span>
                      </div>
                    </div>

                    {card.reset_credits_count > 0 && (
                      <div className="quota-reset-strip">
                        <strong>
                          {card.reset_credits_count} reset credit
                          {card.reset_credits_count !== 1 ? "s" : ""} available
                        </strong>
                        {card.credit_earliest_expiry && (
                          <span> · earliest expires {card.credit_earliest_expiry}</span>
                        )}
                        {card.next_reset_local && (
                          <span> · ideal next reset: {card.next_reset_local}</span>
                        )}
                        {card.runway_with_resets && (
                          <span> · runway with resets: {card.runway_with_resets}</span>
                        )}
                      </div>
                    )}

                    {card.chart_svg ? (
                      <div className="quota-chart-block">
                        {card.chart_heading && (
                          <div className="quota-chart-heading">
                            <span>{card.chart_heading}</span>
                            {activePt && (
                              <span className="quota-chart-readout">
                                {activePt.t}: <strong>{activePt.v}</strong>
                                {activePt.k ? ` · ${activePt.k}` : ""}
                              </span>
                            )}
                          </div>
                        )}
                        <div
                          className="quota-svg-container"
                          onMouseMove={(e) => {
                            if (!card.chart_points.length) return;
                            const rect = e.currentTarget.getBoundingClientRect();
                            const relX = ((e.clientX - rect.left) / rect.width) * 800;
                            let bestIdx = 0;
                            let bestDist = Infinity;
                            card.chart_points.forEach((pt, idx) => {
                              const d = Math.abs(pt.x - relX);
                              if (d < bestDist) {
                                bestDist = d;
                                bestIdx = idx;
                              }
                            });
                            setActivePointByCard((prev) => ({ ...prev, [card.id]: bestIdx }));
                          }}
                          onMouseLeave={() =>
                            setActivePointByCard((prev) => {
                              const next = { ...prev };
                              delete next[card.id];
                              return next;
                            })
                          }
                          dangerouslySetInnerHTML={{ __html: card.chart_svg }}
                        />
                        {card.token_legend.length > 0 && (
                          <ul className="quota-token-legend">
                            {card.token_legend.map((item) => (
                              <li key={item.model}>
                                <i className={`quota-swatch ${item.slot_class}`} />
                                {item.model}: {fmtCompact(item.tokens)}
                              </li>
                            ))}
                          </ul>
                        )}
                        {card.token_efficiency ? (
                          <p className="muted quota-efficiency-note">
                            <strong>
                              {Math.round(
                                card.token_efficiency.tokens_per_point,
                              ).toLocaleString()}{" "}
                              recorded tokens per 1 percentage point used
                            </strong>{" "}
                            ({card.token_efficiency.tokens.toLocaleString()} tokens ÷{" "}
                            {card.token_efficiency.percentage_points} pp,{" "}
                            {card.token_efficiency.start_local} to{" "}
                            {card.token_efficiency.end_local}).
                          </p>
                        ) : (
                          <p className="muted quota-efficiency-note">
                            Recorded tokens per 1 percentage point used: unavailable (needs a
                            measured increase and matching recorded tokens).
                          </p>
                        )}
                      </div>
                    ) : (
                      <p className="muted">No readings in the two-cycle domain.</p>
                    )}

                    <footer className="quota-card-footer muted">
                      last sample{" "}
                      {card.age_min !== null ? `${Math.round(card.age_min)} min ago` : "never"} via{" "}
                      {card.source}, {card.samples_count} samples this window
                      {card.stale && (
                        <>
                          ; <strong>stale</strong>: no recent quota observation
                          {card.issue ? ` (${card.issue})` : ""}
                        </>
                      )}
                    </footer>
                  </article>
                </div>
              );
            })}
          </div>
        )}
      </section>

      {/* Live Capacity Matrix & Collector Health */}
      {data.capacity?.provider_groups && data.capacity.provider_groups.length > 0 && (
        <section className="card quota-section" aria-label="Live capacity matrix">
          <details>
            <summary className="quota-section-summary">
              <span>Live Capacity Matrix &amp; Collector Health</span>
              <span className="muted">
                Reserve policy: {reservePct}% · Revision {data.capacity.revision ?? 0}
              </span>
            </summary>
            {data.capacity.collector_health && (
              <div className="quota-health-pills">
                {Object.entries(data.capacity.collector_health).map(([name, info]) => (
                  <span
                    key={name}
                    className={`quota-badge health-${info.state}`}
                    title={info.last_error ?? undefined}
                  >
                    {name}: {info.state}
                    {info.last_error ? ` (${info.last_error})` : ""}
                  </span>
                ))}
              </div>
            )}
            {data.capacity.provider_groups.map((group) => (
              <div key={group.provider} className="quota-capacity-group">
                <h3>{group.label}</h3>
                <div className="quota-table-scroll">
                  <table className="quota-table">
                    <thead>
                      <tr>
                        <th>Allowance</th>
                        <th>Pool / Models</th>
                        <th className="num">Used</th>
                        <th className="num">Remaining</th>
                        <th className="num">Usable ({100 - reservePct}% cap)</th>
                        <th>Reset</th>
                        <th className="num">Runway</th>
                        <th>Freshness / Source</th>
                      </tr>
                    </thead>
                    <tbody>
                      {group.limits
                        .filter((l) => !l.id.includes(":reported-"))
                        .map((limit) => {
                          const w = limit.window;
                          return (
                            <tr key={limit.id}>
                              <td>
                                <strong>{limit.label}</strong>
                                {limit.availability_reason && (
                                  <div className="muted small">{limit.availability_reason}</div>
                                )}
                              </td>
                              <td>
                                <code>{limit.pool_id ?? "unreported"}</code>
                                <div className="muted small">
                                  {(limit.models ?? []).join(", ") || "—"}
                                </div>
                              </td>
                              <td className="num">
                                {w.used_pct !== null && w.used_pct !== undefined
                                  ? `${Math.round(w.used_pct)}%`
                                  : "unknown"}
                              </td>
                              <td className="num">
                                {w.remaining_pct !== null && w.remaining_pct !== undefined
                                  ? `${Math.round(w.remaining_pct)}%`
                                  : "unknown"}
                              </td>
                              <td className="num">
                                {w.usable_pct !== null && w.usable_pct !== undefined
                                  ? `${Math.round(w.usable_pct)}%`
                                  : "unknown"}
                              </td>
                              <td>{w.resets_at ?? "unknown"}</td>
                              <td className="num">
                                {w.runway_minutes !== null && w.runway_minutes !== undefined
                                  ? fmtDuration(w.runway_minutes)
                                  : "unknown"}
                              </td>
                              <td>
                                {w.freshness} · {w.source}
                              </td>
                            </tr>
                          );
                        })}
                    </tbody>
                  </table>
                </div>
              </div>
            ))}
          </details>
        </section>
      )}

      {/* Recorded tokens by model, last 7 days */}
      {data.efficiency?.daily_models && (
        <section className="card quota-section" aria-label="Recorded tokens by model, last 7 days">
          <h2>Recorded Tokens by Model (Last 7 Days)</h2>
          <p className="muted">
            Today and the preceding six local calendar days. Locally recorded tokens, not
            subscription quota share.
          </p>
          {data.efficiency.daily_models_html && (
            <div
              className="quota-daily-models-html"
              dangerouslySetInnerHTML={{ __html: data.efficiency.daily_models_html }}
            />
          )}
        </section>
      )}

      {/* Token usage summary + Recent requests */}
      {data.usage && (
        <section className="card quota-section" aria-label="Token usage">
          <h2>Token Usage</h2>
          <div className="quota-usage-cards">
            {(["claude", "codex", "antigravity"] as const).map((provider) => {
              const periods = data.usage!.summary[provider];
              if (!periods) return null;
              const title =
                provider === "claude"
                  ? "Claude"
                  : provider === "codex"
                    ? "Codex"
                    : "Antigravity";
              const isAntigravity = provider === "antigravity";
              return (
                <article key={provider} className="card quota-usage-card">
                  <header className="quota-card-header">
                    <h3>{title}</h3>
                    <span className="quota-badge">
                      {isAntigravity ? "tokens inferred" : "exact counts"}
                    </span>
                  </header>
                  <div className="quota-table-scroll">
                    <table className="quota-table">
                      <thead>
                        <tr>
                          <th>Metric</th>
                          <th className="num">Today</th>
                          <th className="num">7 days</th>
                          <th className="num">30 days</th>
                        </tr>
                      </thead>
                      <tbody>
                        <tr>
                          <td>Prompts</td>
                          <td className="num">{fmtCompact(periods.today.prompts)}</td>
                          <td className="num">{fmtCompact(periods["7d"].prompts)}</td>
                          <td className="num">{fmtCompact(periods["30d"].prompts)}</td>
                        </tr>
                        <tr>
                          <td>Requests</td>
                          <td className="num">{fmtCompact(periods.today.requests)}</td>
                          <td className="num">{fmtCompact(periods["7d"].requests)}</td>
                          <td className="num">{fmtCompact(periods["30d"].requests)}</td>
                        </tr>
                        {!isAntigravity ? (
                          <>
                            <tr>
                              <td>Input</td>
                              <td className="num">{fmtCompact(periods.today.input)}</td>
                              <td className="num">{fmtCompact(periods["7d"].input)}</td>
                              <td className="num">{fmtCompact(periods["30d"].input)}</td>
                            </tr>
                            <tr>
                              <td>Cache read</td>
                              <td className="num">{fmtCompact(periods.today.cache_read)}</td>
                              <td className="num">{fmtCompact(periods["7d"].cache_read)}</td>
                              <td className="num">{fmtCompact(periods["30d"].cache_read)}</td>
                            </tr>
                            <tr>
                              <td>Cache write</td>
                              <td className="num">{fmtCompact(periods.today.cache_write)}</td>
                              <td className="num">{fmtCompact(periods["7d"].cache_write)}</td>
                              <td className="num">{fmtCompact(periods["30d"].cache_write)}</td>
                            </tr>
                            <tr>
                              <td>Output</td>
                              <td className="num">{fmtCompact(periods.today.output)}</td>
                              <td className="num">{fmtCompact(periods["7d"].output)}</td>
                              <td className="num">{fmtCompact(periods["30d"].output)}</td>
                            </tr>
                            <tr>
                              <td>
                                <strong>Total</strong>
                              </td>
                              <td className="num">
                                <strong>{fmtCompact(periods.today.total)}</strong>
                              </td>
                              <td className="num">
                                <strong>{fmtCompact(periods["7d"].total)}</strong>
                              </td>
                              <td className="num">
                                <strong>{fmtCompact(periods["30d"].total)}</strong>
                              </td>
                            </tr>
                          </>
                        ) : (
                          <tr>
                            <td>~Input (inferred)</td>
                            <td className="num">~{fmtCompact(periods.today.inferred_input)}</td>
                            <td className="num">~{fmtCompact(periods["7d"].inferred_input)}</td>
                            <td className="num">~{fmtCompact(periods["30d"].inferred_input)}</td>
                          </tr>
                        )}
                      </tbody>
                    </table>
                  </div>
                </article>
              );
            })}
          </div>

          {/* Recent requests table */}
          {data.usage.recent.length > 0 && (
            <div className="quota-recent-block">
              <h3>Recent Requests</h3>
              <div className="quota-table-scroll">
                <table className="quota-table">
                  <thead>
                    <tr>
                      <th>Time</th>
                      <th>Provider</th>
                      <th>Model @ Effort</th>
                      <th>Session / Chat</th>
                      <th className="num">Input</th>
                      <th className="num">Cache R</th>
                      <th className="num">Cache W</th>
                      <th className="num">Output</th>
                      <th className="num">Total</th>
                      <th>Transcript</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.usage.recent.map((row) => {
                      const linkedChatId = row.session_id
                        ? chatIdBySession.get(row.session_id)
                        : undefined;
                      const textState = openTexts[row.id];
                      return (
                        <FragmentRow
                          key={row.id}
                          row={row}
                          linkedChatId={linkedChatId}
                          textState={textState}
                          onToggleText={() => loadRecentText(row.id)}
                        />
                      );
                    })}
                  </tbody>
                </table>
              </div>
              <p className="muted small">{data.usage.note}</p>
            </div>
          )}
        </section>
      )}

      {/* Efficiency Drilldowns */}
      {data.efficiency && (
        <section className="card quota-section" aria-label="Efficiency drilldowns">
          <h2>Efficiency Drilldowns (Last 7 Days)</h2>
          {data.efficiency.insights.length > 0 && (
            <ul className="quota-insights-list">
              {data.efficiency.insights.map((item) => {
                const linkedChatId = item.session_id
                  ? chatIdBySession.get(item.session_id)
                  : undefined;
                return (
                  <li key={item.label}>
                    <strong>{item.label}:</strong> {fmtCompact(item.value)}{" "}
                    <a href={`#${item.session_anchor}`}>view session row</a>
                    {linkedChatId && (
                      <>
                        {" "}
                        · <a href={`#chats/${linkedChatId}`}>open in Chats</a>
                      </>
                    )}
                  </li>
                );
              })}
            </ul>
          )}

          <details open>
            <summary className="quota-section-summary">
              <span>By Session (Top 20)</span>
            </summary>
            <div className="quota-table-scroll">
              <table className="quota-table">
                <thead>
                  <tr>
                    <th>Session</th>
                    <th className="num">Uncached In</th>
                    <th className="num">Cached In</th>
                    <th className="num">Output</th>
                    <th className="num">Reasoning / Out</th>
                    <th className="num">Requests</th>
                    <th className="num">Median Tok/Req</th>
                  </tr>
                </thead>
                <tbody>
                  {data.efficiency.by_session.slice(0, 20).map((row) => {
                    const linkedChatId = row.session_id
                      ? chatIdBySession.get(row.session_id)
                      : undefined;
                    return (
                      <tr key={row.key} id={row.anchor ?? undefined}>
                        <td>
                          <code>{row.key}</code>
                          {linkedChatId && (
                            <>
                              {" "}
                              <a href={`#chats/${linkedChatId}`} className="quota-chat-link">
                                Open chat
                              </a>
                            </>
                          )}
                        </td>
                        <td className="num">
                          {row.exact ? fmtCompact(row.uncached_input) : "unknown"}
                        </td>
                        <td className="num">
                          {row.exact ? fmtCompact(row.cached_input) : "unknown"}
                        </td>
                        <td className="num">
                          {row.exact ? fmtCompact(row.output) : "unknown"}
                        </td>
                        <td className="num">
                          {row.exact && row.reasoning_share_of_output_pct !== null
                            ? `${row.reasoning_share_of_output_pct.toFixed(1)}%`
                            : "unknown"}
                        </td>
                        <td className="num">{row.requests}</td>
                        <td className="num">
                          {row.exact ? fmtCompact(row.median_tokens_per_request) : "unknown"}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </details>

          <details>
            <summary className="quota-section-summary">
              <span>By Model × Effort (Top 20)</span>
            </summary>
            <div className="quota-table-scroll">
              <table className="quota-table">
                <thead>
                  <tr>
                    <th>Model @ Effort</th>
                    <th className="num">Uncached In</th>
                    <th className="num">Cached In</th>
                    <th className="num">Output</th>
                    <th className="num">Reasoning / Out</th>
                    <th className="num">Requests</th>
                    <th className="num">Median Tok/Req</th>
                  </tr>
                </thead>
                <tbody>
                  {data.efficiency.by_model_effort.slice(0, 20).map((row) => (
                    <tr key={row.key}>
                      <td>
                        <code>{row.key}</code>
                      </td>
                      <td className="num">
                        {row.exact ? fmtCompact(row.uncached_input) : "unknown"}
                      </td>
                      <td className="num">
                        {row.exact ? fmtCompact(row.cached_input) : "unknown"}
                      </td>
                      <td className="num">{row.exact ? fmtCompact(row.output) : "unknown"}</td>
                      <td className="num">
                        {row.exact && row.reasoning_share_of_output_pct !== null
                          ? `${row.reasoning_share_of_output_pct.toFixed(1)}%`
                          : "unknown"}
                      </td>
                      <td className="num">{row.requests}</td>
                      <td className="num">
                        {row.exact ? fmtCompact(row.median_tokens_per_request) : "unknown"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
        </section>
      )}

      {/* Paid Credit Plans */}
      <section className="card quota-section" aria-label="Paid credit burndown plans">
        <h2>Paid Credit Burndown Plans</h2>
        <p className="muted">
          Track extra-usage USD (Claude) or prepaid credit pools (Codex) against a target window.
        </p>
        <div className="quota-plans-grid">
          {(["claude", "codex"] as const).map((provider) => (
            <CreditPlanCard
              key={`${provider}-${data.plans?.revision ?? 0}`}
              provider={provider}
              revision={data.plans?.revision ?? 0}
              plan={data.plans?.plans?.[provider] ?? null}
              observation={data.plans?.observations?.[provider]}
              models={data.plans?.models?.[provider]}
            />
          ))}
        </div>
      </section>
    </div>
  );
}

function FragmentRow({
  row,
  linkedChatId,
  textState,
  onToggleText,
}: {
  row: QuotaDashboard["usage"] extends { recent: (infer R)[] } | null ? R : never;
  linkedChatId?: string;
  textState?: QuotaRecentText | "loading";
  onToggleText: () => void;
}) {
  return (
    <>
      <tr className={row.inferred ? "quota-row-inferred" : undefined}>
        <td>{row.ts_local}</td>
        <td>{row.provider}</td>
        <td>
          <code>
            {row.model || "?"} @ {row.effort || "?"}
          </code>
        </td>
        <td>
          {linkedChatId ? (
            <a href={`#chats/${linkedChatId}`} className="quota-chat-link">
              {row.session_id.slice(0, 8)}…
            </a>
          ) : row.session_id ? (
            <code title={row.session_id}>{row.session_id.slice(0, 8)}</code>
          ) : (
            "—"
          )}
        </td>
        {row.inferred ? (
          <>
            <td className="num">~{fmtCompact(row.input_tokens_inferred)}</td>
            <td className="num">—</td>
            <td className="num">—</td>
            <td className="num">?</td>
            <td className="num">?</td>
          </>
        ) : (
          <>
            <td className="num">{fmtCompact(row.input_tokens)}</td>
            <td className="num">{fmtCompact(row.cache_read_tokens)}</td>
            <td className="num">{fmtCompact(row.cache_write_tokens)}</td>
            <td className="num">{fmtCompact(row.output_tokens)}</td>
            <td className="num">
              <strong>{fmtCompact(row.total_tokens)}</strong>
            </td>
          </>
        )}
        <td>
          <button type="button" className="ghost small" onClick={onToggleText}>
            {textState ? "Hide text" : "View text"}
          </button>
        </td>
      </tr>
      {textState && (
        <tr className="quota-text-row">
          <td colSpan={10}>
            {textState === "loading" ? (
              <p className="muted">Loading recorded request text…</p>
            ) : textState.status !== "ok" ? (
              <p className="muted">{textState.note ?? "Request text unavailable."}</p>
            ) : (
              <div className="quota-text-grid">
                <div>
                  <h4>Recorded user prompt</h4>
                  <pre className="quota-pre">{textState.prompt || "(empty)"}</pre>
                </div>
                <div>
                  <h4>Recorded response</h4>
                  <pre className="quota-pre">{textState.response || "(empty)"}</pre>
                </div>
              </div>
            )}
          </td>
        </tr>
      )}
    </>
  );
}

function CreditPlanCard({
  provider,
  revision,
  plan,
  observation,
  models,
}: {
  provider: "claude" | "codex";
  revision: number;
  plan: QuotaCreditPlanReport | null;
  observation?: {
    balance: string | null;
    cumulative_spent: string | null;
    unit: string;
    observed_at: string;
    note: string;
  };
  models?: QuotaPlanModelOption[];
}) {
  const unit = provider === "claude" ? "USD" : "credits";
  const modelOptions: QuotaPlanModelOption[] =
    models && models.length > 0
      ? models
      : provider === "claude"
        ? [
            {
              id: "claude-sonnet-4-6",
              label: "Claude Sonnet 4.6",
              speeds: ["standard"],
              rates_version: "2026-10-07",
            },
            {
              id: "claude-opus-4-6",
              label: "Claude Opus 4.6",
              speeds: ["standard"],
              rates_version: "2026-10-07",
            },
          ]
        : [
            {
              id: "gpt-6-sol",
              label: "GPT-6 Sol",
              speeds: ["standard", "fast"],
              rates_version: "2026-10-07",
            },
            {
              id: "gpt-6-luna",
              label: "GPT-6 Luna",
              speeds: ["standard", "fast"],
              rates_version: "2026-10-07",
            },
          ];

  const defaultEnd = useMemo(() => {
    const d = new Date(Date.now() + 7 * 86_400_000);
    d.setMinutes(0, 0, 0);
    return toLocalInputValue(d);
  }, []);

  const [amount, setAmount] = useState(plan?.amount ?? "");
  const [endsAt, setEndsAt] = useState(
    plan?.ends_at ? toLocalInputValue(new Date(plan.ends_at)) : defaultEnd,
  );
  const [model, setModel] = useState(plan?.model ?? modelOptions[0].id);
  const [speed, setSpeed] = useState<"standard" | "fast" | "ultrafast">(
    (plan?.speed as "standard" | "fast" | "ultrafast") ?? "standard",
  );

  const selectedModel =
    modelOptions.find((m) => m.id === model) ?? modelOptions[0];
  const saveAct = useCommand(commands.saveQuotaPlan);

  return (
    <article className="card quota-plan-card">
      <header className="quota-card-header">
        <h3>
          {provider === "claude"
            ? "Claude Extra Usage (USD)"
            : "Codex Paid Credits"}
        </h3>
        <span className="quota-badge">
          {plan ? `${plan.amount} ${unit} (${plan.status})` : "no active plan"}
        </span>
      </header>

      {observation && (
        <p className="muted small">
          Observed balance:{" "}
          <strong>{observation.balance ?? "unknown"}</strong> {unit}
          {observation.cumulative_spent
            ? ` · counter: ${observation.cumulative_spent}`
            : ""}
          {observation.note ? ` · ${observation.note}` : ""}
        </p>
      )}

      {plan && (
        <div className="quota-stats-grid">
          <div>
            <b>
              {plan.confirmed_spent ?? "0"} / {plan.amount}
            </b>
            <span>{unit} spent</span>
          </div>
          <div>
            <b>{plan.remaining}</b>
            <span>{unit} remaining</span>
          </div>
          <div>
            <b>{plan.required_rate_per_hour ?? "—"}</b>
            <span>{unit}/h required</span>
          </div>
          <div>
            <b>{plan.model}</b>
            <span>{plan.speed}</span>
          </div>
        </div>
      )}

      <form
        className="quota-plan-form"
        onSubmit={(e) => {
          e.preventDefault();
          const parsedEnd = new Date(endsAt);
          if (!amount.trim() || Number.isNaN(parsedEnd.getTime())) return;
          saveAct.mutate({
            provider,
            expected_revision: revision,
            plan: {
              amount: amount.trim(),
              starts_at: plan?.starts_at ?? new Date().toISOString(),
              ends_at: parsedEnd.toISOString(),
              model,
              speed,
            },
          });
        }}
      >
        <div className="quota-plan-fields">
          <label>
            <span>Budget ({unit})</span>
            <input
              type="text"
              required
              placeholder={provider === "claude" ? "50.00" : "1000"}
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
            />
          </label>
          <label>
            <span>Target end</span>
            <input
              type="datetime-local"
              required
              value={endsAt}
              onChange={(e) => setEndsAt(e.target.value)}
            />
          </label>
          <label>
            <span>Model</span>
            <select
              value={model}
              onChange={(e) => {
                const next = e.target.value;
                setModel(next);
                const opt = modelOptions.find((m) => m.id === next);
                if (opt && !opt.speeds.includes(speed)) {
                  setSpeed(
                    (opt.speeds[0] as "standard" | "fast" | "ultrafast") ??
                      "standard",
                  );
                }
              }}
            >
              {modelOptions.map((opt) => (
                <option key={opt.id} value={opt.id}>
                  {opt.label}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>Speed</span>
            <select
              value={speed}
              onChange={(e) =>
                setSpeed(e.target.value as "standard" | "fast" | "ultrafast")
              }
            >
              {selectedModel.speeds.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </label>
        </div>
        {saveAct.error && <ErrorMessage error={saveAct.error} />}
        <div className="quota-plan-actions">
          <button type="submit" disabled={saveAct.isPending}>
            {saveAct.isPending ? "Saving…" : plan ? "Update plan" : "Save plan"}
          </button>
          {plan && (
            <button
              type="button"
              className="ghost"
              disabled={saveAct.isPending}
              onClick={() =>
                saveAct.mutate({
                  provider,
                  expected_revision: revision,
                  plan: null,
                })
              }
            >
              Clear plan
            </button>
          )}
        </div>
      </form>
    </article>
  );
}
