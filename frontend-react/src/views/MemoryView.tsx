import { useEffect, useState } from "react";
import { api } from "../lib/api";
import type { MemoryItem, SettingsOut } from "../lib/types";

export function MemoryView({
  t, onToast,
}: {
  t: (k: string) => string;
  onToast: (m: string, e?: boolean) => void;
}) {
  const [items, setItems] = useState<MemoryItem[]>([]);
  const [enabled, setEnabled] = useState(true);
  const [draft, setDraft] = useState("");
  const [retentionDays, setRetentionDays] = useState<number | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editDraft, setEditDraft] = useState("");

  const load = async () => {
    try {
      const [memories, me] = await Promise.all([
        api<MemoryItem[]>("/api/memory"),
        api<SettingsOut>("/api/settings"),
      ]);
      setItems(memories);
      setEnabled(me.memory_enabled !== false);
      setRetentionDays(me.memory_retention_days ?? null);
    } catch (err) {
      onToast((err as Error).message, true);
    }
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const label = (prefix: string, raw: string) => {
    const key =
      prefix + raw.charAt(0).toUpperCase() + raw.slice(1).replace(/_([a-z])/g,
        (_, c: string) => c.toUpperCase());
    const value = t(key);
    return value === key ? raw : value;
  };

  return (
    <>
      <div className="view-header">
        <h2>{t("memoryTitle")}</h2>
        <button
          className="btn small"
          onClick={async () => {
            try {
              const memories = await api<MemoryItem[]>("/api/memory/export");
              const blob = new Blob([JSON.stringify(memories, null, 2)], {
                type: "application/json",
              });
              const url = URL.createObjectURL(blob);
              const link = document.createElement("a");
              link.href = url;
              link.download = "jalora-memories.json";
              link.click();
              URL.revokeObjectURL(url);
            } catch (err) {
              onToast((err as Error).message, true);
            }
          }}
        >
          {t("memoryExport")}
        </button>
      </div>
      <div className="panel">
        <label className="toggle-row">
          <input
            type="checkbox"
            checked={enabled}
            onChange={async (e) => {
              const next = e.target.checked;
              setEnabled(next);
              try {
                await api("/api/memory/settings", {
                  method: "PUT",
                  body: { memory_enabled: next },
                });
                onToast(next ? t("memoryEnabledOn") : t("memoryEnabledOff"));
              } catch (err) {
                setEnabled(!next);
                onToast((err as Error).message, true);
              }
            }}
          />
          <span>{t("memoryEnabled")}</span>
        </label>
        <label className="toggle-row">
          <span>{t("memoryRetention")}</span>
          <select
            value={retentionDays ?? ""}
            onChange={async (e) => {
              const previous = retentionDays;
              const next = e.target.value === "" ? null : Number(e.target.value);
              setRetentionDays(next);
              try {
                const updated = await api<{ memory_retention_days: number | null }>(
                  "/api/memory/settings",
                  {
                    method: "PUT",
                    body: { memory_enabled: enabled, memory_retention_days: next },
                  },
                );
                setRetentionDays(updated.memory_retention_days);
              } catch (err) {
                setRetentionDays(previous);
                onToast((err as Error).message, true);
              }
            }}
          >
            <option value="">{t("memoryNeverExpires")}</option>
            {[30, 90, 365, 3650].map((days) => (
              <option key={days} value={days}>{days} {t("memoryDays")}</option>
            ))}
          </select>
        </label>

        <form
          className="row gap"
          onSubmit={async (e) => {
            e.preventDefault();
            const content = draft.trim();
            if (!content) return;
            try {
              await api("/api/memory", { method: "POST", body: { content, kind: "fact" } });
              setDraft("");
              load();
            } catch (err) {
              onToast((err as Error).message, true);
            }
          }}
        >
          <input
            type="text"
            placeholder={t("addMemory")}
            value={draft}
            disabled={!enabled}
            onChange={(e) => setDraft(e.target.value)}
          />
          <button type="submit" className="btn primary" disabled={!enabled}>
            {t("add")}
          </button>
        </form>

        <div className="item-list">
          {items.length === 0 && <div className="activity-empty">{t("memoryEmpty")}</div>}
          {items.map((m) => (
            <div key={m.id} className="item">
              <div className="content">
                {editingId === m.id ? (
                  <input
                    value={editDraft}
                    onChange={(e) => setEditDraft(e.target.value)}
                    aria-label={t("memoryCorrect")}
                  />
                ) : (
                  <div className="primary-text">{m.content}</div>
                )}
                <div className="meta provenance">
                  <span className="badge">{label("kind", m.kind)}</span>
                  <span className="badge">{label("src", m.source)}</span>
                  <span className="provenance-date">
                    {new Date(m.created_at).toLocaleString()}
                  </span>
                </div>
              </div>
              <div className="row gap">
                {editingId === m.id ? (
                  <>
                    <button
                      className="btn small primary"
                      disabled={!enabled || !editDraft.trim()}
                      onClick={async () => {
                        try {
                          await api(`/api/memory/${m.id}`, {
                            method: "PUT",
                            body: { content: editDraft },
                          });
                          setEditingId(null);
                          await load();
                        } catch (err) {
                          onToast((err as Error).message, true);
                        }
                      }}
                    >
                      {t("memorySave")}
                    </button>
                    <button className="btn small" onClick={() => setEditingId(null)}>
                      {t("memoryCancel")}
                    </button>
                  </>
                ) : (
                  <>
                    <button
                      className="btn small"
                      disabled={!enabled}
                      onClick={() => {
                        setEditingId(m.id);
                        setEditDraft(m.content);
                      }}
                    >
                      {t("memoryCorrect")}
                    </button>
                    <button
                      className="btn small danger"
                      onClick={async () => {
                        try {
                          await api(`/api/memory/${m.id}`, { method: "DELETE" });
                          load();
                        } catch (err) {
                          onToast((err as Error).message, true);
                        }
                      }}
                    >
                      {t("delete")}
                    </button>
                  </>
                )}
              </div>
            </div>
          ))}
        </div>
      </div>
    </>
  );
}
