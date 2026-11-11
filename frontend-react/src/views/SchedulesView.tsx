import { useCallback, useEffect, useState } from "react";
import type { FormEvent } from "react";
import { api } from "../lib/api";
import type { ScheduleT } from "../lib/types";

function describe(s: ScheduleT, t: (k: string) => string): string {
  const spec = s.spec ?? {};
  if (s.kind === "once") return t("scheduleKindOnce");
  if (s.kind === "interval") return t("scheduleKindInterval");
  const pad2 = (n: unknown) => String(n).padStart(2, "0");
  const min = spec.minute !== undefined ? `:${String(spec.minute).padStart(2, "0")}` : "";
  const tpart = `${pad2(spec.hour ?? 0)}${min}`;
  const dow = spec.day_of_week;
  if (dow === "mon-fri") return `${tpart} · Mon–Fri`;
  if (dow === "sat,sun") return `${tpart} · Sat–Sun`;
  return `${tpart} · ${t("scheduleKindCron")}`;
}

export function SchedulesView({
  t,
  onToast,
  onOpenTask,
}: {
  t: (k: string) => string;
  onToast: (m: string, e?: boolean) => void;
  onOpenTask: (taskId: string) => void;
}) {
  const [rows, setRows] = useState<ScheduleT[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [retrying, setRetrying] = useState(false);
  const [showNew, setShowNew] = useState(false);
  const [when, setWhen] = useState("");
  const [task, setTask] = useState("");
  const [timezone, setTimezone] = useState(
    () => Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
  );
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editWhen, setEditWhen] = useState("");
  const [editTask, setEditTask] = useState("");
  const [editTimezone, setEditTimezone] = useState("UTC");
  const [editReminder, setEditReminder] = useState(false);
  const [reminder, setReminder] = useState(false);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setLoadError(null);
    let attempts = 0;
    const attempt = async () => {
      try {
        setRows(await api<ScheduleT[]>("/api/schedules"));
      } catch (err) {
        const e = err as { message: string; status?: number };
        onToast(e.message, true);
        if (e.status === 429 && attempts < 4) {
          // Transient 429 (only possible on a write-heavy shared IP). Back off
          // and retry automatically — the GET /api/schedules read itself is on
          // the rate-limit whitelist, so a 429 here is rare but recoverable.
          attempts += 1;
          setRetrying(true);
          setLoadError(e.message || t("recentLoadError"));
          window.setTimeout(
            () => { setRetrying(false); attempt(); },
            Math.min(5000 * attempts, 30000),
          );
        } else {
          setRetrying(false);
          setLoadError(e.message || t("recentLoadError"));
        }
      }
    };
    await attempt();
  }, [onToast, t]);

  useEffect(() => {
    load();
  }, [load]);

  const toggle = async (s: ScheduleT) => {
    try {
      await api(`/api/schedules/${s.id}/toggle`, {
        method: "POST",
        body: { enabled: !s.enabled },
      });
      load();
    } catch (err) {
      onToast((err as Error).message, true);
    }
  };

  const remove = async (s: ScheduleT) => {
    try {
      await api(`/api/schedules/${s.id}`, { method: "DELETE" });
      onToast(t("scheduleDeleted"));
      load();
    } catch (err) {
      onToast((err as Error).message, true);
    }
  };

  const create = async (e: FormEvent) => {
    e.preventDefault();
    if (!when.trim() || !task.trim()) return;
    setBusy(true);
    try {
      await api("/api/schedules", {
        method: "POST",
        body: {
          when: when.trim(),
          task_template: task.trim(),
          notify_only: reminder,
          timezone,
        },
      });
      onToast(t("scheduleCreated"));
      setWhen("");
      setTask("");
      setReminder(false);
      setShowNew(false);
      load();
    } catch (err) {
      onToast((err as Error).message, true);
    } finally {
      setBusy(false);
    }
  };

  const startEditing = (schedule: ScheduleT) => {
    setEditingId(schedule.id);
    setEditWhen(schedule.schedule_expression || "");
    setEditTask(schedule.task_template || "");
    setEditTimezone(schedule.timezone || "UTC");
    setEditReminder(schedule.notify_only);
  };

  const saveEdit = async (e: FormEvent) => {
    e.preventDefault();
    if (!editingId || !editWhen.trim() || !editTask.trim() || !editTimezone.trim()) return;
    setBusy(true);
    try {
      await api(`/api/schedules/${editingId}`, {
        method: "PUT",
        body: {
          when: editWhen.trim(),
          task_template: editTask.trim(),
          notify_only: editReminder,
          timezone: editTimezone.trim(),
        },
      });
      onToast(t("scheduleUpdated"));
      setEditingId(null);
      load();
    } catch (err) {
      onToast((err as Error).message, true);
    } finally {
      setBusy(false);
    }
  };

  const occurrenceStatusKey = (status: string) => ({
    completed: "stCompleted",
    awaiting_approval: "stWaiting",
    partial: "stPartial",
    failed: "stFailed",
    cancelled: "stCancelled",
    unknown: "scheduleUnknown",
    missed: "scheduleMissed",
    reminder_sent: "scheduleReminderSent",
  }[status] || status.replaceAll("_", " "));

  return (
    <>
      <div className="view-header">
        <h2>{t("schedulesNav")}</h2>
        <button className="btn small" onClick={() => setShowNew((s) => !s)}>
          {t("scheduleNew")}
        </button>
      </div>

      {showNew && (
        <form className="card schedule-form" onSubmit={create}>
          <label>{t("scheduleWhen")}</label>
          <input value={when} onChange={(e) => setWhen(e.target.value)} placeholder="every Monday at 08:00" />
          <label>{t("scheduleTimezone")}</label>
          <input value={timezone} onChange={(e) => setTimezone(e.target.value)} placeholder="Africa/Nairobi" />
          <label>{t("scheduleTask")}</label>
          <textarea value={task} onChange={(e) => setTask(e.target.value)} rows={2} />
          <label className="check">
            <input
              type="checkbox"
              checked={reminder}
              onChange={(e) => setReminder(e.target.checked)}
            />
            {t("scheduleReminderOnly")}
          </label>
          <button className="btn primary" disabled={busy || !when.trim() || !task.trim()}>
            {t("scheduleCreate")}
          </button>
        </form>
      )}

      <div className="task-list">
        {!rows && !loadError && <div className="activity-empty">…</div>}
        {loadError && !rows && (
          <div className="card schedule-error">
            <div className="schedule-error-msg">{loadError}</div>
            <button className="btn small" disabled={retrying} onClick={load}>
              {retrying ? t("retrying") : t("retry")}
            </button>
          </div>
        )}
        {rows && !loadError && rows.length === 0 && (
          <div className="activity-empty">{t("schedulesEmpty")}</div>
        )}
        {rows?.map((s) => (
          <div key={s.id} className="task-card">
            <div className="row between">
              <div className="task-goal">
                <span className={`badge ${s.enabled ? "ok" : ""}`}>
                  {t(s.enabled ? "scheduleEnabled" : "schedulePaused")}
                </span>{" "}
                {s.task_template || t("scheduleReminderKind")}
              </div>
              <div className="row">
                <button className="btn small" onClick={() => startEditing(s)}>
                  {t("scheduleEdit")}
                </button>
                <button className="btn small" onClick={() => toggle(s)}>
                  {t("scheduleToggle")}
                </button>
                <button className="btn small danger" onClick={() => remove(s)}>
                  {t("delete")}
                </button>
              </div>
            </div>
            <div className="meta">
              {s.notify_only ? t("scheduleReminderKind") : t("scheduleTaskKind")} · {describe(s, t)}
            </div>
            <div className="meta">
              {t("scheduleNext")}:{" "}
              {s.next_run ? new Date(s.next_run).toLocaleString() : t("schedulePaused")}
              {s.last_run ? ` · ${t("scheduleLast")}: ${new Date(s.last_run).toLocaleString()}` : ""}
            </div>
            {s.latest_occurrence && (
              <div className="meta schedule-occurrence">
                {t("scheduleLastOutcome")}:{" "}
                {t(occurrenceStatusKey(s.latest_occurrence.status))} ·{" "}
                {new Date(s.latest_occurrence.scheduled_for).toLocaleString()}
                {s.latest_occurrence.result_summary
                  ? ` — ${s.latest_occurrence.result_summary}`
                  : ""}
                {s.latest_occurrence.task_id && (
                  <button
                    className="btn small"
                    onClick={() => onOpenTask(s.latest_occurrence!.task_id!)}
                  >
                    {t("scheduleOpenTask")}
                  </button>
                )}
              </div>
            )}
            {editingId === s.id && (
              <form className="schedule-form" onSubmit={saveEdit}>
                <label>{t("scheduleWhen")}</label>
                <input value={editWhen} onChange={(e) => setEditWhen(e.target.value)} />
                <label>{t("scheduleTimezone")}</label>
                <input value={editTimezone} onChange={(e) => setEditTimezone(e.target.value)} />
                <label>{t("scheduleTask")}</label>
                <textarea value={editTask} onChange={(e) => setEditTask(e.target.value)} rows={2} />
                <label className="check">
                  <input
                    type="checkbox"
                    checked={editReminder}
                    onChange={(e) => setEditReminder(e.target.checked)}
                  />
                  {t("scheduleReminderOnly")}
                </label>
                <div className="row">
                  <button className="btn primary" disabled={busy}>
                    {t("scheduleSave")}
                  </button>
                  <button className="btn small" type="button" onClick={() => setEditingId(null)}>
                    {t("scheduleCancelEdit")}
                  </button>
                </div>
              </form>
            )}
          </div>
        ))}
      </div>
    </>
  );
}