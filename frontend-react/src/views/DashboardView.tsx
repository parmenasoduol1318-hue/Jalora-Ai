import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { ProviderLogo } from "../components/ProviderLogos";
import type {
  HealthResponse, OllamaMemorySnapshot, Task, ActivityEntry,
  ProviderStatus, OllamaRouteEvent, CalendarEventT, ProviderCard,
} from "../lib/types";

interface Props {
  t: (k: string) => string;
  ramRoute: OllamaRouteEvent | null;
}

export function DashboardView({ t, ramRoute }: Props) {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [ollama, setOllama] = useState<OllamaMemorySnapshot | null>(null);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [activity, setActivity] = useState<ActivityEntry[]>([]);
  const [providers, setProviders] = useState<Record<string, ProviderStatus>>({});
  const [providerCards, setProviderCards] = useState<ProviderCard[]>([]);
  const [todayEvents, setTodayEvents] = useState<CalendarEventT[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const [h, o, t2, a, p, cal, reg] = await Promise.allSettled([
          api<HealthResponse>("/api/health"),
          api<OllamaMemorySnapshot>("/api/ollama/memory"),
          api<Task[]>("/api/tasks?status=running"),
          api<ActivityEntry[]>("/api/activity?limit=20"),
          api<{ providers: Record<string, ProviderStatus> }>("/api/providers"),
          api<{ events: CalendarEventT[] }>("/api/calendar/today"),
          api<{ providers: ProviderCard[] }>("/api/integrations/providers"),
        ]);
        if (!cancelled) {
          if (h.status === "fulfilled") setHealth(h.value);
          if (o.status === "fulfilled") setOllama(o.value);
          if (t2.status === "fulfilled") setTasks(t2.value);
          if (a.status === "fulfilled") setActivity(a.value);
          if (p.status === "fulfilled") setProviders(p.value.providers || {});
          if (cal.status === "fulfilled") setTodayEvents(cal.value.events || []);
          if (reg.status === "fulfilled") setProviderCards(reg.value.providers || []);
          setLoading(false);
        }
      } catch {
        if (!cancelled) setLoading(false);
      }
    };
    load();
    const id = setInterval(load, 30000);
    return () => { cancelled = true; clearInterval(id); };
  }, []);

  const ramModel = ramRoute ? { model: ramRoute.model, tier: ramRoute.tier } : null;
  const activeTasks = tasks.filter((tk) => tk.status === "running" || tk.status === "queued");

  if (loading && !health) {
    return (
      <div className="dashboard">
        <div className="dashboard-scroll">
          <div style={{ padding: "22px", color: "var(--text-dim)" }}>{t("refresh")}…</div>
        </div>
      </div>
    );
  }

  const providerLogoMap: Record<string, string> = {
    openai: "openai", anthropic: "anthropic", google: "google",
    groq: "groq", deepseek: "deepseek", mistral: "mistral",
    openrouter: "openrouter", ollama: "ollama",
    lmstudio: "lmstudio", jan: "jan",
    cohere: "cohere", cerebras: "cerebras", perplexity: "perplexity",
  };

  return (
    <div className="dashboard">
      <div className="dashboard-scroll">
        <div className="dashboard-grid">
          <div className="left-column">
            {/* System health card */}
            <div className="dashboard-card">
              <div className="dc-title">{t("dashboardSystem")}</div>
              <div className="dc-content">
                {health ? (
                  <>
                    <div className="dc-value">
                      <span className={`rs-status-dot ${health.status === "ok" ? "ok" : "warn"}`}></span>
                      {health.status === "ok" ? t("dashboardHealthy") : `Status: ${health.status}`}
                    </div>
                    <div className="dc-label" style={{ marginTop: 6 }}>
                      {health.database === "ok" ? t("dashboardDbOk") : t("dashboardDbError")}
                    </div>
                  </>
                ) : (
                  <div className="dc-value">—</div>
                )}
                {ollama && (
                  <div className="dc-label" style={{ marginTop: 6 }}>
                    {t("ramIndicator").replace("{free}", ollama.free_gb.toFixed(2))}
                    {ramModel && ` · ${t("ramTier").replace("{tier}", ramModel.tier)}`}
                  </div>
                )}
              </div>
            </div>

            {/* Provider status card */}
            <div className="dashboard-card">
              <div className="dc-title">{t("dashboardProviders")}</div>
              <div className="dc-content">
                {Object.entries(providers).slice(0, 6).map(([name, prov]) => (
                  <div className="rs-provider-row" key={name}>
                    <span className="rs-provider-name">
                      <ProviderLogo id={providerLogoMap[name] ?? name} className="dc-logo" />
                      {name}
                    </span>
                    <span className={`rs-provider-status`} style={{
                      color: prov.disabled ? "var(--warn)" : prov.usable ? "var(--ok)" : "var(--text-dim)",
                    }}>
                      {prov.disabled ? "disabled" : prov.usable ? "ready" : "not configured"}
                    </span>
                  </div>
                ))}
              </div>
            </div>

            {/* Tasks snapshot card */}
            <div className="dashboard-card">
              <div className="dc-title">{t("dashboardTasks")}</div>
              <div className="dc-content">
                {activeTasks.length === 0 ? (
                  <div className="dc-label">{t("rightPanelEmpty")}</div>
                ) : (
                  activeTasks.slice(0, 4).map((tk) => (
                    <div className="rs-provider-row" key={tk.id}>
                      <span className="rs-provider-name">{tk.goal || `#${tk.id.slice(0, 6)}`}</span>
                      <span className="rs-provider-status" style={{
                        color: tk.status === "running" ? "var(--accent)"
                          : tk.status === "failed" ? "var(--danger)"
                          : "var(--ok)",
                      }}>{tk.status}</span>
                    </div>
                  ))
                )}
              </div>
            </div>
          </div>

          <div className="right-column">
            {/* Integrations card */}
            <div className="dashboard-card">
              <div className="dc-title">{t("rightPanelIntegrations")}</div>
              <div className="dc-content">
                {providerCards
                  .filter((c) => [
                    "gmail", "calendar", "drive", "telnyx", "twilio", "search", "ollama",
                  ].includes(c.name))
                  .map((c) => {
                    const status = c.connected ? "connected" : "not configured";
                    const color = c.connected ? "var(--ok)" : "var(--text-dim)";
                    return (
                      <div className="rs-provider-row" key={c.name}>
                        <span className="rs-provider-name">
                          <ProviderLogo id={c.logo ?? c.name} className="dc-logo" />
                          {c.name}
                        </span>
                        <span className="rs-provider-status" style={{ color }}>{status}</span>
                      </div>
                    );
                  })}
              </div>
            </div>

            {/* Today's agenda card (daily activities) */}
            <div className="dashboard-card">
              <div className="dc-title">{t("dashboardToday")}</div>
              <div className="dc-content">
                {todayEvents.length === 0 ? (
                  <div className="dc-label">{t("rightPanelEmpty")}</div>
                ) : (
                  todayEvents.slice(0, 4).map((ev) => (
                    <div className="rs-provider-row" key={ev.id}>
                      <span className="rs-provider-name">
                        <ProviderLogo id="calendar" className="dc-logo" />
                        {ev.title || "Untitled event"}
                      </span>
                      <span className="rs-provider-status" style={{ color: "var(--accent)" }}>
                        {ev.start
                          ? new Date(ev.start).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
                          : "—"}
                      </span>
                    </div>
                  ))
                )}
              </div>
            </div>
          </div>
        </div>

        {/* Recent activity */}
        <div className="recent-activity">
          <h3>{t("dashboardActivity")}</h3>
          {activity.length === 0 ? (
            <div className="activity-empty">{t("dashboardEmpty")}</div>
          ) : (
            activity.slice(0, 10).map((entry) => (
              <div className="activity-item" key={entry.id || entry.created_at}>
                <span className="act-icon">{entry.success !== false ? "✓" : "✗"}</span>
                <div className="details">
                  <div>{entry.result_summary || entry.tool || t("jalora")}</div>
                  <div className="act-sub">{new Date(entry.created_at).toLocaleString()}</div>
                </div>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
