import { useState } from "react";
import type { Conversation, Task } from "../lib/types";

interface Props {
  t: (k: string) => string;
  conversations: Conversation[];
  tasks: Task[];
  convError: boolean;
  activeId: string | null;
  onLoad: () => void;
  onOpenConversation: (c: Conversation) => void;
  onNewChat: () => void;
  onToast: (msg: string, error?: boolean) => void;
}

export function RecentsView({
  t, conversations, tasks, convError, activeId, onLoad,
  onOpenConversation, onNewChat, onToast,
}: Props) {
  const [query, setQuery] = useState("");
  const recentTasks = tasks.filter(
    (tk) => tk.status === "running" || tk.status === "queued"
  );

  const handleDelete = async (c: Conversation) => {
    try {
      const { api } = await import("../lib/api");
      await api(`/api/conversations/${c.id}`, { method: "DELETE" });
      if (activeId === c.id) onNewChat();
      onLoad();
    } catch (err) {
      onToast((err as Error).message, true);
    }
  };

  const handlePatch = async (c: Conversation, patch: { title?: string; archived?: boolean }) => {
    try {
      const { api } = await import("../lib/api");
      await api(`/api/conversations/${c.id}`, { method: "PATCH", body: patch });
      onLoad();
    } catch (err) {
      onToast((err as Error).message, true);
    }
  };

  const visibleConversations = conversations.filter((c) =>
    !query.trim() || c.title.toLowerCase().includes(query.trim().toLowerCase())
  );

  return (
    <div className="recents-view">
      <div className="view-header">
        <h2>{t("recentTitle")}</h2>
        <button className="btn small" id="btn-refresh-recents" onClick={() => { onLoad(); }}>
          {t("refresh")}
        </button>
      </div>
      <input
        className="search-input"
        aria-label="Search conversations"
        placeholder="Search conversations…"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
      />
      <div className="recents-scroll">
        <h3 className="recents-section-title">{t("chats")}</h3>
        {convError && (
          <div className="conv-empty">
            {t("recentLoadError")}{" "}
            <button className="link" onClick={onLoad}>{t("retry")}</button>
          </div>
        )}
        {!convError && conversations.length === 0 && (
          <div className="conv-empty">{t("noRecent")}</div>
        )}
        {!convError && (
          <div className="conv-list">
            {visibleConversations.map((c) => (
              <div
                key={c.id}
                className={`conv-item ${activeId === c.id ? "active" : ""}`}
              >
                <span
                  className="title"
                  onClick={() => onOpenConversation(c)}
                  role="button"
                  tabIndex={0}
                  onKeyDown={(e) => e.key === "Enter" && onOpenConversation(c)}
                >
                  {c.title}
                </span>
                <button
                  className="del"
                  title={t("delete")}
                  onClick={async (e) => {
                    e.stopPropagation();
                    await handleDelete(c);
                  }}
                >
                  ✕
                </button>
                <button
                  className="del"
                  title="Rename"
                  aria-label={`Rename ${c.title}`}
                  onClick={(e) => {
                    e.stopPropagation();
                    const title = window.prompt("Conversation title", c.title)?.trim();
                    if (title && title !== c.title) void handlePatch(c, { title });
                  }}
                >
                  ✎
                </button>
                <button
                  className="del"
                  title="Archive"
                  aria-label={`Archive ${c.title}`}
                  onClick={(e) => { e.stopPropagation(); void handlePatch(c, { archived: true }); }}
                >
                  ▣
                </button>
              </div>
            ))}
          </div>
        )}

        <h3 className="recents-section-title" style={{ marginTop: 24 }}>{t("tasks")}</h3>
        {recentTasks.length === 0 ? (
          <div className="conv-empty">{t("noRecentTasks")}</div>
        ) : (
          <div className="conv-list">
            {recentTasks.map((tk) => (
              <div
                key={tk.id}
                className="conv-item"
                role="button"
                tabIndex={0}
                onClick={() => {
                  onOpenConversation({ id: "", title: "", project: null });
                  setTimeout(() => {
                    const ev = new Event("jalora-open-task");
                    (window as any).dispatchEvent?.(ev);
                  }, 0);
                }}
              >
                <span className="title">{tk.goal || `#${tk.id.slice(0, 6)}`}</span>
                <span
                  className="rs-provider-status"
                  style={{
                    color: tk.status === "running" ? "var(--accent)" : "var(--ok)",
                  }}
                >
                  {tk.status}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
