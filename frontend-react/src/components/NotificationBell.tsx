import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import type { NotificationT } from "../lib/types";

const KIND_KEY: Record<string, string> = {
  reminder: "notificationReminder",
  scheduled_task_completed: "notificationScheduledCompleted",
  scheduled_task_failed: "notificationScheduledFailed",
  approval_required: "notificationApprovalRequired",
  inbound_email: "notificationInboundEmail",
  inbound_comms: "notificationInboundComms",
};

export function NotificationBell({
  t, onToast, onOpenNotification,
}: {
  t: (k: string) => string;
  onToast: (message: string, error?: boolean) => void;
  onOpenNotification: (notification: NotificationT) => void;
}) {
  const [count, setCount] = useState(0);
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<NotificationT[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState(false);
  const nodeRef = useRef<HTMLDivElement | null>(null);
  const prevCount = useRef(0);
  const permissionAsked = useRef(false);

  const loadItems = useCallback(async () => {
    setLoading(true);
    try {
      setItems(await api<NotificationT[]>("/api/notifications?limit=20"));
      setLoadError(false);
    } catch (err) {
      setItems(null);
      setLoadError(true);
      onToast((err as Error).message, true);
    } finally {
      setLoading(false);
    }
  }, [onToast]);

  const fireBrowser = useCallback(
    async (title: string) => {
      if (typeof Notification === "undefined" || Notification.permission !== "granted") return;
      try {
        const latest = await api<NotificationT[]>("/api/notifications?limit=1");
        const item = latest[0];
        if (!item) return;
        const n = new Notification(title, { body: item.text, tag: item.id });
        n.onclick = () => {
          window.focus();
          setOpen(true);
          loadItems();
        };
      } catch {
        /* OS notification is best-effort */
      }
    },
    [loadItems],
  );

  const refreshCount = useCallback(async () => {
    try {
      const r = await api<{ count: number }>("/api/notifications/unread-count");
      if (r.count > prevCount.current) fireBrowser(t("notifications"));
      prevCount.current = r.count;
      setCount(r.count);
    } catch {
      /* bell is non-critical */
    }
  }, [fireBrowser, t]);

  useEffect(() => {
    refreshCount();
    const timer = window.setInterval(refreshCount, 20000);
    return () => window.clearInterval(timer);
  }, [refreshCount]);

  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (nodeRef.current && !nodeRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  const requestPermission = async () => {
    if (permissionAsked.current) return;
    permissionAsked.current = true;
    if (
      typeof Notification === "undefined" ||
      Notification.permission === "granted" ||
      Notification.permission === "denied"
    ) {
      return;
    }
    try {
      await Notification.requestPermission();
    } catch {
      /* permission request is best-effort */
    }
  };

  const openPanel = async () => {
    if (!open) {
      requestPermission();
      loadItems();
    }
    setOpen((o) => !o);
  };

  const markRead = async (n: NotificationT) => {
    if (n.read) return;
    try {
      await api(`/api/notifications/${n.id}/read`, { method: "POST" });
      setItems((prev) => prev?.map((x) => (x.id === n.id ? { ...x, read: true } : x)) ?? prev);
      refreshCount();
    } catch (err) {
      onToast((err as Error).message, true);
    }
  };

  const markAll = async () => {
    try {
      await api("/api/notifications/read-all", { method: "POST" });
      setItems((prev) => prev?.map((x) => ({ ...x, read: true })) ?? prev);
      refreshCount();
    } catch (err) {
      onToast((err as Error).message, true);
    }
  };

  return (
    <div className="bell" ref={nodeRef}>
      <button
        id="btn-notifications"
        className="bell-btn"
        onClick={openPanel}
        aria-label={t("notifications")}
        aria-expanded={open}
        aria-controls="notification-panel"
      >
        🔔
        {count > 0 && <span className="bell-badge">{count > 99 ? "99+" : count}</span>}
      </button>
      {open && (
        <div className="bell-panel" id="notification-panel" aria-label={t("notifications")}>
          <div className="bell-head">
            <span>{t("notifications")}</span>
            <button className="link" onClick={markAll} disabled={count === 0}>
              {t("markAllRead")}
            </button>
          </div>
          <div className="bell-list">
            {loading && <div className="activity-empty">…</div>}
            {!loading && loadError && (
              <div className="activity-empty" role="alert">{t("notificationsUnavailable")}</div>
            )}
            {!loading && !loadError && (!items || items.length === 0) && (
              <div className="activity-empty">{t("noNotifications")}</div>
            )}
            {items?.map((n) => (
              <button
                type="button"
                key={n.id}
                className={`bell-item ${n.read ? "" : "unread"}`}
                onClick={() => {
                  void markRead(n);
                  setOpen(false);
                  onOpenNotification(n);
                }}
                aria-label={`${t(KIND_KEY[n.kind] || "notificationSystem")}: ${n.text}`}
              >
                <span className={`bell-kind ${n.kind}`}>
                  {t(KIND_KEY[n.kind] || "notificationSystem")}
                </span>
                <span className="bell-text">{n.text}</span>
                <span className="bell-when">
                  {n.created_at ? new Date(n.created_at).toLocaleString() : ""}
                </span>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}