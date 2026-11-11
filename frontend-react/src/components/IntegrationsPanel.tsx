import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { ProviderLogo } from "../components/ProviderLogos";
import type { ProviderCard } from "../lib/types";

interface Props {
  t: (k: string) => string;
  className?: string;
}

/* Right-sidebar integrations card. Reads the SAME source as the Integrations
   page (the /api/integrations/providers registry) so the two can never show
   conflicting status for calendar/search/telnyx/twilio/etc. */
const SHOW = ["gmail", "calendar", "drive", "telnyx", "twilio", "search", "ollama"];

export function IntegrationsPanel({ t, className }: Props) {
  const [cards, setCards] = useState<Record<string, ProviderCard>>({});

  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        const data = await api<{ providers: ProviderCard[] }>("/api/integrations/providers");
        if (!cancelled) {
          const map: Record<string, ProviderCard> = {};
          for (const c of data.providers || []) map[c.name] = c;
          setCards(map);
        }
      } catch { /* keep stale */ }
    };
    tick();
    const id = setInterval(tick, 20000);
    return () => { cancelled = true; clearInterval(id); };
  }, []);

  return (
    <div className={className}>
      <div className="rs-card">
        <div className="rs-card-title">{t("rightPanelIntegrations")}</div>
        {SHOW.map((key) => {
          const c = cards[key];
          const status = !c ? "not configured"
            : c.connected ? "connected"
            : c.configured ? "not configured"
            : "not configured";
          const color =
            status === "connected" ? "var(--ok)"
            : "var(--text-dim)";
          return (
            <div className="rs-provider-row" key={key}>
              <span className="rs-provider-name">
                <ProviderLogo id={c?.logo ?? key} className="rs-logo" />
                {c?.name ? c.name.charAt(0).toUpperCase() + c.name.slice(1) : key}
              </span>
              <span className="rs-provider-status" style={{ color }}>{status}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
