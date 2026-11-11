import { useCallback, useEffect, useMemo, useState } from "react";
import type { FormEvent } from "react";
import { api } from "../lib/api";
import { resolveTimeZone } from "../lib/timezone";
import { ProviderLogo } from "../components/ProviderLogos";
import type { CalendarEventT, IntegrationItem, ProviderCard } from "../lib/types";

type ConnectResult = {
  status: string;
  provider: string;
  integration_id?: string;
  authorization_url?: string;
};

/* Backend datetimes are naive UTC; JS parses bare strings as *local* time, so
   an explicit Z is appended when no timezone is present. */
function toDate(value?: string | null): Date | null {
  if (!value) return null;
  const hasTz = /[zZ]$|[+-]\d{2}:?\d{2}$/.test(value);
  const d = new Date(hasTz ? value : `${value}Z`);
  return Number.isNaN(d.getTime()) ? null : d;
}

function dayKey(d: Date, timeZone: string): string {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone, year: "numeric", month: "2-digit", day: "2-digit",
  }).formatToParts(d).reduce<Record<string, string>>((acc, part) => {
    if (part.type !== "literal") acc[part.type] = part.value;
    return acc;
  }, {});
  return `${parts.year}-${parts.month}-${parts.day}`;
}

function startOfMonth(d: Date): Date {
  return new Date(d.getFullYear(), d.getMonth(), 1);
}

function addDays(d: Date, n: number): Date {
  const x = new Date(d);
  x.setDate(x.getDate() + n);
  return x;
}

function sameDay(a: Date, b: Date, timeZone: string): boolean {
  return dayKey(a, timeZone) === dayKey(b, timeZone);
}

const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

export function CalendarView({
  t,
  onToast,
}: {
  t: (k: string) => string;
  onToast: (m: string, e?: boolean) => void;
}) {
  const [events, setEvents] = useState<CalendarEventT[]>([]);
  const [cursor, setCursor] = useState<Date>(() => startOfMonth(new Date()));
  const [calendarRow, setCalendarRow] = useState<IntegrationItem | null>(null);
  const [calendarCard, setCalendarCard] = useState<ProviderCard | null>(null);
  const [busy, setBusy] = useState(false);
  const [showNew, setShowNew] = useState(false);
  const [title, setTitle] = useState("");
  const [startAt, setStartAt] = useState("");
  const [endAt, setEndAt] = useState("");
  const [location, setLocation] = useState("");
  const [description, setDescription] = useState("");
  const [attendees, setAttendees] = useState("");
  const [timeZone, setTimeZone] = useState("Africa/Nairobi");
  const displayTimeZone = resolveTimeZone(timeZone);

  const gridStart = useMemo(() => {
    const first = startOfMonth(cursor);
    return addDays(first, -first.getDay());
  }, [cursor]);

  const load = useCallback(async () => {
    const rangeStart = gridStart;
    const rangeEnd = addDays(gridStart, 42);
    try {
      const [calendar, integrations, registry, settings] = await Promise.all([
        api<{ events: CalendarEventT[] }>(
          `/api/calendar?start=${encodeURIComponent(rangeStart.toISOString())}` +
            `&end=${encodeURIComponent(rangeEnd.toISOString())}&limit=500`
        ),
        api<IntegrationItem[]>("/api/integrations"),
        api<{ providers: ProviderCard[] }>("/api/integrations/providers"),
        api<{ timezone?: string }>("/api/settings"),
      ]);
      setEvents(calendar.events || []);
      setCalendarRow(integrations.find((i) => i.provider === "calendar") ?? null);
      setCalendarCard(
        (registry.providers || []).find((c) => c.name === "calendar") ?? null
      );
      if (settings.timezone) setTimeZone(settings.timezone);
    } catch (err) {
      onToast((err as Error).message || t("calError"), true);
    }
  }, [gridStart, onToast, t]);

  useEffect(() => {
    load();
  }, [load]);

  const byDay = useMemo(() => {
    const map = new Map<string, CalendarEventT[]>();
    for (const ev of events) {
      const d = toDate(ev.start);
      if (!d) continue;
      const key = dayKey(d, displayTimeZone);
      const list = map.get(key) ?? [];
      list.push(ev);
      map.set(key, list);
    }
    for (const list of map.values()) {
      list.sort(
        (a, b) => (toDate(a.start)?.getTime() ?? 0) - (toDate(b.start)?.getTime() ?? 0)
      );
    }
    return map;
  }, [events, displayTimeZone]);

  const upcoming = useMemo(
    () =>
      [...events]
        .filter((e) => (toDate(e.start)?.getTime() ?? 0) >= Date.now() - 86400000)
        .sort(
          (a, b) =>
            (toDate(a.start)?.getTime() ?? 0) - (toDate(b.start)?.getTime() ?? 0)
        )
        .slice(0, 12),
    [events]
  );

  const connected = calendarRow?.status === "connected" || !!calendarCard?.connected;

  const connect = async () => {
    setBusy(true);
    try {
      const res = await api<ConnectResult>("/api/integrations/calendar/connect", {
        method: "POST",
      });
      if (res.status === "redirect" && res.authorization_url) {
        window.location.href = res.authorization_url;
        return;
      }
      onToast(t("integrationConnected"));
      load();
    } catch (err) {
      onToast((err as Error).message, true);
    } finally {
      setBusy(false);
    }
  };

  const create = async (e: FormEvent) => {
    e.preventDefault();
    if (!title.trim() || !startAt) return;
    setBusy(true);
    try {
      const body = {
        title: title.trim(),
        start: new Date(startAt).toISOString(),
        end: endAt ? new Date(endAt).toISOString() : undefined,
        location: location.trim(),
        description: description.trim(),
        attendees: attendees
          .split(",")
          .map((s) => s.trim())
          .filter(Boolean)
          .map((email) => ({ email })),
        timezone: displayTimeZone,
      };
      const res = await api<{ status: string; message?: string; suggested?: { start?: string } }>(
        "/api/calendar/events",
        { method: "POST", body }
      );
      if (res.status === "paused_approval") {
        onToast(t("calAwaitingApproval"));
      } else if (res.status === "conflict") {
        onToast(res.message || t("calCreated"), true);
      } else {
        onToast(t("calCreated"));
      }
      setTitle("");
      setStartAt("");
      setEndAt("");
      setLocation("");
      setDescription("");
      setAttendees("");
      setShowNew(false);
      load();
    } catch (err) {
      onToast((err as Error).message, true);
    } finally {
      setBusy(false);
    }
  };

  const remove = async (ev: CalendarEventT) => {
    if (ev.id.startsWith("g:")) {
      onToast(t("calGoogleManaged"), true);
      return;
    }
    try {
      const result = await api<{ status?: string }>(
        `/api/calendar/events/${encodeURIComponent(ev.id)}`,
        { method: "DELETE" }
      );
      if (result.status === "paused_approval") {
        onToast(t("calAwaitingApproval"));
      } else {
        load();
      }
    } catch (err) {
      onToast((err as Error).message, true);
    }
  };

  const editTitle = async (ev: CalendarEventT) => {
    if (ev.id.startsWith("g:")) {
      onToast(t("calGoogleManaged"), true);
      return;
    }
    const next = window.prompt("Event title", ev.title);
    if (next === null || !next.trim() || next.trim() === ev.title) return;
    try {
      const result = await api<{ status?: string }>(`/api/calendar/events/${encodeURIComponent(ev.id)}`, {
        method: "PATCH", body: { title: next.trim() },
      });
      if (result.status === "paused_approval") {
        onToast(t("calAwaitingApproval"));
      } else {
        load();
      }
    } catch (err) {
      onToast((err as Error).message, true);
    }
  };

  const cells: Date[] = [];
  for (let i = 0; i < 42; i += 1) cells.push(addDays(gridStart, i));
  const monthLabel = cursor.toLocaleDateString(undefined, {
    month: "long", year: "numeric", timeZone: displayTimeZone,
  });
  const today = new Date();

  return (
    <>
      <div className="view-header">
        <div>
          <h2>{t("calendarNav")}</h2>
          <p className="muted small">
            {connected ? "Google Calendar" : t("calMockNotice")}
          </p>
        </div>
        <div className="row gap">
          <button className="btn small" onClick={() => setCursor(startOfMonth(addDays(cursor, -31)))}>
            {t("calPrev")}
          </button>
          <button className="btn small" onClick={() => setCursor(startOfMonth(new Date()))}>
            {t("calToday")}
          </button>
          <button className="btn small" onClick={() => setCursor(startOfMonth(addDays(cursor, 31)))}>
            {t("calNext")}
          </button>
          <button className="btn small primary" onClick={() => setShowNew((s) => !s)}>
            {t("calNewEvent")}
          </button>
        </div>
      </div>

      {!connected && (
        <div className="notice warn row between">
          <span>
            <ProviderLogo id="calendar" className="provider-logo" /> {t("calMockNotice")}
            {calendarCard && !calendarCard.configured ? ` ${t("addCredentialsHint")}` : ""}
          </span>
          <button
            className="btn small primary"
            disabled={busy || !calendarCard?.configured}
            onClick={connect}
          >
            {calendarCard?.configured ? t("calConnect") : t("calAddCredentials")}
          </button>
        </div>
      )}

      {connected && (
        <p className="muted small">{t("calReminders")}</p>
      )}

      {showNew && (
        <form className="card schedule-form" onSubmit={create}>
          <label>{t("calEventTitle")}</label>
          <input value={title} onChange={(e) => setTitle(e.target.value)} />
          <label>{t("calStart")}</label>
          <input
            type="datetime-local"
            value={startAt}
            onChange={(e) => setStartAt(e.target.value)}
          />
          <label>{t("calEnd")}</label>
          <input
            type="datetime-local"
            value={endAt}
            onChange={(e) => setEndAt(e.target.value)}
          />
          <label>{t("calLocation")}</label>
          <input value={location} onChange={(e) => setLocation(e.target.value)} />
          <label>{t("calDescription")}</label>
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            rows={2}
          />
          <label>{t("calAttendees")}</label>
          <input value={attendees} onChange={(e) => setAttendees(e.target.value)} />
          <button className="btn primary" disabled={busy || !title.trim() || !startAt}>
            {busy ? t("calSaving") : t("calCreate")}
          </button>
        </form>
      )}

      <div className="card">
        <div className="cal-toolbar">
          <strong>{monthLabel}</strong>
        </div>
        <div className="cal-grid cal-head">
          {WEEKDAYS.map((d) => (
            <div key={d} className="cal-weekday">
              {d}
            </div>
          ))}
        </div>
        <div className="cal-grid">
          {cells.map((cell) => {
            const key = dayKey(cell, displayTimeZone);
            const dayEvents = byDay.get(key) ?? [];
            const cellMonth = dayKey(cell, displayTimeZone).slice(0, 7);
            const cursorMonth = dayKey(cursor, displayTimeZone).slice(0, 7);
            const outside = cellMonth !== cursorMonth;
            const isToday = sameDay(cell, today, displayTimeZone);
            const dayNumber = new Intl.DateTimeFormat("en-US", {
              timeZone: displayTimeZone, day: "numeric",
            }).format(cell);
            return (
              <div
                key={key}
                className={`cal-cell${outside ? " outside" : ""}${isToday ? " today" : ""}`}
              >
                <div className="cal-date">{dayNumber}</div>
                {dayEvents.map((ev) => (
                  <button
                    key={ev.id}
                    className={`cal-event${ev.provider === "google" ? " google" : ""}`}
                    title={`${ev.title}${ev.location ? ` · ${ev.location}` : ""}`}
                    onClick={() => editTitle(ev)}
                  >
                    {toDate(ev.start)?.toLocaleTimeString([], {
                      hour: "2-digit",
                      minute: "2-digit",
                      timeZone: resolveTimeZone(ev.timezone || displayTimeZone),
                    })}{" "}
                    {ev.title}
                  </button>
                ))}
              </div>
            );
          })}
        </div>
      </div>

      <div className="panel">
        <h3>{t("calToday")}</h3>
        {upcoming.length === 0 ? (
          <div className="activity-empty">{t("calNoEvents")}</div>
        ) : (
          <div className="task-list">
            {upcoming.map((ev) => (
              <div key={ev.id} className="task-card">
                {ev.provider !== "google" && (
                  <div className="row gap" style={{ justifyContent: "flex-end" }}>
                    <button className="btn small" onClick={() => void editTitle(ev)}>
                      Edit
                    </button>
                    <button className="btn small danger" onClick={() => void remove(ev)}>
                      Delete
                    </button>
                  </div>
                )}
                <div className="row between">
                  <div className="task-goal">{ev.title}</div>
                  <span className={`tool-pill ${ev.provider === "google" ? "p3" : ""}`}>
                    {ev.provider === "google" ? "Google" : "Local"}
                  </span>
                </div>
                <div className="meta">
                  {resolveTimeZone(ev.timezone || displayTimeZone)}{" · "}
                  {toDate(ev.start)?.toLocaleString()}
                  {ev.end ? ` – ${toDate(ev.end)?.toLocaleTimeString()}` : ""}
                  {ev.location ? ` · ${ev.location}` : ""}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </>
  );
}
