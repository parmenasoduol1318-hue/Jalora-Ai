/** PHASE J (§53-54) + workspace upgrade: a self-contained, Replit-style coding
 * workspace. It is fully independent of the Chats view:
 *
 *   — a project switcher (each project = a `Conversation(project="workspace")`
 *     backed by its own per-project sandbox folder);
 *   — an editable file tree + code editor (save/create/delete/rename/mkdir);
 *   — a runnable terminal backed by the allowlisted /api/workspace/exec;
 *   — an embedded coding agent (streams from /api/workspace/chat) with tool
 *     chips, approvals, and pending-edit diff/rollback;
 *   — a per-project requirements scratchpad.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { api, exportProject, uploadWorkspaceFiles } from "../lib/api";
import { errorMessage, friendlyError } from "../lib/errors";
import { streamAgent } from "../lib/sse";
import { toolDetailText } from "../lib/toolDetail";
import type {
  AppSettings, ApprovalInfo, ChatResponse, ProviderStatus, WorkspaceActionResponse, WorkspaceDiff,
  WorkspaceExecOut, WorkspaceFileContent, WorkspaceRollbackResult, WorkspaceSession,
  WorkspaceTerminalEntry, WorkspaceTree,
} from "../lib/types";

import { WorkspaceEditor } from "./WorkspaceEditor";

interface Props {
  t: (k: string) => string;
  onToast: (m: string, e?: boolean) => void;
  onOpenRun?: (runId: string) => void;
}

interface WsTool {
  name: string;
  status: "running" | "ok" | "fail";
  label: string;
  call_id?: string;
  detail?: Record<string, unknown>;
}

/* Turn a coding tool's structured result into a Run/Output console line so the
   user watches file writes and command output happen live, like an IDE. */
function toolConsoleLine(
  name: string, detail: Record<string, unknown>,
): WorkspaceTerminalEntry | null {
  if (name === "run_command" || name === "run_tests") {
    const out = [detail.stdout, detail.stderr].filter(Boolean).join("\n");
    return {
      command: String(detail.command || name),
      output: out || "(no output)",
      ok: Boolean(detail.ok),
      source: "agent",
    };
  }
  if (name === "run_linter") {
    const diags = (detail.diagnostics as Array<Record<string, unknown>>) || [];
    return {
      command: "lint",
      output: diags.length
        ? diags.map((d) => `${d.path || d.file || "?"}:${d.line ?? ""} ${d.message || d.detail || ""}`).join("\n")
        : "no issues",
      ok: diags.length === 0,
      source: "agent",
    };
  }
  if (name === "edit_code") {
    return {
      command: `${detail.created ? "create" : "edit"} ${detail.path || ""}`,
      output: String(detail.diff || "(saved)"),
      ok: true,
      source: "agent",
    };
  }
  if (name === "delete_file") {
    return {
      command: `delete ${detail.path || ""}`,
      output: "File removed from the project.",
      ok: true,
      source: "agent",
    };
  }
  if (name === "edit_code_multi") {
    const applied = (detail.applied as Array<Record<string, unknown>>) || [];
    const failures = (detail.failures as Array<Record<string, unknown>>) || [];
    return {
      command: `edit ${applied.length} file(s)`,
      output: applied.map((a) => `• ${a.path}\n${a.diff || ""}`).join("\n")
        + (failures.length ? `\n${failures.length} failed` : ""),
      ok: failures.length === 0,
      source: "agent",
    };
  }
  if (name === "create_project") {
    const filesArr = (detail.files as string[]) || [];
    return {
      command: `scaffold ${detail.name || ""} (${detail.template || ""})`,
      output: filesArr.join("\n") || "(empty)",
      ok: true,
      source: "agent",
    };
  }
  return null;
}

interface WsMsg {
  role: "user" | "assistant";
  text: string;
  finalized?: boolean;
  error?: boolean;
  tools?: WsTool[];
}

type LeftTab = "files" | "changes";
type AgentProvider = "auto" | "poolside" | "copilot" | "codex";
interface ExternalAgentStatus {
  sdk_installed: boolean;
  configured: boolean;
  available: boolean;
}

const RECOGNIZED_TOOLS: Record<string, string> = {
  list_files: "wsToolListFiles",
  read_file: "wsToolReadFile",
  inspect_code: "wsToolInspect",
  search_code: "wsToolSearch",
  analyze_error: "wsToolAnalyze",
  edit_code: "wsToolEdit",
  delete_file: "wsToolDelete",
  run_tests: "wsToolTests",
  run_command: "wsToolRun",
  run_linter: "wsToolLint",
};

const toolLabel = (name: string, t: (k: string) => string): string =>
  RECOGNIZED_TOOLS[name] ? t(RECOGNIZED_TOOLS[name]) : name;

const reqKey = (projectId: string) => `jalora_ws_requirements:${projectId}`;

export function WorkspaceView({ t, onToast, onOpenRun }: Props) {
  const [sessions, setSessions] = useState<WorkspaceSession[]>([]);
  const [projectId, setProjectId] = useState("");
  const [loadingSessions, setLoadingSessions] = useState(true);
  const [legacyFiles, setLegacyFiles] = useState<string[]>([]);
  const [poolsideAvailable, setPoolsideAvailable] = useState(false);
  const [externalAgents, setExternalAgents] = useState<
    Partial<Record<"copilot" | "codex", ExternalAgentStatus>>
  >({});
  const [agentProvider, setAgentProvider] = useState<AgentProvider>("auto");

  /* ---- files / editor -------------------------------------------------- */
  const [leftTab, setLeftTab] = useState<LeftTab>("files");
  const [tree, setTree] = useState<WorkspaceTree | null>(null);
  const [selectedFile, setSelectedFile] = useState("");
  const [openFiles, setOpenFiles] = useState<string[]>([]);
  const [fileContent, setFileContent] = useState<WorkspaceFileContent | null>(null);
  const [editorText, setEditorText] = useState("");
  const [saving, setSaving] = useState(false);
  const [agentChangedFile, setAgentChangedFile] = useState("");
  const [agentDeletedFile, setAgentDeletedFile] = useState("");
  /* ---- view mode (editor / agent / split) -------------------------------- */
  const [viewMode, setViewMode] = useState<"split" | "editor" | "agent">("split");

  /* ---- diff ------------------------------------------------------------ */
  const [diff, setDiff] = useState<WorkspaceDiff | null>(null);

  /* ---- terminal -------------------------------------------------------- */
  const [terminal, setTerminal] = useState<WorkspaceTerminalEntry[]>([]);
  const [cmdInput, setCmdInput] = useState("");
  const [termRunning, setTermRunning] = useState(false);

  /* ---- agent ----------------------------------------------------------- */
  const [messages, setMessages] = useState<WsMsg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [approval, setApproval] = useState<ApprovalInfo | null>(null);
  const [approving, setApproving] = useState(false);
  const runRef = useRef<string>("");
  const abortRef = useRef<AbortController | null>(null);
  const runningChip = useRef<Set<string>>(new Set());
  const bottomRef = useRef<HTMLDivElement | null>(null);

  /* ---- requirements scratchpad ---------------------------------------- */
  const [requirements, setRequirements] = useState("");
  const [reqSaved, setReqSaved] = useState(false);
  const reqTimer = useRef<number | null>(null);

  const fileRef = useRef<HTMLInputElement | null>(null);
  const saveTimer = useRef<number | null>(null);
  const projectIdRef = useRef(projectId);
  const selectedFileRef = useRef(selectedFile);
  const dirtyRef = useRef(false);
  const editorTextRef = useRef(editorText);
  const fileContentRef = useRef(fileContent);

  const files = useMemo(
    () => [...(tree?.files || [])].sort((a, b) => a.path.localeCompare(b.path)),
    [tree],
  );
  const dirty = !!fileContent && editorText !== fileContent.content;
  projectIdRef.current = projectId;
  selectedFileRef.current = selectedFile;
  dirtyRef.current = dirty;
  editorTextRef.current = editorText;
  fileContentRef.current = fileContent;

  const capturePendingApproval = (result: WorkspaceActionResponse): boolean => {
    if (result.status !== "paused_approval") return false;
    if (!result.approval) {
      throw new Error("The workspace action is awaiting approval, but no approval record was returned.");
    }
    setApproval(result.approval);
    return true;
  };

  const hasPendingApproval = (): boolean => {
    if (!approval) return false;
    onToast(t("approvalBanner"), true);
    return true;
  };

  /* ---- session loading -------------------------------------------------- */
  const loadSessions = async (select?: string) => {
    setLoadingSessions(true);
    try {
      const list = await api<WorkspaceSession[]>("/api/workspace/sessions");
      const next = list.find((s) => s.id === (select ?? projectId ?? ""))?.id || list[0]?.id || "";
      setSessions(list);
      setProjectId(next);
    } catch (err) {
      onToast((err as Error).message, true);
    } finally {
      setLoadingSessions(false);
    }
  };

  useEffect(() => {
    void loadSessions();
    api<{ files: Array<{ path: string }> }>("/api/workspace/legacy")
      .then((result) => setLegacyFiles(result.files.map((file) => file.path)))
      .catch((err) => onToast(errorMessage(err, t), true));
    Promise.all([
      api<{ providers: Record<string, ProviderStatus> }>("/api/providers"),
      api<AppSettings>("/api/settings"),
    ])
      .then(([status, prefs]) => {
        const poolside = status.providers.poolside;
        setPoolsideAvailable(
          !!poolside && !poolside.disabled
          && (poolside.configured || (prefs.provider_keys || []).includes("poolside")),
        );
      })
      .catch((err) => onToast(errorMessage(err, t), true));
    api<{ providers: Record<"copilot" | "codex", ExternalAgentStatus> }>("/api/workspace/agents")
      .then((result) => setExternalAgents(result.providers))
      .catch((err) => onToast(errorMessage(err, t), true));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /* ---- per-project load ------------------------------------------------- */
  useEffect(() => {
    if (!projectId) {
      setTree(null);
      setSelectedFile("");
      setOpenFiles([]);
      setFileContent(null);
      setEditorText("");
      setAgentChangedFile("");
      setAgentDeletedFile("");
      setDiff(null);
      setTerminal([]);
      setMessages([]);
      setApproval(null);
      setRequirements("");
      return;
    }
    setSelectedFile("");
    setOpenFiles([]);
    setFileContent(null);
    setEditorText("");
    setAgentChangedFile("");
    setAgentDeletedFile("");
    setTerminal([]);
    setMessages([]);
    setApproval(null);
    setRequirements(localStorage.getItem(reqKey(projectId)) || "");
    void loadTree(projectId);
    void loadDiff(projectId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  /* ---- requirements persistence (debounced) ---------------------------- */
  useEffect(() => {
    if (!projectId) return;
    if (reqTimer.current) window.clearTimeout(reqTimer.current);
    reqTimer.current = window.setTimeout(() => {
      try { localStorage.setItem(reqKey(projectId), requirements); } catch { /* quota */ }
      setReqSaved(true);
      window.setTimeout(() => setReqSaved(false), 1200);
    }, 500);
    return () => { if (reqTimer.current) window.clearTimeout(reqTimer.current); };
  }, [requirements, projectId]);

  /* ---- file autosave (debounced) ---------------------------------------- */
  useEffect(() => {
    if (!projectId || !fileContent || !dirty || agentChangedFile === selectedFile || approval) return;
    if (saving) return;
    if (saveTimer.current) window.clearTimeout(saveTimer.current);
    saveTimer.current = window.setTimeout(() => {
      void saveFile();
    }, 2000);
    return () => { if (saveTimer.current) window.clearTimeout(saveTimer.current); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editorText, fileContent, projectId, saving, agentChangedFile, selectedFile, approval]);

  /* ---- data helpers ----------------------------------------------------- */
  const loadTree = async (pid = projectId) => {
    if (!pid) return;
    try {
      const latest = await api<WorkspaceTree>(`/api/workspace/tree?project_id=${encodeURIComponent(pid)}`);
      if (projectIdRef.current === pid) setTree(latest);
    } catch (err) {
      onToast((err as Error).message, true);
    }
  };

  const loadDiff = async (pid = projectId) => {
    if (!pid) return;
    try {
      const latest = await api<WorkspaceDiff>(`/api/workspace/diff?project_id=${encodeURIComponent(pid)}`);
      if (projectIdRef.current === pid) setDiff(latest);
    } catch (err) {
      onToast((err as Error).message, true);
    }
  };

  const openFile = async (path: string) => {
    if (!projectId) return;
    const pid = projectId;
    setSelectedFile(path);
    setOpenFiles((current) => current.includes(path) ? current : [...current, path]);
    setFileContent(null);
    setEditorText("");
    try {
      const fc = await api<WorkspaceFileContent>(
        `/api/workspace/file?path=${encodeURIComponent(path)}&project_id=${encodeURIComponent(pid)}`);
      if (projectIdRef.current !== pid || selectedFileRef.current !== path) return;
      setFileContent(fc);
      setEditorText(fc.content);
      setAgentChangedFile("");
      setAgentDeletedFile("");
    } catch (err) {
      onToast((err as Error).message, true);
    }
  };

  const refreshWorkspaceAfterApproval = async (action: ApprovalInfo) => {
    const pid = projectIdRef.current;
    const args = action.arguments || {};
    let targetPath = selectedFileRef.current;
    const actionPath = typeof args.path === "string" ? args.path : "";

    if (
      ["delete_workspace_file", "delete_file"].includes(action.tool_name)
      && actionPath === targetPath
    ) {
      setOpenFiles((current) => current.filter((path) => path !== actionPath));
      setSelectedFile("");
      selectedFileRef.current = "";
      setFileContent(null);
      setEditorText("");
      editorTextRef.current = "";
      setAgentChangedFile("");
      setAgentDeletedFile("");
      targetPath = "";
    } else if (
      action.tool_name === "rename_workspace_file"
      && actionPath === targetPath
      && typeof args.new_path === "string"
    ) {
      targetPath = args.new_path;
      setOpenFiles((current) => current.map((path) => path === actionPath ? targetPath : path));
      setSelectedFile(targetPath);
      selectedFileRef.current = targetPath;
    } else if (
      ["create_workspace_file", "edit_code"].includes(action.tool_name)
      && actionPath
    ) {
      targetPath = actionPath;
      setOpenFiles((current) => current.includes(targetPath) ? current : [...current, targetPath]);
      setSelectedFile(targetPath);
      selectedFileRef.current = targetPath;
    }

    await loadTree(pid);
    await loadDiff(pid);
    if (!targetPath || projectIdRef.current !== pid) return;

    try {
      const latest = await api<WorkspaceFileContent>(
        `/api/workspace/file?path=${encodeURIComponent(targetPath)}&project_id=${encodeURIComponent(pid)}`,
      );
      if (projectIdRef.current !== pid || selectedFileRef.current !== targetPath) return;
      const oldBaseline = fileContentRef.current?.content;
      const approvedContent = typeof args.content === "string" ? args.content : undefined;
      setFileContent(latest);
      if (
        action.tool_name === "create_workspace_file"
        || editorTextRef.current === oldBaseline
        || editorTextRef.current === approvedContent
      ) {
        setEditorText(latest.content);
        editorTextRef.current = latest.content;
      }
      setAgentChangedFile("");
      setAgentDeletedFile("");
    } catch (err) {
      onToast(errorMessage(err, t), true);
    }
  };

  const closeFileTab = (path: string) => {
    if (path === selectedFile && dirty
        && !window.confirm(t("wsCloseDirtyFileConfirm"))) return;
    const remaining = openFiles.filter((item) => item !== path);
    setOpenFiles(remaining);
    if (path === selectedFile) {
      if (remaining.length) void openFile(remaining[remaining.length - 1]);
      else {
        setSelectedFile("");
        setFileContent(null);
        setEditorText("");
      }
    }
  };

  const refreshAgentChangedFiles = async (
    paths: string[], pid: string, deletedPaths: string[] = [],
  ) => {
    void loadTree(pid);
    void loadDiff(pid);
    const selected = selectedFileRef.current;
    if (!selected || !paths.includes(selected) || projectIdRef.current !== pid) return;
    if (deletedPaths.includes(selected)) {
      if (dirtyRef.current) {
        setAgentChangedFile(selected);
        setAgentDeletedFile(selected);
      } else {
        setOpenFiles((current) => current.filter((path) => path !== selected));
        setSelectedFile("");
        setFileContent(null);
        setEditorText("");
        setAgentChangedFile("");
        setAgentDeletedFile("");
      }
      return;
    }
    if (dirtyRef.current) {
      setAgentChangedFile(selected);
      setAgentDeletedFile("");
      return;
    }
    try {
      const latest = await api<WorkspaceFileContent>(
        `/api/workspace/file?path=${encodeURIComponent(selected)}&project_id=${encodeURIComponent(pid)}`,
      );
      if (projectIdRef.current !== pid || selectedFileRef.current !== selected) return;
      setFileContent(latest);
      setEditorText(latest.content);
      setAgentChangedFile("");
      setAgentDeletedFile("");
    } catch (err) {
      onToast(errorMessage(err, t), true);
    }
  };

  const importLegacyFiles = async () => {
    if (hasPendingApproval() || !projectId || !legacyFiles.length) return;
    if (!window.confirm(t("wsImportLegacyConfirm").replace("{n}", String(legacyFiles.length)))) return;
    try {
      const result = await api<WorkspaceActionResponse & { imported?: string[]; skipped?: string[] }>(
        `/api/workspace/sessions/${encodeURIComponent(projectId)}/import-legacy`,
        { method: "POST" },
      );
      if (capturePendingApproval(result)) return;
      const imported = result.imported || [];
      const skipped = result.skipped || [];
      setLegacyFiles((current) => current.filter((path) => !imported.includes(path)));
      await loadTree(projectId);
      const message = t("wsImportLegacyDone")
        .replace("{imported}", String(imported.length))
        .replace("{skipped}", String(skipped.length));
      onToast(message, skipped.length > 0);
    } catch (err) {
      onToast(errorMessage(err, t), true);
    }
  };

  const saveFile = async () => {
    if (hasPendingApproval() || !fileContent || !projectId) return;
    if (fileContent.truncated) {
      onToast(t("wsTooLargeToEdit"), true);
      return;
    }
    setSaving(true);
    try {
      const result = await api<WorkspaceActionResponse>(`/api/workspace/file?project_id=${encodeURIComponent(projectId)}`, {
        method: "PUT",
        body: { path: selectedFile, content: editorText },
      });
      if (capturePendingApproval(result)) return;
      setFileContent({ ...fileContent, content: editorText, size: editorText.length });
      setAgentChangedFile("");
      onToast(t("wsSaved"));
      void loadTree();
    } catch (err) {
      onToast((err as Error).message, true);
    } finally {
      setSaving(false);
    }
  };

  const newFile = async () => {
    if (hasPendingApproval() || !projectId) return;
    const name = window.prompt(t("wsNewFilePrompt"));
    if (!name?.trim()) return;
    try {
      const result = await api<WorkspaceActionResponse>(`/api/workspace/file?project_id=${encodeURIComponent(projectId)}`, {
        method: "POST", body: { path: name.trim(), content: "" },
      });
      if (capturePendingApproval(result)) return;
      await loadTree();
      void openFile(name.trim());
    } catch (err) {
      onToast((err as Error).message, true);
    }
  };

  const newFolder = async () => {
    if (hasPendingApproval() || !projectId) return;
    const name = window.prompt(t("wsNewFolderPrompt"));
    if (!name?.trim()) return;
    try {
      const result = await api<WorkspaceActionResponse>(`/api/workspace/mkdir?project_id=${encodeURIComponent(projectId)}`, {
        method: "POST", body: { path: name.trim() },
      });
      if (capturePendingApproval(result)) return;
      void loadTree();
    } catch (err) {
      onToast((err as Error).message, true);
    }
  };

  const deleteFile = async (path: string) => {
    if (hasPendingApproval() || !projectId
        || !window.confirm(t("wsDeleteConfirm").replace("{name}", path))) return;
    try {
      const result = await api<WorkspaceActionResponse>(`/api/workspace/file?path=${encodeURIComponent(path)}&project_id=${encodeURIComponent(projectId)}`,
        { method: "DELETE" });
      if (capturePendingApproval(result)) return;
      if (selectedFile === path) {
        setSelectedFile("");
        setFileContent(null);
        setEditorText("");
      }
      setOpenFiles((current) => current.filter((item) => item !== path));
      onToast(t("wsDeleted"));
      void loadTree();
    } catch (err) {
      onToast((err as Error).message, true);
    }
  };

  const renameFile = async (path: string) => {
    if (hasPendingApproval() || !projectId) return;
    const next = window.prompt(t("wsRenamePrompt"), path);
    if (!next?.trim() || next.trim() === path) return;
    try {
      const result = await api<WorkspaceActionResponse>(`/api/workspace/rename?project_id=${encodeURIComponent(projectId)}`, {
        method: "POST", body: { path, new_path: next.trim() },
      });
      if (capturePendingApproval(result)) return;
      if (selectedFile === path) setSelectedFile(next.trim());
      setOpenFiles((current) => current.map((item) => item === path ? next.trim() : item));
      void loadTree();
    } catch (err) {
      onToast((err as Error).message, true);
    }
  };

  const rollbackFile = async (path?: string) => {
    if (hasPendingApproval() || !projectId) return;
    try {
      const q = path ? `&path=${encodeURIComponent(path)}` : "";
      const result = await api<WorkspaceActionResponse & Partial<WorkspaceRollbackResult>>(
        `/api/workspace/rollback?project_id=${encodeURIComponent(projectId)}${q}`, { method: "POST" });
      if (capturePendingApproval(result)) return;
      onToast(`${t("wsRollback")}: ${(result.restored || []).join(", ") || path || ""}`);
      void loadTree();
      void loadDiff();
      if (selectedFile && (!path || path === selectedFile)) void openFile(selectedFile);
    } catch (err) {
      onToast((err as Error).message, true);
    }
  };

  const handleUpload = async (fileList: FileList | null) => {
    if (hasPendingApproval() || !fileList || !fileList.length || !projectId) return;
    try {
      const res = await uploadWorkspaceFiles(Array.from(fileList), projectId);
      if (capturePendingApproval(res)) return;
      const results = res.results || [];
      const rejected = res.rejected || [];
      if (results.length) onToast(t("wsUploadDone").replace("{n}", String(results.length)));
      if (rejected.length) onToast(t("wsUploadError").replace("{n}", String(rejected.length)), true);
      void loadTree();
    } catch (err) {
      onToast((err as Error).message, true);
    }
  };

  /* ---- new / rename / delete project ----------------------------------- */
  const newProject = async () => {
    if (hasPendingApproval()) return;
    const title = window.prompt(t("wsNewProjectPrompt"), "project-1");
    if (!title?.trim()) return;
    try {
      const created = await api<WorkspaceActionResponse & Partial<WorkspaceSession>>("/api/workspace/sessions", {
        method: "POST", body: { title: title.trim() },
      });
      if (capturePendingApproval(created)) return;
      if (!created.id) throw new Error("The project was created without a project ID.");
      await loadSessions(created.id);
    } catch (err) {
      onToast((err as Error).message, true);
    }
  };

  const renameProject = async () => {
    if (hasPendingApproval()) return;
    const current = sessions.find((s) => s.id === projectId);
    if (!current) return;
    const title = window.prompt(t("wsRenameProjectPrompt"), current.title);
    if (!title?.trim()) return;
    try {
      const result = await api<WorkspaceActionResponse & Partial<WorkspaceSession>>(
        `/api/workspace/sessions/${projectId}`, { method: "PATCH", body: { title: title.trim() } },
      );
      if (capturePendingApproval(result)) return;
      await loadSessions(projectId);
    } catch (err) {
      onToast((err as Error).message, true);
    }
  };

  const deleteProject = async () => {
    if (hasPendingApproval()) return;
    const current = sessions.find((s) => s.id === projectId);
    if (!current || !window.confirm(t("wsDeleteProjectConfirm").replace("{name}", current.title))) return;
    try {
      const result = await api<WorkspaceActionResponse | null>(
        `/api/workspace/sessions/${projectId}`, { method: "DELETE" },
      );
      if (result && capturePendingApproval(result)) return;
      await loadSessions("");
    } catch (err) {
      onToast((err as Error).message, true);
    }
  };

  /* ---- project export --------------------------------------------------- */
  const downloadProject = async () => {
    if (!projectId) return;
    try {
      const blob = await exportProject(projectId);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "workspace.zip";
      a.style.display = "none";
      document.body.appendChild(a);
      a.click();
      URL.revokeObjectURL(url);
      a.remove();
      onToast(t("wsExportDone").replace("{n}", String(files.length)));
    } catch (err) {
      onToast((err as Error).message, true);
    }
  };

  /* ---- terminal --------------------------------------------------------- */
  const runCommand = async (argv: string[]) => {
    if (hasPendingApproval() || !projectId || !argv.length) return;
    setTermRunning(true);
    const shown = argv.join(" ");
    try {
      const res = await api<WorkspaceActionResponse & Partial<WorkspaceExecOut>>(
        `/api/workspace/exec?project_id=${encodeURIComponent(projectId)}`,
        { method: "POST", body: { argv } });
      if (capturePendingApproval(res)) {
        setTerminal((prev) => [...prev, {
          command: shown,
          output: t("approvalBanner"),
          ok: false,
          source: "user",
        }]);
        return;
      }
      const output = [res.stdout, res.stderr].filter(Boolean).join("\n")
        + (res.timed_out ? `\n${t("wsTimedOut")}` : "");
      setTerminal((prev) => [...prev, {
        command: res.command || shown,
        output: output || t("wsNoOutput"),
        ok: Boolean(res.ok),
        source: "user",
      }]);
      void loadTree();
    } catch (err) {
      setTerminal((prev) => [...prev, {
        command: shown, output: (err as Error).message, ok: false, source: "user",
      }]);
    } finally {
      setTermRunning(false);
    }
  };

  const submitCommand = () => {
    const line = cmdInput.trim();
    if (!line) return;
    setCmdInput("");
    void runCommand(line.split(/\s+/));
  };

  /* ---- agent ------------------------------------------------------------ */
  const send = async (text: string) => {
    if (busy || !projectId) return;
    const finalText = text.trim();
    if (!finalText) return;
    const externalAgent = agentProvider === "copilot" || agentProvider === "codex";
    if (externalAgent) {
      if (!externalAgents[agentProvider]?.available) {
        onToast(t("wsExternalAgentUnavailable"), true);
        return;
      }
      const agentName = agentProvider === "copilot" ? t("wsAgentCopilot") : t("wsAgentCodex");
      const projectName = sessions.find((session) => session.id === projectId)?.title || projectId;
      const confirmation = t("wsExternalAgentConfirm")
        .replace("{agent}", agentName)
        .replace("{project}", projectName);
      if (!window.confirm(confirmation)) return;
    }
    setBusy(true);
    setApproval(null);
    setAgentChangedFile("");
    setAgentDeletedFile("");
    setInput("");
    const replyIdx = messages.length + 1;
    setMessages((prev) => [
      ...prev,
      { role: "user", text: finalText, finalized: true },
      { role: "assistant", text: "", tools: [] },
    ]);
    let live = "";
    let streamed = false;
    runningChip.current.clear();
    const ctrl = new AbortController();
    abortRef.current = ctrl;

    const finalize = (changes: Partial<WsMsg>) =>
      setMessages((prev) => prev.map((m, i) => (i === replyIdx ? { ...m, ...changes } : m)));

    try {
      await streamAgent(
        "/api/workspace/chat",
        {
          message: finalText,
          conversation_id: projectId,
          stream: true,
          ...(agentProvider === "poolside" ? { model_provider: "poolside" } : {}),
          ...(externalAgent ? {
            model_provider: agentProvider,
            external_agent_confirmed: true,
          } : {}),
        },
        (event) => {
          switch (event.type) {
            case "meta":
              runRef.current = String(event.run_id || "");
              break;
            case "token": {
              const tk = (event.text as string) || "";
              if (!streamed) {
                live += tk;
                finalize({ text: live, finalized: false });
              }
              break;
            }
            case "tool_start": {
              const name = String(event.name || "tool");
              const callId = String(event.call_id || "");
              setMessages((prev) => prev.map((m, i) => {
                if (i !== replyIdx) return m;
                const tools = [...(m.tools || []),
                  { name, status: "running" as const, label: `${toolLabel(name, t)}…`,
                    call_id: callId || undefined }];
                if (callId) runningChip.current.add(callId);
                return { ...m, tools, finalized: false };
              }));
              break;
            }
            case "tool_end": {
              const ok = Boolean(event.success);
              const msg = (event.message as string) || "";
              const callId = String(event.call_id || "");
              const detail = (event.detail as Record<string, unknown>) || undefined;
              const name = String(event.name || "");
              setMessages((prev) => prev.map((m, i) => {
                if (i !== replyIdx) return m;
                const tools = (m.tools || []).map((c) => {
                  const match = callId ? (c.call_id === callId)
                    : (c.name === name && c.status === "running");
                  return match
                    ? {
                        ...c,
                        status: ok ? ("ok" as const) : ("fail" as const),
                        detail,
                        label: `${toolLabel(c.name, t)} ${ok ? "✓" : "✗"}${msg ? ` — ${msg}` : ""}`,
                      }
                    : c;
                });
                return { ...m, tools };
              }));
              if (callId) runningChip.current.delete(callId);
              // Mirror coding tool activity into the Run/Output console.
              if (detail) {
                const line = toolConsoleLine(name, detail);
                if (line) setTerminal((prev) => [...prev, line]);
                const changedPaths = name === "edit_code"
                  ? [String(detail.path || "")].filter(Boolean)
                  : name === "edit_code_multi"
                    ? ((detail.applied as Array<Record<string, unknown>>) || [])
                      .map((item) => String(item.path || "")).filter(Boolean)
                    : name === "create_project"
                      ? ((detail.files as string[]) || [])
                        .map((path) => `${String(detail.name || "")}/${path}`).filter(Boolean)
                      : [];
                if (ok && changedPaths.length) {
                  void refreshAgentChangedFiles(
                    changedPaths,
                    projectId,
                    name === "delete_file" ? changedPaths : [],
                  );
                }
              }
              break;
            }
            case "approval":
              setApproval(event as unknown as ApprovalInfo);
              break;
            case "done": {
              streamed = true;
              finalize({ text: String(event.reply || live), finalized: true });
              if (event.status === "paused_approval" && event.approval) {
                setApproval(event.approval as ApprovalInfo);
              }
              void loadTree();
              void loadDiff();
              break;
            }
            case "error": {
              streamed = true;
              const err = event.error as { code?: string; details?: string } | undefined;
              runningChip.current.clear();
              finalize({ text: friendlyError(err?.code, err?.details, t), finalized: true, error: true });
              break;
            }
          }
        },
        ctrl.signal,
      );
      if (!streamed) finalize({ text: live, finalized: true });
    } catch (err) {
      if ((err as Error)?.name === "AbortError") {
        finalize({ text: live ? `${live}\n\n⏹ ${t("stopped")}` : `⏹ ${t("stopped")}`, finalized: true });
      } else {
        finalize({ text: errorMessage(err, t), finalized: true, error: true });
      }
    } finally {
      abortRef.current = null;
      runningChip.current.clear();
      setBusy(false);
    }
  };

  const stop = () => {
    abortRef.current?.abort();
    if (runRef.current) {
      api(`/api/runs/${runRef.current}/stop`, { method: "POST" }).catch(() => { /* aborted anyway */ });
    }
  };

  const decide = async (approve: boolean) => {
    const pendingAction = approval;
    const id = pendingAction?.approval_id || pendingAction?.id;
    if (!id) return;
    setApproval(null);
    setApproving(true);
    const replyIdx = messages.length;
    setMessages((prev) => [...prev, { role: "assistant", text: "" }]);
    try {
      const data = await api<ChatResponse>(
        `/api/approvals/${id}/${approve ? "approve" : "reject"}`, { method: "POST" });
      setMessages((prev) => prev.map((m, i) =>
        i === replyIdx
          ? {
              ...m,
              text: data.reply || (approve ? t("approvedMsg") : t("rejectedMsg")),
              finalized: true,
            }
          : m));
      if (data.approval) {
        setApproval(data.approval);
      } else if (approve && data.status === "completed" && pendingAction) {
        if (
          [
            "create_workspace_project",
            "rename_workspace_project",
            "archive_workspace_project",
          ].includes(pendingAction.tool_name)
        ) {
          await loadSessions();
        } else {
          await refreshWorkspaceAfterApproval(pendingAction);
        }
      } else {
        void loadTree();
        void loadDiff();
      }
    } catch (err) {
      setApproval(pendingAction);
      setMessages((prev) => prev.map((m, i) =>
        i === replyIdx ? { ...m, text: errorMessage(err, t), finalized: true, error: true } : m));
    } finally {
      setApproving(false);
    }
  };

  /* ---- render ----------------------------------------------------------- */
  const current = sessions.find((s) => s.id === projectId);

  return (
    <div className="ws-shell">
      <div className="ws-topbar">
        <span className="ws-brand">💻 {t("workspaceTitle")}</span>
        <select
          className="ws-project-select"
          value={projectId}
          disabled={loadingSessions || sessions.length === 0 || !!approval}
          onChange={(e) => setProjectId(e.target.value)}
        >
          {sessions.length === 0 && <option value="">{t("wsNoProjects")}</option>}
          {sessions.map((s) => <option key={s.id} value={s.id}>{s.title}</option>)}
        </select>
        <button className="btn small" onClick={newProject}>＋ {t("wsNewProject")}</button>
        {legacyFiles.length > 0 && (
          <button className="btn small" disabled={!projectId || loadingSessions} onClick={() => void importLegacyFiles()}>
            {t("wsImportLegacy").replace("{n}", String(legacyFiles.length))}
          </button>
        )}
        <button className="btn small" disabled={!projectId} onClick={renameProject}>{t("wsRenameProject")}</button>
        <button className="btn small danger" disabled={!projectId} onClick={deleteProject}>{t("delete")}</button>
        {' '}
        <button className="btn small" disabled={!projectId} onClick={() => void downloadProject()}>
          ⬇ {t("wsExportProject")}
        </button>
        <div className="ws-view-toggle" role="group" title={t("wsViewSplit")}>
          <button className={`btn small ${viewMode === "split" ? "active" : ""}`}
            onClick={() => setViewMode("split")}>{t("wsViewSplit")}</button>
          <button className={`btn small ${viewMode === "editor" ? "active" : ""}`}
            onClick={() => setViewMode("editor")}>{t("wsViewEditor")}</button>
          <button className={`btn small ${viewMode === "agent" ? "active" : ""}`}
            onClick={() => setViewMode("agent")}>{t("wsViewAgent")}</button>
        </div>
        <span className="ws-spacer" />
        {current && <span className="ws-subtitle">{t("wsStartedCoding")}</span>}
      </div>

      {!projectId && approval && (
        <div className="approval-banner">
          <div className="approval-banner-info">
            <span>⏳ <strong>{approval.tool_name}</strong> {t("approvalBanner")}</span>
            <p className="muted small">
              {t("taskApprovalReview")} · {t("taskApprovalLevel")} {approval.level}
            </p>
            {approval.kind === "credentials"
              ? <p className="muted small">{t("taskCredentialApprovalHint")}</p>
              : <pre className="task-approval-payload">
                  {JSON.stringify(approval.arguments || {}, null, 2)}
                </pre>}
            <p className="muted small">{t("taskApprovalScopeHint")}</p>
          </div>
          <span>
            {approval.run_id && onOpenRun && (
              <button
                className="btn small"
                onClick={() => {
                  const runId = approval.run_id;
                  if (runId) onOpenRun(runId);
                }}
              >
                {t("taskOpenDetails")}
              </button>
            )}{" "}
            <button className="btn small" disabled={approving} onClick={() => void decide(false)}>{t("deny")}</button>{" "}
            <button className="btn primary small" disabled={approving} onClick={() => void decide(true)}>{t("approve")}</button>
          </span>
        </div>
      )}

      {!projectId ? (
        <div className="ws-empty-full">
          <div className="ws-empty-logo">◆</div>
          <h2>{t("wsEmptyTitle")}</h2>
          <p>{t("wsEmptyHint")}</p>
          <button className="btn primary" onClick={newProject}>＋ {t("wsNewProject")}</button>
        </div>
      ) : (
        <div className={`ws-body ws-view-${viewMode}`}>
          {/* ---- left: files / changes ---- */}
          <div className="ws-col ws-col-files">
            <div className="ws-tabs">
              <button className={`tab ${leftTab === "files" ? "active" : ""}`}
                onClick={() => setLeftTab("files")}>{t("wsFileTree")}</button>
              <button className={`tab ${leftTab === "changes" ? "active" : ""}`}
                onClick={() => { setLeftTab("changes"); void loadDiff(); }}>
                {t("wsDiff")}{diff?.edits.length ? ` (${diff.edits.length})` : ""}
              </button>
            </div>

            {leftTab === "files" ? (
              <>
                <div className="ws-file-actions">
                  <button className="btn small" onClick={newFile}>＋ {t("wsFile")}</button>
                  <button className="btn small" onClick={newFolder}>＋ {t("wsFolder")}</button>
                  <button className="btn small" onClick={() => fileRef.current?.click()}>⬆ {t("wsUploadBtn")}</button>
                  <button className="btn small" onClick={() => void loadTree()}>↻</button>
                </div>
                <input
                  ref={fileRef}
                  type="file"
                  multiple
                  style={{ display: "none" }}
                  onChange={(e) => { void handleUpload(e.target.files); e.target.value = ""; }}
                />
                <ul className="ws-file-list">
                  {files.length === 0 && <li className="ws-empty">{t("workspaceEmpty")}</li>}
                  {files.map((f) => (
                    <li key={f.path} className="ws-file-row">
                      <button
                        className={`ws-file-link ${selectedFile === f.path ? "active" : ""}`}
                        onClick={() => void openFile(f.path)}
                        title={f.path}
                      >📄 {f.path}</button>
                      <button className="ws-icon-btn" title={t("wsRenameProject")}
                        onClick={() => void renameFile(f.path)}>✎</button>
                      <button className="ws-icon-btn danger" title={t("delete")}
                        onClick={() => void deleteFile(f.path)}>✕</button>
                    </li>
                  ))}
                </ul>
              </>
            ) : (
              <div className="ws-diff-view">
                <div className="ws-file-actions">
                  <button className="btn small danger" disabled={!diff?.edits.length}
                    onClick={() => void rollbackFile()}>{t("wsRollbackAll")}</button>
                  <button className="btn small" onClick={() => void loadDiff()}>↻</button>
                </div>
                {(!diff || diff.edits.length === 0) && <div className="ws-empty">{t("wsNoEdits")}</div>}
                {diff?.edits.map((e) => (
                  <div key={e.path} className="ws-diff-entry">
                    <div className="ws-diff-path">
                      {e.path}{e.created && ` (${t("wsCreated")})`}
                    </div>
                    <pre className="ws-diff-text">{e.diff}</pre>
                    <button className="btn small danger" onClick={() => void rollbackFile(e.path)}>
                      {t("wsRollback")}
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* ---- center: editor + terminal ---- */}
          <div className="ws-col ws-col-editor">
            <div className="ws-editor-tabs" role="toolbar" aria-label={t("wsOpenFiles")}>
              {openFiles.map((path) => (
                <div key={path} className={`ws-editor-tab ${selectedFile === path ? "active" : ""}`}>
                  <button aria-current={selectedFile === path ? "page" : undefined}
                    title={path} onClick={() => {
                      if (path === selectedFile) return;
                      if (dirty && !window.confirm(t("wsSwitchDirtyFileConfirm"))) return;
                      void openFile(path);
                    }}>{path}</button>
                  <button className="ws-tab-close" aria-label={`${t("delete")} ${path}`}
                    onClick={() => closeFileTab(path)}>×</button>
                </div>
              ))}
            </div>
            <div className="ws-editor-head">
              <span className="ws-editor-path">
                {selectedFile || t("wsFilePath")}{dirty ? " •" : ""}
              </span>
              {agentChangedFile === selectedFile && (
                <span className="ws-external-change" role="status">
                  {agentDeletedFile === selectedFile ? t("wsAgentDeletedFile") : t("wsAgentChangedFile")}
                  {agentDeletedFile === selectedFile ? (
                    <button className="btn small" onClick={() => closeFileTab(selectedFile)}>
                      {t("wsCloseDeletedFile")}
                    </button>
                  ) : (
                    <button className="btn small" onClick={() => void openFile(selectedFile)}>
                      {t("wsReloadFile")}
                    </button>
                  )}
                </span>
              )}
              <span className="ws-spacer" />
              {selectedFile && (
                <>
                  <button className="btn small" disabled={!dirty || saving} onClick={() => void saveFile()}>
                    {saving ? "…" : t("wsSave")}
                  </button>
                  <button className="btn small" onClick={() => void renameFile(selectedFile)}>✎</button>
                  <button className="btn small danger" onClick={() => void deleteFile(selectedFile)}>✕</button>
                </>
              )}
            </div>

            <WorkspaceEditor
              path={selectedFile || ""}
              value={editorText}
              saving={saving}
              onChange={(v) => setEditorText(v ?? "")}
              onSave={saveFile}
              t={t}
            />

            <div className="ws-terminal">
              <div className="ws-terminal-head">
                <span>{t("wsTerminal")}</span>
                <span className="ws-spacer" />
                <button className="btn small" disabled={termRunning}
                  onClick={() => void runCommand(["pytest", "-q"])}>{t("wsRunTests")}</button>
                <button className="btn small" disabled={termRunning}
                  onClick={() => void runCommand(["python", selectedFile || "main.py"])}>{t("wsRun")}</button>
                <button className="btn small" onClick={() => setTerminal([])}>🧹</button>
              </div>
              <div className="ws-terminal-out">
                {terminal.length === 0 && <div className="ws-empty">{t("wsTerminalEmpty")}</div>}
                {terminal.map((entry, i) => (
                  <div key={i} className={`ws-term-entry ${entry.source === "agent" ? "agent" : ""}`}>
                    <div className="ws-term-cmd">
                      {entry.source === "agent" ? "◆" : "$"} {entry.command}
                    </div>
                    <pre className={`ws-term-out ${entry.ok ? "" : "err"}`}>{entry.output}</pre>
                  </div>
                ))}
                {termRunning && <div className="ws-term-cmd">$ …</div>}
              </div>
              <div className="ws-term-input">
                <span>$</span>
                <input
                  value={cmdInput}
                  placeholder={t("wsTerminalPlaceholder")}
                  onChange={(e) => setCmdInput(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); submitCommand(); } }}
                />
                <button className="btn small primary" disabled={termRunning} onClick={submitCommand}>
                  {t("wsRun")}
                </button>
              </div>
            </div>
          </div>

          {/* ---- right: agent ---- */}
          <div className="ws-col ws-col-agent">
            <div className="ws-agent-head">
              <span>◆ {
                agentProvider === "poolside" ? t("wsAgentPoolside")
                  : agentProvider === "copilot" ? t("wsAgentCopilot")
                    : agentProvider === "codex" ? t("wsAgentCodex")
                      : t("jalora")
              }</span>
              <span className="ws-spacer" />
              <select
                className="ws-agent-provider"
                aria-label={t("wsAgentProvider")}
                title={t("wsAgentProvider")}
                value={agentProvider}
                disabled={busy}
                onChange={(event) => setAgentProvider(event.target.value as AgentProvider)}
              >
                <option value="auto">{t("wsAgentJalora")}</option>
                {poolsideAvailable && <option value="poolside">{t("wsAgentPoolside")}</option>}
                <option value="copilot" disabled={!externalAgents.copilot?.available}>
                  {t("wsAgentCopilot")}{!externalAgents.copilot?.available ? ` — ${t("wsAgentSetupNeeded")}` : ""}
                </option>
                <option value="codex" disabled={!externalAgents.codex?.available}>
                  {t("wsAgentCodex")}{!externalAgents.codex?.available ? ` — ${t("wsAgentSetupNeeded")}` : ""}
                </option>
              </select>
              {busy && <button className="btn small danger" onClick={stop}>■ {t("stop")}</button>}
            </div>
            {(agentProvider === "copilot" || agentProvider === "codex") && (
              <div className="ws-agent-provider-notice" role="status">
                {externalAgents[agentProvider]?.available
                  ? t("wsExternalAgentScope")
                  : t("wsAgentSetupNeeded")}
              </div>
            )}

            <div className="ws-requirements">
              <textarea
                className="ws-requirements-input"
                rows={2}
                placeholder={t("wsRequirementsPlaceholder")}
                value={requirements}
                onChange={(e) => setRequirements(e.target.value)}
              />
              <div className="ws-req-meta">
                {reqSaved && <span className="ws-requirements-saved">{t("wsRequirementsSaved")}</span>}
              </div>
              <div className="ws-requirements-actions">
                <button
                  className="btn primary small"
                  disabled={!requirements.trim() || busy}
                  onClick={() => {
                    if (!requirements.trim() || busy) return;
                    void send(requirements.trim());
                  }}
                >{t("wsStartCoding")}</button>
              </div>
            </div>

            <div className="ws-agent-messages">
              {messages.length === 0 && <div className="ws-empty">{t("wsAgentEmpty")}</div>}
              {messages.map((m, i) => (
                <div key={i} className={`ws-msg ${m.role}`}>
                  {m.role === "user" ? (
                    <div className="ws-bubble user">{m.text}</div>
                  ) : (
                    <div className={`ws-bubble assistant ${m.error ? "error" : ""}`}>
                      {m.tools && m.tools.length > 0 && (
                        <div className="ws-tools">
                          {m.tools.map((c, ci) => (
                            <span key={ci} className={`tool-chip ${c.status === "running" ? "" : c.status}`}>
                              <span className="dot" /><span className="label">{c.label}</span>
                            </span>
                          ))}
                        </div>
                      )}
                      {m.tools && m.tools.some((c) => c.detail && toolDetailText(c.detail)) && (
                        <div className="ws-tool-details">
                          {m.tools.filter((c) => c.detail && toolDetailText(c.detail)).map((c, ci) => (
                            <details key={ci} className="tool-detail">
                              <summary>{c.label}</summary>
                              <pre>{toolDetailText(c.detail)}</pre>
                            </details>
                          ))}
                        </div>
                      )}
                      {m.error
                        ? <div className="error-bubble">⚠ {m.text}</div>
                        : <div className="ws-text">{m.text}{!m.finalized && m.text && <span className="cursor">▌</span>}</div>}
                      {!m.finalized && !m.text && (!m.tools || m.tools.length === 0) && !m.error && (
                        <div className="thinking"><span className="pm-spinner">…</span> {t("searching")}</div>
                      )}
                    </div>
                  )}
                </div>
              ))}
              <div ref={bottomRef} />
            </div>

            {approval && (
              <div className="approval-banner">
                <div className="approval-banner-info">
                  <span>⏳ <strong>{approval.tool_name}</strong> {t("approvalBanner")}</span>
                  <p className="muted small">
                    {t("taskApprovalReview")} · {t("taskApprovalLevel")} {approval.level}
                  </p>
                  {approval.kind === "credentials"
                    ? <p className="muted small">{t("taskCredentialApprovalHint")}</p>
                    : <pre className="task-approval-payload">
                        {JSON.stringify(approval.arguments || {}, null, 2)}
                      </pre>}
                  <p className="muted small">{t("taskApprovalScopeHint")}</p>
                </div>
                <span>
                  {approval.run_id && onOpenRun && (
                    <button
                      className="btn small"
                      onClick={() => {
                        const runId = approval.run_id;
                        if (runId) onOpenRun(runId);
                      }}
                    >
                      {t("taskOpenDetails")}
                    </button>
                  )}{" "}
                  <button className="btn small" disabled={approving} onClick={() => void decide(false)}>{t("deny")}</button>{" "}
                  <button className="btn primary small" disabled={approving} onClick={() => void decide(true)}>{t("approve")}</button>
                </span>
              </div>
            )}

            <div className="ws-agent-composer">
              <textarea
                rows={2}
                placeholder={t("wsAgentPlaceholder")}
                value={input}
                disabled={busy}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void send(input); }
                }}
              />
              <button
                className={`send-btn ${busy ? "stop-btn" : ""}`}
                disabled={busy || !input.trim()}
                title={busy ? t("stop") : t("send")}
                onClick={() => (busy ? stop() : void send(input))}
              >{busy ? "■" : "➤"}</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
