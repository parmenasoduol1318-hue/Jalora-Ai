import { useEffect, useState } from "react";
import { api } from "../lib/api";
import type { ActivityEntry } from "../lib/types";

export function ActivityView({
  t, onToast, onStopAll,
}: {
  t: (k: string) => string;
  onToast: (m: string, e?: boolean) => void;
  onStopAll: () => void;
}) {
  const [entries, setEntries] = useState<ActivityEntry[]>([]);

  useEffect(() => {
    (async () => {
      try {
        setEntries(await api<ActivityEntry[]>("/api/activity?limit=150"));
      } catch (err) {
        onToast((err as Error).message, true);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <>
      <div className="view-header">
        <h2>{t("activityTitle")}</h2>
        <button className="btn danger small" onClick={onStopAll}>{t("stopAll")}</button>
      </div>
      <div className="activity-list">
        {entries.length === 0 && (
          <div className="activity-empty">{t("activityEmpty")}</div>
        )}
        {entries.map((a, i) => (
          <div key={a.id || i} className="activity-item">
            <span className="icon">
              {a.approval_status === "pending" ? "⚠" : a.success ? "✓" : "✗"}
            </span>
            <div className="details">
              <div>
                <strong>
                  {a.kind === "preference"
                    ? t("preferenceLearned")
                    : a.kind === "feedback"
                    ? t("feedbackTitle")
                    : a.tool || a.agent}
                </strong>{" "}—{" "}
                {(a.content || a.result_summary || "").slice(0, 180)}
              </div>
              <div className="sub">
                {new Date(a.created_at).toLocaleString()} · {a.duration_ms} ms
                {a.approval_status ? ` · approval: ${a.approval_status}` : ""}
                {(a.meta?.usage as { total_tokens?: number } | undefined)?.total_tokens
                  ? ` · ${(a.meta!.usage as { total_tokens: number }).total_tokens} tokens`
                  : ""}
                {a.kind === "preference" && ` · ${t("preferenceLearned")}`}
              </div>
            </div>
          </div>
        ))}
      </div>
    </>
  );
}
