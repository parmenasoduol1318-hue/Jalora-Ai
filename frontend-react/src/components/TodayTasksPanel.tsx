import { useEffect, useState } from "react";
import { api } from "../lib/api";
import type { Task } from "../lib/types";

interface Props {
  t: (k: string) => string;
  className?: string;
}

export function TodayTasksPanel({ t, className }: Props) {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        const data = await api<Task[]>("/api/tasks?status=running");
        if (!cancelled) setTasks(data);
      } catch { /* keep stale */ }
      finally { if (!cancelled) setLoading(false); }
    };
    tick();
    const id = setInterval(tick, 15000);
    return () => { cancelled = true; clearInterval(id); };
  }, []);

  const activeTasks = tasks.filter((tk) => tk.status === "running" || tk.status === "queued");

  return (
    <div className={className}>
      <div className="rs-card">
        <div className="rs-card-title">{t("rightPanelTasks")}</div>
        {loading ? (
          <div style={{ fontSize: 12, color: "var(--text-dim)" }}>{t("refresh")}…</div>
        ) : activeTasks.length === 0 ? (
          <div className="rs-task-item">
            <span className="rs-task-dot"></span>
            <span style={{ color: "var(--text-dim)", fontSize: 12 }}>{t("rightPanelEmpty")}</span>
          </div>
        ) : (
          activeTasks.slice(0, 5).map((task) => (
            <div className="rs-task-item" key={task.id}>
              <span className="rs-task-dot"></span>
              <span style={{ flex: 1, fontSize: 12 }}>{task.goal || `#${task.id.slice(0, 6)}`}</span>
              <span className="rs-provider-status" style={{
                color:
                  task.status === "running" ? "var(--accent)"
                  : task.status === "failed" ? "var(--danger)"
                  : "var(--ok)",
              }}>
                {task.status}
              </span>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
