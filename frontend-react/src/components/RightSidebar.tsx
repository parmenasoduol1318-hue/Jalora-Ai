import { useState } from "react";
import { SystemHealthPanel } from "./SystemHealthPanel";
import { TodayTasksPanel } from "./TodayTasksPanel";
import { IntegrationsPanel } from "./IntegrationsPanel";

export type RightPanel = "system" | "tasks" | "integrations";

interface Props {
  t: (k: string) => string;
  ramModel: { model: string; tier: string } | null;
  open: boolean;
  onToggle: (open: boolean) => void;
}

export function RightSidebar({ t, ramModel, open, onToggle }: Props) {
  const [panel, setPanel] = useState<RightPanel>("integrations");

  const tabs: { id: RightPanel; label: string }[] = [
    { id: "integrations", label: t("rightPanelIntegrations") },
    { id: "tasks", label: t("rightPanelTasks") },
    { id: "system", label: t("rightPanelSystem") },
  ];

  return (
    <aside id="context-sidebar" className={`right-sidebar ${open ? "open" : ""}`}>
      <div className="rs-header">
        <span className="rs-title">{t("rightSidebar")}</span>
        <div style={{ display: "flex", gap: 2, background: "var(--bg-3)", borderRadius: 6, padding: 2 }}>
          {tabs.map((tab) => (
            <button
              key={tab.id}
              className={`mode-btn ${panel === tab.id ? "active" : ""}`}
              style={{ fontSize: 11, padding: "4px 8px" }}
              onClick={() => setPanel(tab.id)}
            >
              {tab.label}
            </button>
          ))}
        </div>
        <button
          className="icon-btn"
          title={t("menu")}
          onClick={() => onToggle(!open)}
          style={{ fontSize: 14 }}
        >
          {open ? "›" : "‹"}
        </button>
      </div>
      <div className="rs-scroll">
        <div className="rs-panel active">
          {panel === "integrations" && <IntegrationsPanel t={t} />}
          {panel === "tasks" && <TodayTasksPanel t={t} />}
          {panel === "system" && <SystemHealthPanel t={t} ramModel={ramModel} />}
        </div>
      </div>
    </aside>
  );
}
