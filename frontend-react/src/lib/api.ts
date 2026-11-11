import type {
  DocumentT, MediaItem, TranscribeResult, VoiceConfig, WorkspaceActionResponse, WorkspaceUploadOut,
} from "./types";

export class ApiError extends Error {
  code: string;
  status: number;
  constructor(code: string, details: string, status: number) {
    super(details);
    this.code = code;
    this.status = status;
  }
}

let token = localStorage.getItem("jalora_token") || "";
let unauthorized: (() => void) | null = null;

export function setUnauthorizedHandler(fn: () => void) {
  unauthorized = fn;
}
export function getToken() {
  return token;
}
export function setToken(value: string) {
  token = value;
  localStorage.setItem("jalora_token", value);
}
export function clearToken() {
  token = "";
  localStorage.removeItem("jalora_token");
}

/* Append the Bearer token as a `?token=` query parameter to a media URL so
   that <img src> / <video src> tags (which cannot set Authorization headers)
   can fetch authenticated assets. Exported so both ChatView and
   CreativeStudioView can share the same logic. */
export function mediaUrl(base: string | undefined | null): string {
  if (!base) return "";
  if (!token) return base;
  return base + (base.includes("?") ? "&" : "?") + "token=" + encodeURIComponent(token);
}

export interface RequestOptions {
  method?: string;
  body?: unknown;
  formData?: FormData;
}

export async function api<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = {};
  if (token) headers.Authorization = `Bearer ${token}`;
  if (opts.body) headers["Content-Type"] = "application/json";
  const resp = await fetch(path, {
    method: opts.method || "GET",
    headers,
    body: opts.formData || (opts.body ? JSON.stringify(opts.body) : undefined),
  });
  if (resp.status === 401) {
    const data = await resp.json().catch(() => ({})) as Record<string, unknown>;
    const err = (data.error || {}) as { code?: string; details?: string };
    if (err.details || data.message) {
      throw new ApiError(err.code || "INVALID_CREDENTIALS", err.details || String(data.message), resp.status);
    }
    clearToken();
    unauthorized?.();
    throw new ApiError("UNAUTHORIZED", "Session expired", 401);
  }
  if (resp.status === 204) return null as T;
  const data = (await resp.json().catch(() => ({}))) as Record<string, unknown>;
  if (!resp.ok) {
    const err = (data.error || {}) as { code?: string; details?: string };
    throw new ApiError(
      err.code || "ERROR",
      err.details || (data.message as string) || resp.statusText,
      resp.status,
    );
  }
  return data as T;
}

/* Phase D (§42): upload a chat attachment (image/audio/video) via /api/media. */
export async function uploadMedia(file: File): Promise<MediaItem> {
  const fd = new FormData();
  fd.append("file", file);
  return api<MediaItem>("/api/media", { method: "POST", formData: fd });
}

/** Upload an owned document so it can be selected as a real email attachment. */
export async function uploadDocument(file: File): Promise<DocumentT> {
  const fd = new FormData();
  fd.append("file", file, file.name);
  return api<DocumentT>("/api/documents", { method: "POST", formData: fd });
}

export interface EmailAttachmentUpload {
  filename: string;
  mime_type: string;
  size_bytes: number;
  url: string;
}

export async function uploadEmailAttachment(file: File): Promise<EmailAttachmentUpload> {
  const fd = new FormData();
  fd.append("file", file, file.name);
  return api<EmailAttachmentUpload>("/api/emails/attachments", {
    method: "POST",
    formData: fd,
  });
}

/* Phase H (§46-48): server-side speech-to-text fallback for browsers without
   the Web Speech API. The recorded clip is posted as multipart field `file`. */
export async function transcribeAudio(blob: Blob, filename = "clip.webm"): Promise<TranscribeResult> {
  const fd = new FormData();
  fd.append("file", new File([blob], filename, { type: blob.type || "audio/webm" }));
  return api<TranscribeResult>("/api/voice/transcribe", { method: "POST", formData: fd });
}

/* Phase H: what the server can do (STT always on via the whisper chain). */
export function fetchVoiceConfig(): Promise<VoiceConfig> {
  return api<VoiceConfig>("/api/voice/config");
}

/* Phase 1: WebSocket URL for the live in-app voice call. Browsers cannot set
   headers on WebSockets, so the JWT travels as a query parameter instead. */
export function voiceLiveUrl(
  conversationId?: string | null,
  callId?: string | null,
): string {
  const defaultBackendOrigin = import.meta.env.PROD
    ? "https://jalora-ai.onrender.com"
    : window.location.origin;
  const backendOrigin = import.meta.env.VITE_API_WS_ORIGIN || defaultBackendOrigin;
  const url = new URL(backendOrigin);
  if (url.pathname !== "/" || url.search || url.hash) {
    throw new Error("VITE_API_WS_ORIGIN must be an origin without a path or query.");
  }
  if (url.protocol === "https:") {
    url.protocol = "wss:";
  } else if (url.protocol === "http:" && window.location.protocol !== "https:") {
    url.protocol = "ws:";
  } else {
    throw new Error("The voice backend must use HTTPS when Jalora is opened over HTTPS.");
  }
  url.pathname = "/api/voice/live";
  url.searchParams.set("token", token);
  if (conversationId) url.searchParams.set("conversation_id", conversationId);
  if (callId) url.searchParams.set("call_id", callId);
  return url.toString();
}

/* Phase J (§53-54): upload one or more files into the coding workspace.
   `projectId` scopes the upload to a specific workspace project folder. */
export function uploadWorkspaceFiles(
  files: File[], projectId = "",
): Promise<WorkspaceActionResponse & Partial<WorkspaceUploadOut>> {
  const fd = new FormData();
  for (const f of files) fd.append("files", f, f.name);
  if (projectId) fd.append("project_id", projectId);
  return api<WorkspaceActionResponse & Partial<WorkspaceUploadOut>>(
    "/api/workspace/upload", { method: "POST", formData: fd },
  );
}

/* Phase J (§55): download a zip of the whole project workspace ("download my
   work") as a binary Blob, so it can be saved locally without leaving a copy
   in the browser's memory once the download anchor is revoked. */
export async function exportProject(projectId = ""): Promise<Blob> {
  const headers: Record<string, string> = {};
  if (token) headers.Authorization = `Bearer ${token}`;
  const resp = await fetch(
    `/api/workspace/export?project_id=${encodeURIComponent(projectId)}`,
    { method: "GET", headers },
  );
  if (resp.status === 401) {
    clearToken();
    unauthorized?.();
    throw new ApiError("UNAUTHORIZED", "Session expired", 401);
  }
  if (!resp.ok) {
    const data = (await resp.json().catch(() => ({}))) as { error?: { code?: string; details?: string } };
    throw new ApiError(
      data.error?.code || "ERROR",
      data.error?.details || resp.statusText,
      resp.status,
    );
  }
  return await resp.blob();
}
