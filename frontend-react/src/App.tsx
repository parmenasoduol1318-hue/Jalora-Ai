import { useCallback, useEffect, useRef, useState } from "react";
import {
  api, clearToken, getToken, setToken, setUnauthorizedHandler,
} from "./lib/api";
import { translator, readStoredLocale, type Locale } from "./lib/i18n";
import type { Conversation, Task, User, ViewName, ThemeName, OllamaRouteEvent } from "./lib/types";
import { ChatView } from "./views/ChatView";
import { DashboardView } from "./views/DashboardView";
import { TasksView } from "./views/TasksView";
import { MemoryView } from "./views/MemoryView";
import { DocumentsView } from "./views/DocumentsView";
import { SettingsView } from "./views/SettingsView";
import { WorkspaceView } from "./views/WorkspaceView";
import { IntegrationsView } from "./views/IntegrationsView";
import { RecentsView } from "./views/RecentsView";
import { SchedulesView } from "./views/SchedulesView";
import { CalendarView } from "./views/CalendarView";
import { CreativeStudioView } from "./views/CreativeStudioView";
import { PeopleView } from "./views/PeopleView";
import { DevicesView } from "./views/DevicesView";
import { NotificationBell } from "./components/NotificationBell";
import { RightSidebar } from "./components/RightSidebar";
import { navIcons } from "./components/SidebarIcons";
import { RemoteDeviceCompanion } from "./components/RemoteDeviceCompanion";

type NavIconKey = keyof typeof navIcons;

const NAV: { view: ViewName; icon: NavIconKey; key: string }[] = [
  { view: "chat", icon: "chat", key: "chatNav" },
  { view: "dashboard", icon: "dashboard", key: "dashboardTitle" },
  { view: "tasks", icon: "tasks", key: "tasks" },
  { view: "schedules", icon: "schedules", key: "schedulesNav" },
  { view: "calendar", icon: "calendar", key: "calendarNav" },
  { view: "memory", icon: "memory", key: "notesMemory" },
  { view: "workspace", icon: "workspace", key: "workspaceNav" },
  { view: "documents", icon: "documents", key: "filesNav" },
  { view: "integrations", icon: "integrations", key: "integrations" },
  { view: "devices", icon: "devices", key: "devicesAccess" },
  { view: "people", icon: "people", key: "peopleTitle" },
  { view: "settings", icon: "settings", key: "settings" },
  { view: "history", icon: "history", key: "historyNav" },
  { view: "creative", icon: "creative", key: "creativeStudio" },
];

const SIDEBAR_VIEWS = new Set<ViewName>(["chat", "dashboard"]);

export default function App() {
  const [locale, setLocale] = useState<Locale>(readStoredLocale);
  const t = translator(locale);
  const [user, setUser] = useState<User | null>(null);
  const [booted, setBooted] = useState(false);
  const [view, setView] = useState<ViewName>("chat");
  const [taskFocusId, setTaskFocusId] = useState<string | null>(null);
  const [taskFocusKey, setTaskFocusKey] = useState(0);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [convError, setConvError] = useState(false);
  const [navOpen, setNavOpen] = useState(false);
  const [rightSidebarOpen, setRightSidebarOpen] = useState(
    () => !window.matchMedia("(max-width: 1000px)").matches,
  );
  const [theme, setTheme] = useState<ThemeName>(
    (localStorage.getItem("jalora_theme") as ThemeName) || "dark"
  );
  const [ramRoute, setRamRoute] = useState<OllamaRouteEvent | null>(null);
  const [active, setActive] = useState<{ id: string; title: string } | null>(null);
  const [chatReset, setChatReset] = useState(0);
  /* chatKey only bumps on explicit user actions (sidebar click, new chat,
     start-coding). It must NOT change when a conversation is auto-created
     mid-stream — that previously caused ChatView to remount mid-request and
     silently drop the first reply (the "first message doesn't respond but
     the second does" bug). */
  const [chatKey, setChatKey] = useState(0);
  const [initialPrompt, setInitialPrompt] = useState<string | null>(null);
  const [initialChatMode, setInitialChatMode] = useState<"chat" | "agent">("chat");
  const [toastMsg, setToastMsg] = useState<{ text: string; error: boolean } | null>(null);
  const toastTimer = useRef<number | null>(null);
  const [backendDown, setBackendDown] = useState(false);

  const toast = useCallback((text: string, error = false) => {
    setToastMsg({ text, error });
    if (toastTimer.current) window.clearTimeout(toastTimer.current);
    toastTimer.current = window.setTimeout(() => setToastMsg(null), 3800);
  }, []);

  const logout = useCallback(() => {
    clearToken();
    setUser(null);
    setConversations([]);
    setActive(null);
    setNavOpen(false);
  }, []);

  useEffect(() => {
    setUnauthorizedHandler(() => setUser(null));
  }, []);

  useEffect(() => {
    document.body.setAttribute("data-theme", theme);
    localStorage.setItem("jalora_theme", theme);
  }, [theme]);

  const toggleTheme = () => {
    setTheme((prev) => (prev === "dark" ? "light" : "dark"));
  };

  useEffect(() => {
    (async () => {
      if (!getToken()) {
        setBooted(true);
        return;
      }
      try {
        const me = await api<User>("/api/auth/me");
        setUser(me);
      } catch {
        /* token invalid — api() already cleared it */
      }
      setBooted(true);
    })();
  }, []);

  // Backend reachability probe: surface a banner + toast when the API is
  // down (e.g. dev server not started or a port-8000 conflict). One-shot on mount.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await fetch("/api/health", { cache: "no-store" });
        if (!r.ok && !cancelled) {
          setBackendDown(true);
          toast("Backend unreachable — start the dev server and refresh.", true);
        }
      } catch {
        if (!cancelled) {
          setBackendDown(true);
          toast("Backend unreachable — start the dev server and refresh.", true);
        }
      }
    })();
    return () => { cancelled = true; };
  }, []);

  // OAuth callbacks redirect back to the SPA with a status query param. Handle
  // them globally so the toast fires and the relevant view opens regardless of
  // which view was active when the user left for the provider.
  useEffect(() => {
    if (!booted) return;
    const params = new URLSearchParams(window.location.search);
    const gmail = params.get("gmail");
    const integration = params.get("integration");
    if (!gmail && !integration) return;
    const provider = params.get("provider") || "";
    if (gmail) {
      if (gmail === "connected") {
        const email = params.get("email");
        toast(email ? `${t("gmailConnected")}: ${email}` : t("gmailConnected"));
      } else {
        const reason = params.get("reason");
        const detail = reason === "access_denied"
          ? ` ${t("googleOAuthAccessDenied")}`
          : "";
        toast(`${t("gmailConnectError")}${detail}`, true);
      }
      setView("integrations");
    } else if (integration === "connected") {
      const label = provider ? provider.charAt(0).toUpperCase() + provider.slice(1) : "";
      toast(`${label} ${t("integrationConnected")}`.trim());
      setView("integrations");
    } else {
      const reason = params.get("reason");
      const detail = reason === "access_denied"
        ? ` ${t("googleOAuthAccessDenied")}`
        : "";
      toast(`${t("integrationConnectError")}${detail}`, true);
      setView("integrations");
    }
    window.history.replaceState({}, "", window.location.pathname);
  }, [booted, t, toast]);

  const loadConversations = useCallback(async () => {
    try {
      const convs = await api<Conversation[]>("/api/conversations");
      setConversations(convs);
      setConvError(false);
      setActive((a) => {
        if (!a) return a;
        const found = convs.find((c) => c.id === a.id);
        return found && found.title !== a.title ? { ...a, title: found.title } : a;
      });
    } catch {
      setConvError(true);
    }
  }, []);

  useEffect(() => {
    if (user) loadConversations();
  }, [user, loadConversations]);

  useEffect(() => {
    if (!user) return;
    let cancelled = false;
    const tick = async () => {
      try {
        const data = await api<Task[]>("/api/tasks?status=running");
        if (!cancelled) setTasks(data);
      } catch { /* keep stale */ }
    };
    tick();
    const id = window.setInterval(tick, 15000);
    return () => { cancelled = true; window.clearInterval(id); };
  }, [user]);
  const changeLocale = (next: Locale) => {
    setLocale(next);
    localStorage.setItem("jalora_locale", next);
    toast(translator(next)("langChanged"));
  };

  const stopAll = async () => {
    try {
      const data = await api<{ message: string }>("/api/agents/stop", { method: "POST" });
      toast(data.message);
    } catch (err) {
      toast((err as Error).message, true);
    }
  };

  const openConversation = (c: Conversation) => {
    setActive({ id: c.id, title: c.title });
    setChatKey((k) => k + 1); // fresh ChatView for the selected conversation
    setView("chat");
    setNavOpen(false);
  };

  const openConversationById = (id: string) => {
    const conversation = conversations.find((item) => item.id === id);
    setActive({ id, title: conversation?.title || t("newChat") });
    setChatKey((key) => key + 1);
    setView("chat");
    setNavOpen(false);
  };

  const focusTask = (taskId: string) => {
    setTaskFocusId(taskId);
    setTaskFocusKey((key) => key + 1);
    setView("tasks");
    setNavOpen(false);
  };

  const openRun = async (runId: string) => {
    try {
      const matches = await api<Task[]>(
        `/api/tasks?run_id=${encodeURIComponent(runId)}&limit=1`,
      );
      if (matches[0]) {
        focusTask(matches[0].id);
        return;
      }
      toast(t("notificationTargetUnavailable"), true);
    } catch (err) {
      toast((err as Error).message, true);
    }
  };

  const openNotification = async (notification: {
    task_id?: string | null;
    run_id?: string | null;
    link?: string | null;
  }) => {
    if (notification.task_id) {
      focusTask(notification.task_id);
      return;
    }
    if (notification.run_id) {
      await openRun(notification.run_id);
      return;
    }
    if (notification.link && /^[a-f0-9]{32}$/i.test(notification.link)) {
      openConversationById(notification.link);
      return;
    }
    toast(t("notificationTargetUnavailable"), true);
  };

  const newChat = () => {
    setActive(null);
    setChatReset((n) => n + 1);
    setInitialChatMode("chat");
    setView("chat");
    setNavOpen(false);
  };

  const startAgentTask = (prompt: string) => {
    setActive(null);
    setInitialPrompt(prompt);
    setInitialChatMode("agent");
    setChatKey((key) => key + 1);
    setView("chat");
    setNavOpen(false);
  };

  if (!booted) return null;

  if (window.location.pathname === "/device-companion") {
    return <RemoteDeviceCompanion onExit={() => { window.location.href = "/"; }} />;
  }

  if (!user) {
    return (
      <AuthScreen
        t={t}
        onLoggedIn={async (u) => {
          setUser(u);
          setActive(null);
          setChatReset((n) => n + 1);
        }}
      />
    );
  }

  return (
    <div className={`app ${SIDEBAR_VIEWS.has(view) && rightSidebarOpen ? "with-context" : ""}`}>
      {backendDown && (
        <div className="backend-down-banner" role="alert">
          Backend unreachable — start the dev server (scripts\dev_run.bat) and refresh.
        </div>
      )}
      {navOpen && <div className="sidebar-backdrop" onClick={() => setNavOpen(false)} />}
      <aside className={`sidebar ${navOpen ? "open" : ""}`}>
        {/* Brand — SVG logo + name + subtitle */}
        <div className="brand">
          <div className="brand-logo">
            <svg viewBox="0 0 48 48" fill="none" aria-hidden="true">
              <path d="M5 4L19 4L43 44L29 44Z" fill={theme === "dark" ? "#087CFF" : "#1677ff"} />
              <path d="M43 4L29 4L5 44L19 44Z" fill={theme === "dark" ? "#1557D5" : "#3b82f6"} />
              <path d="M17 16L31 16L36 24L22 24Z" fill={theme === "dark" ? "#2C9BFF" : "#60a5fa"} />
            </svg>
          </div>
          <div>
            <div className="brand-name">Jalora AI</div>
            <div className="brand-tagline">{t("tagline")}</div>
          </div>
        </div>

        {/* New chat button */}
        <button id="btn-new-chat" className="nav-item" onClick={newChat}>
          <span className="nav-icon" aria-hidden>＋</span>
          <span className="nav-label">{t("newChat")}</span>
        </button>

        <div className="nav-divider"></div>

        {/* Navigation items */}
        <div className="nav-section">
          {NAV.map(({ view: v, icon: iconKey, key: i18nKey }) => {
            const Icon = navIcons[iconKey];
            return (
              <button
                key={v}
                className={`nav-item ${view === v ? "active" : ""}`}
                onClick={() => { setView(v); setNavOpen(false); }}
              >
                <Icon className="nav-icon" />
                <span className="nav-label">{t(i18nKey)}</span>
              </button>
            );
          })}
        </div>

        {/* Sidebar bottom — profile + theme toggle */}
        <div className="sidebar-bottom">
          <div className="profile">
            <div
              className="avatar"
              style={{
                background: "linear-gradient(135deg, var(--accent), var(--accent-2))",
              }}
            >
              {user?.name?.[0]?.toUpperCase() || user?.email?.[0]?.toUpperCase() || "U"}
            </div>
            <div className="profile-info">
              <div className="profile-name">{user?.name || user?.email || "User"}</div>
              <div className="profile-plan">{t("planBadge")}</div>
            </div>
          </div>

          <div className="theme-row">
            <span>{theme === "dark" ? "Dark Mode" : "Light Mode"}</span>
            <button
              id="btn-theme"
              className={`switch ${theme === "light" ? "off" : ""}`}
              title={t("themeToggle")}
              onClick={toggleTheme}
              aria-label={t("themeToggle")}
            />
          </div>

          <button id="btn-logout" className="secondary-button" onClick={logout}>
            {t("logout")}
          </button>
          <button id="btn-stop" className="secondary-button" onClick={stopAll}>
            {t("stopAll")}
          </button>
        </div>
      </aside>

      <main className="main">
        {/* Topbar — visible on desktop and mobile */}
        <header className="topbar">
          <div className="topbar-left">
            <button
              className="nav-toggle mobile-menu"
              aria-label={t("menu")}
              title={t("menu")}
              onClick={() => setNavOpen(true)}
            >
              ☰
            </button>
            <div>
              <div className="page-heading">{t(NAV.find((n) => n.view === view)?.key ?? "dashboardTitle")}</div>
              <div className="page-subheading">Your intelligent workspace</div>
            </div>
          </div>
          <label className="search">
            <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <circle cx="11" cy="11" r="7" />
              <path d="M21 21l-4.35-4.35" />
            </svg>
            <input type="search" placeholder="Search anything…" />
          </label>
          <NotificationBell
            t={t}
            onToast={toast}
            onOpenNotification={(notification) => void openNotification(notification)}
          />
          {SIDEBAR_VIEWS.has(view) && (
            <button
              className="top-icon"
              aria-label={t("rightSidebar")}
              aria-controls="context-sidebar"
              aria-expanded={rightSidebarOpen}
              title={t("rightSidebar")}
              onClick={() => setRightSidebarOpen((open) => !open)}
            >
              <span style={{ fontSize: 20, color: theme === "dark" ? "var(--cyan)" : "#087cff" }}>◉</span>
            </button>
          )}
        </header>

        <div className="content">
        {view === "chat" && (
          <ChatView
            key={`chat:${chatKey}:${chatReset}`}
            t={t}
            active={active}
            user={user}
            initialMode={initialChatMode}
            initialPrompt={initialPrompt}
            onPromptConsumed={() => {
              setInitialPrompt(null);
              setInitialChatMode("chat");
            }}
            onConversation={(id) => {
              setActive({ id, title: t("newChat") });
              loadConversations();
            }}
             onToast={toast}
            onAuthError={() => logout()}
            onReloadConversations={loadConversations}
            onRamRoute={setRamRoute}
            onOpenRun={(runId) => void openRun(runId)}
          />
        )}
        {view === "dashboard" && (
          <DashboardView t={t} ramRoute={ramRoute} />
        )}
        {view === "tasks" && (
          <TasksView
            t={t}
            onToast={toast}
            onStartTask={startAgentTask}
            focusTaskId={taskFocusId}
            focusTaskKey={taskFocusKey}
            onOpenConversation={openConversationById}
          />
        )}
        {view === "schedules" && (
          <SchedulesView
            t={t}
            onToast={toast}
            onOpenTask={focusTask}
          />
        )}
        {view === "calendar" && <CalendarView t={t} onToast={toast} />}
        {view === "memory" && <MemoryView t={t} onToast={toast} />}
        {view === "documents" && <DocumentsView t={t} onToast={toast} />}
        {view === "workspace" && (
          <WorkspaceView
            t={t}
            onToast={toast}
            onOpenRun={(runId) => void openRun(runId)}
          />
        )}
        {view === "integrations" && <IntegrationsView t={t} onToast={toast} />}
        {view === "devices" && <DevicesView t={t} onToast={toast} />}
        {view === "people" && <PeopleView t={t} onToast={toast} />}
        {view === "recents" && (
          <RecentsView
            t={t}
            conversations={conversations}
            tasks={tasks}
            convError={convError}
            activeId={active?.id ?? null}
            onLoad={loadConversations}
            onOpenConversation={openConversation}
            onNewChat={newChat}
            onToast={toast}
          />
        )}
        {view === "history" && (
          <RecentsView
            t={t}
            conversations={conversations}
            tasks={tasks}
            convError={convError}
            activeId={active?.id ?? null}
            onLoad={loadConversations}
            onOpenConversation={openConversation}
            onNewChat={newChat}
            onToast={toast}
          />
        )}
        {view === "settings" && (
          <SettingsView t={t} locale={locale} onLocale={changeLocale} onToast={toast} theme={theme} toggleTheme={toggleTheme} />
        )}
        {view === "creative" && <CreativeStudioView toast={toast} />}
      </div>
      </main>

      {SIDEBAR_VIEWS.has(view) && (
        <RightSidebar
          t={t}
          ramModel={ramRoute ? { model: ramRoute.model, tier: ramRoute.tier } : null}
          open={rightSidebarOpen}
          onToggle={setRightSidebarOpen}
        />
      )}

      <div className={`toast ${toastMsg ? "" : "hidden"} ${toastMsg?.error ? "error" : ""}`}>
        {toastMsg?.text || ""}
      </div>
    </div>
  );
}

function AuthScreen({
  t, onLoggedIn,
}: {
  t: (k: string) => string;
  onLoggedIn: (u: User) => Promise<void>;
}) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const data = await api<{ access_token: string; user: User }>(
        `/api/auth/${mode}`,
        { method: "POST", body: mode === "register" ? { email, password, name } : { email, password } },
      );
      setToken(data.access_token);
      await onLoggedIn(data.user);
    } catch (err) {
      setError((err as Error).message || t("failed"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="auth-screen">
      <div className="auth-card">
        <div className="auth-brand"><span className="auth-logo">◆</span> JALORA AI</div>
        <p className="auth-tagline">{t("tagline")}</p>
        <form onSubmit={submit}>
          {mode === "register" && (
            <div className="field">
              <input
                type="text" name="name" placeholder={t("nameLabel")} autoComplete="name"
                value={name} onChange={(e) => setName(e.target.value)}
              />
            </div>
          )}
          <div className="field">
            <input
              type="email" name="email" placeholder={t("emailLabel")} required autoComplete="email"
              value={email} onChange={(e) => setEmail(e.target.value)}
            />
          </div>
          <div className="field">
            <input
              type="password" name="password" placeholder={t("passwordLabel")} required minLength={8}
              autoComplete="current-password"
              value={password} onChange={(e) => setPassword(e.target.value)}
            />
          </div>
          <button type="submit" className="btn primary block" disabled={busy}>
            {mode === "login" ? t("loginInstead") : t("createAccount")}
          </button>
          {error && <div className="auth-error">{error}</div>}
        </form>
        <div className="auth-switch">
          <span>{mode === "login" ? t("noAccount") : t("haveAccount")}</span>
          <a
            href="#"
            className="link"
            onClick={(e) => { e.preventDefault(); setMode(mode === "login" ? "register" : "login"); setError(""); }}
          >
            {mode === "login" ? t("createAccount") : t("loginInstead")}
          </a>
        </div>
      </div>
    </div>
  );
}
