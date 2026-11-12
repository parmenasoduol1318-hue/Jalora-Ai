import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import type { AppSettings, NotificationT } from "../lib/types";

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
  const [notificationPrefs, setNotificationPrefs] = useState({
    notifications_enabled: false,
    notification_sound: false,
  });
  const nodeRef = useRef<HTMLDivElement | null>(null);
  const prevCount = useRef(-1);

  const loadNotificationPrefs = useCallback(async () => {
    try {
      const prefs = await api<AppSettings>("/api/settings");
      setNotificationPrefs({
        notifications_enabled: prefs.notifications_enabled,
        notification_sound: prefs.notification_sound,
      });
    } catch (err) {
      onToast((err as Error).message, true);
    }
  }, [onToast]);

  useEffect(() => {
    void loadNotificationPrefs();
    window.addEventListener("jalora:settings-changed", loadNotificationPrefs);
    return () => {
      window.removeEventListener("jalora:settings-changed", loadNotificationPrefs);
    };
  }, [loadNotificationPrefs]);

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
      if (
        !notificationPrefs.notifications_enabled
        || typeof Notification === "undefined"
        || Notification.permission !== "granted"
      ) return;
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
        console.warn("Could not display the desktop notification.");
      }
    },
    [loadItems, notificationPrefs.notifications_enabled],
  );

  const playSound = useCallback(() => {
    if (!notificationPrefs.notification_sound || typeof window.AudioContext === "undefined") {
      return;
    }
    try {
      const context = new window.AudioContext();
      const oscillator = context.createOscillator();
      const gain = context.createGain();
      oscillator.type = "sine";
      oscillator.frequency.value = 880;
      gain.gain.setValueAtTime(0.08, context.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.001, context.currentTime + 0.16);
      oscillator.connect(gain);
      gain.connect(context.destination);
      oscillator.onended = () => {
        void context.close().catch((err: unknown) => {
          console.warn("Could not close the notification audio context.", err);
        });
      };
      void context.resume().then(() => {
        oscillator.start();
        oscillator.stop(context.currentTime + 0.16);
      }).catch((err: unknown) => {
        console.warn("Could not play the notification sound.", err);
        void context.close().catch((closeErr: unknown) => {
          console.warn("Could not close the notification audio context.", closeErr);
        });
      });
    } catch (err) {
      console.warn("Could not initialize the notification sound.", err);
    }
  }, [notificationPrefs.notification_sound]);

  const refreshCount = useCallback(async () => {
    try {
      const r = await api<{ count: number }>("/api/notifications/unread-count");
      if (prevCount.current < 0) {
        prevCount.current = r.count;
      } else if (r.count > prevCount.current) {
        fireBrowser(t("notifications"));
        playSound();
      }
      prevCount.current = r.count;
      setCount(r.count);
    } catch {
      /* bell is non-critical */
    }
  }, [fireBrowser, playSound, t]);

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

  const openPanel = async () => {
    if (!open) {
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