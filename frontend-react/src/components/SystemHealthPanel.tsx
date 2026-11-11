import { useEffect, useState } from "react";
import type { HealthResponse, OllamaMemorySnapshot } from "../lib/types";
import { api } from "../lib/api";

interface Props {
  t: (k: string) => string;
  ramModel: { model: string; tier: string } | null;
  className?: string;
}

export function SystemHealthPanel({ t, ramModel, className }: Props) {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [ollama, setOllama] = useState<OllamaMemorySnapshot | null>(null);

  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        const [h, o] = await Promise.all([
          api<HealthResponse>("/api/health"),
          api<OllamaMemorySnapshot>("/api/ollama/memory"),
        ]);
        if (!cancelled) { setHealth(h); setOllama(o); }
      } catch { /* keep stale */ }
    };
    tick();
    const id = setInterval(tick, 15000);
    return () => { cancelled = true; clearInterval(id); };
  }, []);

  const ramFree = ollama ? `${ollama.free_gb.toFixed(2)} GB` : "—";
  const ramLow = ollama ? ollama.free_gb < 2 : false;

  return (
    <div className={className}>
      <div className="rs-card">
        <div className="rs-card-title">{t("dashboardSystem")}</div>
        <div className="rs-card-value">
          {health ? (
            <>
              <span className={`rs-status-dot ${health.status === "ok" ? "ok" : "warn"}`}></span>
              {health.status === "ok" ? t("dashboardHealthy") : `Status: ${health.status}`}
            </>
          ) : (
            <span className="rs-status-dot warn"></span>
          )}
        </div>
        {health && (
          <div style={{ marginTop: 6, fontSize: 12, color: "var(--text-dim)" }}>
            {health.database === "ok" ? t("dashboardDbOk") : t("dashboardDbError")}
          </div>
        )}
        {health && (
          <div style={{ marginTop: 4, fontSize: 11, color: "var(--text-dim)" }}>
            {t("dashboardVersion").replace("{version}", health.version).replace("{env}", health.env)}
          </div>
        )}
      </div>

      {ollama && (
        <div className="rs-card">
          <div className="rs-card-title">{t("ramIndicator").replace("{free}", "…")}</div>
          <div className="rs-card-value">{ramFree} free</div>
          {ramModel && (
            <div style={{ marginTop: 6, fontSize: 11, color: "var(--accent)" }}>
              {t("ramTier").replace("{tier}", ramModel.tier)}: {ramModel.model}
            </div>
          )}
          {ramLow && (
            <div style={{ marginTop: 6, fontSize: 11, color: "var(--warn)" }}>
              {t("ramWarning")}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
