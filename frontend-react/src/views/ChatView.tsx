import { Fragment, useEffect, useRef, useState } from "react";
import { api, clearToken, getToken, ApiError, transcribeAudio, uploadDocument, uploadEmailAttachment, uploadMedia, mediaUrl } from "../lib/api";
import { errorMessage, friendlyError } from "../lib/errors";
import { resolveTimeZone } from "../lib/timezone";
import { toolDetailText } from "../lib/toolDetail";
import type {
  ApprovalInfo, AppSettings, Artifact, BrowserStatus, ChatMessage, ChatResponse,
  CallResult, CitationRef, DiagramRef, OllamaRouteEvent, PlanStep, SourceRef, User, VoiceSettings,
} from "../lib/types";
import {
  createRecognizer, readRecognition, recordUntilStopped, speak, stopSpeaking,
  type ClipRecorder, type SpeechRecognitionLike,
} from "../lib/voice";
import { AssistantAvatar } from "../components/AssistantAvatar";

/* MathJax v3 (loaded via CDN in index.html). Typed locally to avoid an npm
 * @types/mathjax dependency; safe no-op when the script hasn't loaded yet. */
type MathJaxInstance = {
  typesetPromise?: (nodes?: Element[] | Element | null) => Promise<unknown>;
  typesetClearPromise?: (nodes?: Element[] | Element | null) => Promise<unknown>;
};
const getMathJax = (): MathJaxInstance | undefined =>
  (window as unknown as { MathJax?: MathJaxInstance }).MathJax;

interface ToolChip {
  name: string;
  status: "running" | "ok" | "fail";
  label: string;
  call_id?: string;
  detail?: Record<string, unknown>;
}

interface PendingMedia {
  id: string;
  name: string;
  kind: "image" | "audio" | "video" | "document";
  url?: string;
  uploading: boolean;
}

interface ChatPlan {
  steps: PlanStep[];
  progress: number;
  current_step: number;
  reason?: string;
}

interface UiMessage {
  role: "user" | "assistant";
  text: string;
  finalized?: boolean;
  error?: boolean;
  tools?: ToolChip[];
  sources?: SourceRef[];
  intent?: string;
  agentic?: boolean;
  plan?: ChatPlan;
  artifacts?: Artifact[];
  citations?: CitationRef[];
  diagrams?: DiagramRef[];
  created_at?: string;
}

const STEP_ICON: Record<string, string> = {
  pending: "○", active: "◐", done: "✓", failed: "✗", skipped: "⊘",
};

const RECOGNIZED_TOOLS: Record<string, string> = {
  search_web: "webSearching",
  browse_page: "browsingPage",
  open_url: "openingPage",
};

const toolLabel = (name: string, t: (k: string) => string): string =>
  RECOGNIZED_TOOLS[name] ? t(RECOGNIZED_TOOLS[name]) : name;

interface EmailTaskResult {
  status: string;
  draft?: Record<string, unknown> | null;
  approval?: ApprovalInfo | null;
  error?: { code?: string; detail?: string } | null;
}

const isDirectEmailRequest = (text: string): boolean => {
  if (
    /\b(?:step\s*#?\s*\d+|multi[-\s]?step|step[-\s]?by[-\s]?step|dependent sequence|interconnected pipeline)\b/i
      .test(text)
  ) return false;
  if (
    !/\b(?:send|sending)\b/i.test(text)
    && !/\bemail\s+(?:to\s+)?[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b/i.test(text)
  ) return false;
  if (/\b(?:how|why|when|where|what)\s+(?:do|can|should|would)\b/i.test(text)) {
    return false;
  }
  return /\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b/i.test(text);
};

/* Phase 7 (math/science): explanation-depth options sent as `math_level`. */
const MATH_LEVELS: { value: string; key: string }[] = [
  { value: "auto", key: "levelAuto" },
  { value: "primary", key: "levelPrimary" },
  { value: "highschool", key: "levelHighschool" },
  { value: "university", key: "levelUniversity" },
  { value: "stem", key: "levelStem" },
  { value: "postgrad", key: "levelPostgrad" },
  { value: "professor", key: "levelProfessor" },
];

/* Dynamic quick actions — generic capability shortcuts. Each action collects
   parameters from the user via a prompt dialog, then builds a natural-language
   message that is sent to the chat backend. No task is hardcoded. */
interface QuickActionDef {
  id: string;
  icon: string;
  label: string;
  subtitle: string;
  intent: "web_search" | "email" | "math" | "task" | "coding" | "research";
  category?: string;
}

interface Props {
  t: (k: string) => string;
  active: { id: string; title: string } | null;
  user?: User | null;
  initialMode?: "chat" | "agent";
  initialPrompt?: string | null;
  onPromptConsumed?: () => void;
  onConversation: (id: string) => void;
  onToast: (m: string, e?: boolean) => void;
  onAuthError: () => void;
  onReloadConversations: () => void;
  onRamRoute?: (event: OllamaRouteEvent | null) => void;
  onOpenRun?: (runId: string) => void;
}

/* Fix #4: Specialised rendering for send_email approvals. Instead of showing
   raw JSON, this renders a friendly TO / SUBJECT / MESSAGE card that matches
   the design spec so the user can verify the exact email before approving. */
function EmailApprovalCard({
  arguments: args,
  t,
  onChange,
  onSave,
  onAttach,
  onRemoveAttachment,
  saving,
  uploadingAttachment,
  heading,
}: {
  arguments: Record<string, unknown>;
  t: (k: string) => string;
  onChange: (next: Record<string, unknown>) => void;
  onSave: () => void;
  onAttach: (files: File[], emailIndex?: number) => void;
  onRemoveAttachment: (index: number, emailIndex?: number) => void;
  saving: boolean;
  uploadingAttachment: boolean;
  heading?: string;
}) {
  const [draft, setDraft] = useState<Record<string, unknown>>(args);
  const attachmentRef = useRef<HTMLInputElement | null>(null);
  useEffect(() => setDraft(args), [args]);
  const setField = (key: string, value: string) => {
    const next = { ...draft, [key]: value };
    setDraft(next);
    onChange(next);
  };
  const to = String(args.to ?? args.recipient ?? args.recipients ?? "");
  const subject = String(draft.subject ?? draft.title ?? "");
  const message = String(draft.body ?? draft.message ?? draft.text ?? "");
  const regards = String(draft.regards ?? "Regards,");
  const usedFallback = String(draft.generation_method ?? args.generation_method ?? "") === "heuristic";
  const rawAttachments = draft.attachments;
  const attachments: string[] = Array.isArray(rawAttachments)
    ? rawAttachments.map((a) => typeof a === "string" ? a : String((a as Record<string, unknown>).filename || (a as Record<string, unknown>).name || (a as Record<string, unknown>).document_id || "attachment"))
    : [];

  /* Render attachments as a simple list when present. */
  const attachmentSection = attachments.length > 0
    ? (
      <div className="approval-email-attachments">
        <strong>{t("attachments")}</strong>
        <ul className="approval-email-att-list">
          {attachments.map((a, i) => (
            <li key={`${a}-${i}`}>
              <span>{a}</span>
              <button className="btn small" type="button" onClick={() => onRemoveAttachment(i)}>
                {t("remove")}
              </button>
            </li>
          ))}
        </ul>
      </div>
    )
    : (
      <div className="approval-email-attachments">
        <strong>{t("attachments")}</strong>
        <span className="approval-email-no-attach">{t("noAttachments")}</span>
      </div>
    );

  return (
    <div className="approval-email-card">
      {heading && <strong>{heading}</strong>}
      {usedFallback && <small className="approval-email-no-attach">{t("emailModelFallback")}</small>}
      <div className="approval-email-field">
        <label>{t("toLabel")}</label>
        <input className="approval-email-input" value={String(draft.to ?? to)} onChange={(e) => setField("to", e.target.value)} />
      </div>
      <div className="approval-email-field">
        <label>Cc</label>
        <input className="approval-email-input" value={String(draft.cc ?? "")} onChange={(e) => setField("cc", e.target.value)} placeholder="name@example.com, ..." />
      </div>
      <div className="approval-email-field">
        <label>Bcc</label>
        <input className="approval-email-input" value={String(draft.bcc ?? "")} onChange={(e) => setField("bcc", e.target.value)} placeholder="name@example.com, ..." />
      </div>
      <div className="approval-email-field">
        <label>{t("subjectLabel")}</label>
        <input className="approval-email-input" value={subject} onChange={(e) => setField("subject", e.target.value)} placeholder={t("noSubject")} />
      </div>
      <div className="approval-email-field">
        <label>{t("messageLabel")}</label>
        <textarea className="approval-email-input approval-email-textarea" value={message} onChange={(e) => setField("body", e.target.value)} rows={6} />
      </div>
      <div className="approval-email-field">
        <label>{t("regardsLabel")}</label>
        <textarea className="approval-email-input approval-email-regards" value={regards} onChange={(e) => setField("regards", e.target.value)} rows={2} />
      </div>
      {attachmentSection}
      <div className="approval-email-actions">
        <input ref={attachmentRef} type="file" multiple hidden onChange={(e) => {
          const files = Array.from(e.target.files || []);
          if (files.length) onAttach(files);
          e.target.value = "";
        }} />
        <button className="btn small" type="button" disabled={uploadingAttachment} onClick={() => attachmentRef.current?.click()}>
          {uploadingAttachment ? t("uploadingAttachment") : t("addAttachment")}
        </button>
        <button className="btn small primary" type="button" disabled={saving} onClick={onSave}>{saving ? t("saving") : t("saveChanges")}</button>
      </div>
    </div>
  );
}

function EmailBatchApprovalCard({
  arguments: args,
  t,
  onChange,
  onSave,
  onAttach,
  onRemoveAttachment,
  saving,
  uploadingAttachment,
}: {
  arguments: Record<string, unknown>;
  t: (k: string) => string;
  onChange: (next: Record<string, unknown>) => void;
  onSave: () => void;
  onAttach: (files: File[], emailIndex?: number) => void;
  onRemoveAttachment: (index: number, emailIndex?: number) => void;
  saving: boolean;
  uploadingAttachment: boolean;
}) {
  const emails = Array.isArray(args.emails)
    ? args.emails.filter(
      (email): email is Record<string, unknown> =>
        Boolean(email) && typeof email === "object" && !Array.isArray(email),
    )
    : [];
  return (
    <div className="approval-email-batch">
      {emails.map((email, index) => (
        <EmailApprovalCard
          key={`${String(email.to || "recipient")}-${index}`}
          arguments={email}
          t={t}
          heading={`Email ${index + 1} of ${emails.length}`}
          saving={saving}
          uploadingAttachment={uploadingAttachment}
          onChange={(updated) => {
            const nextEmails = emails.map((current, currentIndex) =>
              currentIndex === index ? updated : current,
            );
            onChange({ ...args, emails: nextEmails });
          }}
          onSave={onSave}
          onAttach={(files) => onAttach(files, index)}
          onRemoveAttachment={(attachmentIndex) =>
            onRemoveAttachment(attachmentIndex, index)
          }
        />
      ))}
    </div>
  );
}

export function ChatView({
  t, active, user, initialMode = "chat", initialPrompt, onPromptConsumed, onConversation, onToast, onAuthError, onReloadConversations,
  onRamRoute, onOpenRun,
}: Props) {
  const [messages, setMessages] = useState<UiMessage[]>([]);
  const [timezone, setTimezone] = useState("Africa/Nairobi");
  const displayTimezone = resolveTimeZone(timezone);
  const [input, setInput] = useState("");
  const [editingPromptIndex, setEditingPromptIndex] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [model, setModel] = useState("");
  const [ramRoute, setRamRoute] = useState<OllamaRouteEvent | null>(null);
  const [mode, setMode] = useState<"chat" | "research" | "agent">(initialMode);
  const [mathLevel, setMathLevel] = useState<string>(
    () => localStorage.getItem("jalora_math_level") || "auto",
  );
  const [showWorking, setShowWorking] = useState<boolean>(
    () => localStorage.getItem("jalora_show_working") !== "false",
  );
  const [approval, setApproval] = useState<ApprovalInfo | null>(null);
  const [approvalDraft, setApprovalDraft] = useState<Record<string, unknown> | null>(null);
  const [approvalDraftSaving, setApprovalDraftSaving] = useState(false);
  const [approvalAttachmentUploading, setApprovalAttachmentUploading] = useState(false);
  const [pausedRun, setPausedRun] = useState<string | null>(null);
  const [browserStatus, setBrowserStatus] = useState<BrowserStatus | null>(null);
  const [callResult, setCallResult] = useState<CallResult | null>(null);
  const [credUsername, setCredUsername] = useState("");
  const [credSecret, setCredSecret] = useState("");
  const [approving, setApproving] = useState(false);
  const [pendingMedia, setPendingMedia] = useState<PendingMedia[]>([]);
  const pendingRef = useRef<PendingMedia[]>([]);
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const fileRef = useRef<HTMLInputElement | null>(null);
  const composerRef = useRef<HTMLTextAreaElement | null>(null);
  const runningChip = useRef<Set<string>>(new Set());
  const abortRef = useRef<AbortController | null>(null);
  const runRef = useRef<{ runId: string; taskId: string } | null>(null);
  const autoSentRef = useRef(false);
  useEffect(() => {
    if (
      approval?.tool_name === "send_email"
      || approval?.tool_name === "send_email_batch"
    ) {
      const draft = { ...(approval.arguments || {}) };
      if (approval.tool_name === "send_email_batch" && Array.isArray(draft.emails)) {
        setApprovalDraft({
          ...draft,
          emails: draft.emails.map((email) => ({
            ...(email as Record<string, unknown>),
            regards: (email as Record<string, unknown>).regards ?? "Regards,",
          })),
        });
      } else {
        setApprovalDraft({
          ...draft,
          regards: draft.regards ?? "Regards,",
        });
      }
    } else if (approval?.tool_name === "request_user_input") {
      setApprovalDraft({
        ...(approval.arguments || {}),
        answer: approval.arguments?.answer ?? "",
      });
    } else {
      setApprovalDraft(null);
    }
  }, [approval?.id, approval?.approval_id]);
  /* convIdRef holds a conversation id created server-side during the current
     stream — used so subsequent calls in the same turn carry the right id even
     before the parent's `active` prop updates (which no longer triggers a
     remount). */
  const convIdRef = useRef<string | null>(null);
  /* streamingRef guards the useEffect that loads saved messages: when a
     conversation is auto-created mid-stream, `active?.id` changes and the effect
     would fire — we skip the reload to avoid clobbering the live reply. */
  const streamingRef = useRef(false);
  /* Phase H (§46-48): voice interface state */
  const [voice, setVoice] = useState<VoiceSettings>({
    enabled: false, auto_speak: true, continuous: false, voice: "",
  });
  const [listening, setListening] = useState(false);
  const [voicePartial, setVoicePartial] = useState("");
  const [transcribing, setTranscribing] = useState(false);
  const [speaking, setSpeaking] = useState(false);
  const voiceRef = useRef<VoiceSettings>(voice);
  const recognitionRef = useRef<SpeechRecognitionLike | null>(null);
  const recorderRef = useRef<ClipRecorder | null>(null);
  const afterReplyRef = useRef<(text: string) => void>(() => {});

  /* Selecting a home shortcut chooses a default intent; the user's own
     message remains the request sent to the backend. */
  const [showMoreActions, setShowMoreActions] = useState(false);
  const [promptAction, setPromptAction] = useState<QuickActionDef | null>(null);

  /* The selected intent is only a fallback: the backend's classifier takes
     precedence when the message clearly asks for a different capability. */
  const primaryActions: QuickActionDef[] = [
    {
      id: "search",
      icon: "🔍",
      label: t("qaSearch"),
      subtitle: t("qaSearchSubtitle"),
      intent: "web_search",
      category: "research",
    },
    {
      id: "email",
      icon: "📝",
      label: t("qaEmail"),
      subtitle: t("qaEmailSubtitle"),
      intent: "email",
      category: "email",
    },
    {
      id: "math",
      icon: "💡",
      label: t("qaMath"),
      subtitle: t("qaMathSubtitle"),
      intent: "math",
      category: "math",
    },
    {
      id: "plan",
      icon: "🧠",
      label: t("qaPlan"),
      subtitle: t("qaPlanSubtitle"),
      intent: "task",
      category: "internal",
    },
    {
      id: "summarize",
      icon: "📄",
      label: t("qaSummarize"),
      subtitle: t("qaSummarizeSubtitle"),
      intent: "research",
      category: "research",
    },
    {
      id: "code",
      icon: "🐛",
      label: t("qaCode"),
      subtitle: t("qaCodeSubtitle"),
      intent: "coding",
      category: "coding",
    },
    {
      id: "job_search",
      icon: "💼",
      label: t("qaJobSearch"),
      subtitle: t("qaJobSearchSubtitle"),
      intent: "research",
      category: "jobs",
    },
  ];

  const moreActions: QuickActionDef[] = [
    {
      id: "call",
      icon: "📞",
      label: t("qaCall"),
      subtitle: t("qaCallSubtitle"),
      intent: "task",
      category: "comms",
    },
    {
      id: "translate",
      icon: "🌐",
      label: t("qaTranslate"),
      subtitle: t("qaTranslateSubtitle"),
      intent: "task",
      category: "translate",
    },
    {
      id: "analyze",
      icon: "📊",
      label: t("qaAnalyzeDoc"),
      subtitle: t("qaAnalyzeDocSubtitle"),
      intent: "task",
      category: "documents",
    },
  ];

  useEffect(() => {
    let cancelled = false;
    (async () => {
      /* If we just kicked off a stream (e.g. auto-send from the Workspace
         "Start coding" handoff), don't clobber the in-flight messages with
         setMessages([]) — the streaming handler is already updating state live.
         This guard must run before the !active?.id check below, otherwise a
         fresh mount with active=null + initialPrompt would wipe the just-added
         user message (the "first message doesn't respond" regression). */
      if (streamingRef.current) return;
      if (!active?.id) {
        setMessages([]);
        setModel("");
        setRamRoute(null);
        return;
      }
      try {
        const msgs = await api<ChatMessage[]>(`/api/conversations/${active.id}/messages`);
        if (cancelled) return;
        setMessages(
          msgs
            .filter((m) => m.role !== "tool")
            .map((m) => ({ role: m.role as "user" | "assistant", text: m.content, finalized: true, created_at: m.created_at })),
        );
      } catch {
        /* ignore — chat stays usable */
      }
    })();
    return () => { cancelled = true; };
  }, [active?.id]);

  useEffect(() => {
    let cancelled = false;
    api<ApprovalInfo[]>("/api/approvals")
      .then((items) => {
        if (cancelled || approval) return;
        const pendingEmail = items.find(
          (item) => item.status === "pending"
            && ["send_email", "send_email_batch"].includes(item.tool_name),
        );
        if (pendingEmail) {
          setApproval({
            ...pendingEmail,
            standalone: pendingEmail.standalone ?? pendingEmail.run_id === "",
          });
        }
      })
      .catch((err) => {
        if (!cancelled) onToast(errorMessage(err, t), true);
      });
    return () => {
      cancelled = true;
    };
  }, [active?.id, approval, onToast, t]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const s = await api<AppSettings>("/api/settings");
        if (!cancelled && s.voice) setVoice(s.voice);
        if (!cancelled && s.timezone) setTimezone(s.timezone);
      } catch {
        /* voice stays off if settings can't be loaded */
      }
    })();
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    voiceRef.current = voice;
  });

  // MathJax is loaded via an async CDN script, so it may not be ready yet when a
  // reply finalises. Re-typeset the messages area once MathJax is available and
  // only when the app is idle (not mid-stream) so we don't thrash the typeset
  // engine on every token.
  useEffect(() => {
    if (busy) return;
    let raf: number | undefined;
    let attempts = 0;
    const tick = () => {
      const mj = getMathJax();
      if (mj?.typesetPromise) {
        void mj.typesetPromise().catch(() => {});
        return;
      }
      if (attempts++ < 30) {
        raf = window.setTimeout(tick, 120);
      }
    };
    tick();
    return () => {
      if (raf !== undefined) window.clearTimeout(raf);
    };
  }, [messages.length, busy]);

  useEffect(() => {
    const composer = composerRef.current;
    if (!composer) return;
    composer.style.height = "auto";
    composer.style.height = `${Math.min(composer.scrollHeight, 180)}px`;
  }, [input]);

  const patch = (idx: number, changes: Partial<UiMessage>) =>
    setMessages((prev) => prev.map((m, i) => (i === idx ? { ...m, ...changes } : m)));

  const copyMessage = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      onToast(t("copiedToClipboard"));
    } catch {
      onToast(t("copyFailed"), true);
    }
  };

  const editPrompt = (index: number, text: string) => {
    setInput(text);
    setEditingPromptIndex(index);
    requestAnimationFrame(() => {
      const composer = composerRef.current;
      composer?.focus();
    });
  };

  const activateQuickAction = (action: QuickActionDef) => {
    setPromptAction(action);
    setShowMoreActions(false);
    requestAnimationFrame(() => composerRef.current?.focus());
  };

  const syncPending = (next: PendingMedia[]) => {
    pendingRef.current = next;
    setPendingMedia(next);
  };

  const DOC_EXTS = /\.(pdf|txt|md|csv|json|log|py|html|docx|xlsx|pptx)$/i;

  const attachFiles = async (files: FileList | File[] | null) => {
    if (!files) return;
    for (const f of Array.from(files)) {
      // Plain office/text files keep the classic document-upload flow (§6);
      // anything sniffable as media becomes a chat attachment (§42).
      if (DOC_EXTS.test(f.name)) {
        await uploadFile(f);
        continue;
      }
      const entry: PendingMedia = {
        id: "",
        name: f.name,
        kind: f.type.startsWith("audio/") ? "audio"
          : f.type.startsWith("video/") ? "video" : "image",
        url: "",
        uploading: true,
      };
      syncPending([...pendingRef.current, entry]);
      try {
        const media = await uploadMedia(f);
        syncPending(pendingRef.current.map((p) =>
          p === entry
            ? { id: media.id, name: media.filename, kind: media.kind, url: media.url, uploading: false }
            : p));
      } catch (err) {
        syncPending(pendingRef.current.filter((p) => p !== entry));
        onToast((err as Error).message, true);
      }
    }
  };

  const send = async (text: string) => {
    if (busy) return;
    if (pendingRef.current.some((p) => p.uploading)) {
      onToast(t("mediaUploading"), true);
      return;
    }
    const media_ids = pendingRef.current
      .filter((p) => p.kind !== "document")
      .map((p) => p.id)
      .filter(Boolean);
    const document_ids = pendingRef.current
      .filter((p) => p.kind === "document")
      .map((p) => p.id)
      .filter(Boolean);
    const finalText = text.trim() || (
      document_ids.length
        ? t("documentOnlyPrompt")
        : media_ids.length
          ? t("mediaOnlyPrompt")
          : ""
    );
    if (!finalText) return;
    const intentHint = promptAction?.intent;
    const capabilityHint = promptAction?.category;
    syncPending([]);
    setBusy(true);
    streamingRef.current = true;
    setApproval(null);
    setBrowserStatus(null);
    setCallResult(null);
    setInput("");
    setPromptAction(null);
    setEditingPromptIndex(null);
    const sentAt = new Date().toISOString();
    const userMsg: UiMessage = { role: "user", text: finalText, finalized: true, created_at: sentAt };
    const replyIdx = messages.length + 1;
    setMessages((prev) => [
      ...prev, userMsg, { role: "assistant", text: "", tools: [], created_at: sentAt },
    ]);

    let liveText = "";
    let streamed = false;
    let terminalReply: string | null = null;
    runningChip.current.clear();

    const finalize = (idx: number, changes: Partial<UiMessage>) => {
      setMessages((prev) => prev.map((m, i) => (i === idx ? { ...m, ...changes } : m)));
    };

    const directEmail = media_ids.length || document_ids.length
      ? false
      : isDirectEmailRequest(finalText);
    if (directEmail) {
      try {
        const result = await api<EmailTaskResult>("/api/emails/task", {
          method: "POST",
          body: { request: finalText },
        });
        if (result.approval) {
          finalize(replyIdx, {
            text: t("emailApprovalReview"),
            finalized: true,
            intent: "email",
          });
          setApproval({ ...result.approval, standalone: true });
        } else if (result.status === "error") {
          throw new Error(result.error?.detail || t("emailDraftFailed"));
        } else {
          throw new Error(t("emailDraftFailed"));
        }
      } catch (err) {
        finalize(replyIdx, {
          text: errorMessage(err, t),
          finalized: true,
          error: true,
        });
      } finally {
        streamingRef.current = false;
        setBusy(false);
        onReloadConversations();
      }
      return;
    }

    const ctrl = new AbortController();
    abortRef.current = ctrl;
    runRef.current = null;

    try {
      const resp = await fetch("/api/chat", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${getToken()}`,
        },
        body: JSON.stringify({
          message: finalText,
          conversation_id: active?.id ?? convIdRef.current ?? null,
          stream: true,
          media_ids,
          document_ids,
          mode: mode,
          intent_hint: intentHint,
          capability_hint: capabilityHint,
          math_level: mathLevel,
          show_working: showWorking,
        }),
        signal: ctrl.signal,
      });
      if (resp.status === 401) {
        clearToken();
        onAuthError?.();
        onToast(t("errAuthRequired"), true);
        setBusy(false);
        streamingRef.current = false;
        return;
      }
      if (!resp.ok) {
        const data = (await resp.json().catch(() => ({}))) as {
          error?: { code?: string; details?: string };
        };
        throw new ApiError(data.error?.code || "ERROR", data.error?.details || "Request failed", resp.status);
      }

      if (!resp.body) {
        throw new ApiError("EMPTY_RESPONSE", "Server returned no response body", resp.status);
      }
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      const handle = (event: Record<string, unknown>) => {
        switch (event.type) {
          case "start":
            /* Immediate feedback that the backend accepted the turn and is
               routing / warming the model — the spinner already shows, but this
               confirms the SSE pipe is live. */
            break;
          case "meta": {
            const conv = event.conversation_id as string | undefined;
            if (conv && !active?.id) {
              convIdRef.current = conv;
              /* Notify parent so the sidebar shows the new conversation. App.tsx
                 uses a separate chatKey for the ChatView key, so this state
                 update does NOT cause a remount mid-stream. */
              onConversation(conv);
            }
            if (event.model) setModel(String(event.model));
            runRef.current = {
              runId: String(event.run_id || ""),
              taskId: String(event.task_id || ""),
            };
            onReloadConversations();
            break;
          }
          case "token": {
            const tk = (event.text as string) || "";
            if (!streamed) {
              liveText += tk;
              finalize(replyIdx, { text: liveText, finalized: false });
            }
            break;
          }
          case "tool_start": {
            const name = String(event.name || "tool");
            const callId = String(event.call_id || "");
            setMessages((prev) => prev.map((m, i) => {
              if (i !== replyIdx) return m;
              const tools = [...(m.tools || []), {
                name, status: "running" as const, label: `${toolLabel(name, t)}…`,
                call_id: callId || undefined,
              }];
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
            const detailText = toolDetailText(detail);
            setMessages((prev) => prev.map((m, i) => {
              if (i !== replyIdx) return m;
              const tools = (m.tools || []).map((c) => {
                const match = callId ? (c.call_id === callId)
                  : (c.name === (event.name as string || "") && c.status === "running");
                // Coding/exec tools carry full output via `detail`; keep the
                // chip label short and expose the rest in the expandable feed.
                const label = detailText
                  ? `${toolLabel(c.name, t)} ${ok ? "✓" : "✗"}`
                  : `${toolLabel(c.name, t)} ${ok ? "✓" : "✗"}${msg ? ` — ${msg.slice(0, 80)}` : ""}`;
                return match
                  ? { ...c, status: ok ? ("ok" as const) : ("fail" as const), detail, label }
                  : c;
              });
              return { ...m, tools };
            }));
            if (callId) runningChip.current.delete(callId);
            break;
          }
          case "approval":
            setApproval(event as unknown as ApprovalInfo);
            break;
          case "intent": {
            const intent = String(event.intent || "");
            const signals = Array.isArray(event.signals) ? event.signals.map(String) : [];
            if (intent) {
              finalize(replyIdx, {
                intent,
                agentic: intent === "task" || signals.includes("multi_step"),
              });
            }
            break;
          }
          case "plan": {
            const steps = (event.steps as PlanStep[]) || [];
            const reason = (event.reason as string) || undefined;
            if (steps.length) {
              finalize(replyIdx, {
                plan: {
                  steps,
                  progress: Number(event.progress || 0),
                  current_step: Number(event.current_step || 0),
                  reason,
                },
              });
            }
            break;
          }
          case "browser_status": {
            const bs = event.browser as BrowserStatus | undefined;
            if (bs) setBrowserStatus(bs);
            break;
          }
          case "artifact": {
            const artifact = event.artifact as Artifact | undefined;
            if (artifact?.url) {
              setMessages((prev) => prev.map((m, i) =>
                i === replyIdx
                  ? { ...m, artifacts: [...(m.artifacts || []), artifact] }
                  : m));
            }
            break;
          }
          case "diagram": {
            const d = event.diagram as DiagramRef | undefined;
            if (d?.id) {
              setMessages((prev) => prev.map((m, i) =>
                i === replyIdx
                  ? { ...m, diagrams: [...(m.diagrams || []), d] }
                  : m));
            }
            break;
          }
          case "call_result": {
            /* Priority 3 (§31-33): phone call result — display contact info,
               call status, duration, and summary inline with the reply. */
            setCallResult(event as unknown as CallResult);
            break;
          }
          case "citations": {
            const c = (event.citations as CitationRef[] | undefined) || [];
            if (c.length) finalize(replyIdx, { citations: c });
            break;
          }
          case "done": {
            streamed = true;
            terminalReply = String(event.reply || liveText);
            finalize(replyIdx, {
              text: terminalReply,
              finalized: true,
              created_at: new Date().toISOString(),
              sources: (event.sources as SourceRef[]) || [],
              intent: String(event.intent || "") || undefined,
              plan: (event.plan as ChatPlan) || undefined,
              artifacts: (event.artifacts as Artifact[]) || undefined,
              citations: (event.citations as CitationRef[]) || undefined,
              diagrams: (event.diagrams as DiagramRef[]) || undefined,
            });
            if (event.status === "paused_approval" && event.approval) {
              setApproval(event.approval as ApprovalInfo);
            }
            if (event.status === "paused") {
              setPausedRun(String(event.run_id || ""));
            }
            onReloadConversations();
            afterReplyRef.current(terminalReply);
            break;
          }
          case "ollama_route": {
            const r = event as unknown as OllamaRouteEvent;
            setRamRoute(r);
            onRamRoute?.(r);
            break;
          }
          case "error": {
            const err = event.error as { code?: string; details?: string } | undefined;
            runningChip.current.clear();
            finalize(replyIdx, {
              text: friendlyError(err?.code, err?.details, t),
              finalized: true,
              error: true,
            });
            break;
          }
        }
      };

      const dispatch = (block: string) => {
        const dataLine = block.split("\n").find((line) => line.startsWith("data:"));
        if (!dataLine) return;
        try {
          handle(JSON.parse(dataLine.slice(5).trim()));
        } catch {
          handle({
            type: "error",
            error: { code: "MALFORMED_SSE", details: "Received malformed server event." },
          });
        }
      };

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let idx: number;
        while ((idx = buffer.indexOf("\n\n")) >= 0) {
          const block = buffer.slice(0, idx);
          buffer = buffer.slice(idx + 2);
          dispatch(block);
        }
      }
      buffer += decoder.decode();
      if (buffer.trim()) dispatch(buffer);
      if (streamed) {
        finalize(replyIdx, {
          text: terminalReply ?? liveText,
          finalized: true,
        });
      }
    } catch (err) {
      if ((err as Error)?.name === "AbortError") {
        finalize(replyIdx, {
          text: liveText
            ? `${liveText}\n\n⏹ ${t("stopped")}`
            : `⏹ ${t("stopped")}`,
          finalized: true,
        });
      } else {
        const message = errorMessage(err, t);
        if (!String(message).includes("Invalid JSON")) {
          finalize(replyIdx, { text: message, finalized: true, error: true });
        }
      }
    } finally {
      abortRef.current = null;
      runRef.current = null;
      runningChip.current.clear();
      streamingRef.current = false;
      setBusy(false);
    }
  };

  const stop = () => {
    const run = runRef.current;
    abortRef.current?.abort();
    // Ask the backend to halt the run so no further tools execute server-side.
    if (run?.runId) {
      api(`/api/runs/${run.runId}/stop`, { method: "POST" }).catch(() => {
        /* run may already be finished — the abort above is enough */
      });
    }
  };

  const pause = async () => {
    const run = runRef.current;
    if (!run?.runId) return;
    try {
      await api(`/api/runs/${run.runId}/pause`, { method: "POST" });
      onToast(t("pauseRequested"));
    } catch (err) {
      onToast((err as Error).message, true);
    }
  };

  const resume = async () => {
    const runId = pausedRun;
    if (!runId) return;
    const replyIdx = messages.length;
    setPausedRun(null);
    setMessages((prev) => [...prev, { role: "assistant", text: "", created_at: new Date().toISOString() }]);
    try {
      const data = await api<ChatResponse>(
        `/api/runs/${runId}/resume`,
        { method: "POST" },
      );
      patch(replyIdx, {
        text: data.reply || "",
        finalized: true,
        sources: data.sources,
        citations: data.citations,
        diagrams: data.diagrams,
      });
      if (data.approval) setApproval(data.approval);
      if (data.model) setModel(data.model);
    } catch (err) {
      patch(replyIdx, { text: errorMessage(err, t), finalized: true, error: true });
    }
    onReloadConversations();
  };

  const decide = async (approve: boolean) => {
    const id = approval?.approval_id || approval?.id;
    if (!id) return;
    const needsCredentials =
      approval?.kind === "credentials" || Boolean(approval?.credential);
    if (approve && needsCredentials && (!credUsername || !credSecret)) {
      onToast(t("credMissing"), true);
      return;
    }
    if (
      approve
      && ["send_email", "send_email_batch", "request_user_input"].includes(
        approval?.tool_name || "",
      )
      && approvalDraft
    ) {
      if (!await saveApprovalDraft()) return;
    }
    const standaloneEmail = approval?.standalone === true
      || (approval?.tool_name === "send_email" && approval?.run_id === "");
    setApproval(null);
    setApproving(true);
    const replyIdx = messages.length;
    setMessages((prev) => [...prev, { role: "assistant", text: "", created_at: new Date().toISOString() }]);
    try {
      const payload =
        approve && needsCredentials
          ? { credentials: { username: credUsername, secret: credSecret } }
          : undefined;
      const data = await api<ChatResponse | { status: string }>(
        standaloneEmail
          ? `/api/emails/send/${id}/${approve ? "approve" : "reject"}`
          : `/api/approvals/${id}/${approve ? "approve" : "reject"}`,
        { method: "POST", body: payload },
      );
      const responseText = standaloneEmail
        ? approve
          ? t("emailSent")
          : t("emailRejected")
        : (data as ChatResponse).reply || (approve ? t("approvedMsg") : t("rejectedMsg"));
      patch(replyIdx, {
        text: responseText,
        finalized: true,
        sources: (data as ChatResponse).sources,
        artifacts: (data as ChatResponse).artifacts || undefined,
        citations: (data as ChatResponse).citations,
        diagrams: (data as ChatResponse).diagrams,
      });
      if ("approval" in data && data.approval) setApproval(data.approval);
      if ("model" in data && data.model) setModel(data.model);
    } catch (err) {
      patch(replyIdx, { text: errorMessage(err, t), finalized: true, error: true });
    } finally {
      setApproving(false);
      setCredUsername("");
      setCredSecret("");
    }
    onReloadConversations();
  };

  const saveApprovalDraft = async (): Promise<boolean> => {
    const id = approval?.approval_id || approval?.id;
    if (
      !id
      || !["send_email", "send_email_batch", "request_user_input"].includes(
        approval?.tool_name || "",
      )
      || !approvalDraft
    ) return false;
    setApprovalDraftSaving(true);
    try {
      const updated = await api<ApprovalInfo>(
        (approval.standalone || approval.run_id === "")
          ? `/api/emails/send/${id}`
          : `/api/approvals/${id}`,
        {
          method: "PATCH",
          body: (approval.standalone || approval.run_id === "")
            ? approvalDraft
            : { arguments: approvalDraft },
        },
      );
      setApproval((current) => current ? { ...current, ...updated, arguments: updated.arguments } : current);
      setApprovalDraft(updated.arguments);
      onToast(t("changesSaved"));
      return true;
    } catch (err) {
      onToast((err as Error).message, true);
      return false;
    } finally {
      setApprovalDraftSaving(false);
    }
  };

  const addApprovalAttachments = async (files: File[], emailIndex = 0) => {
    if (!approvalDraft) return;
    setApprovalAttachmentUploading(true);
    try {
      for (const file of files) {
        const attachment = await uploadEmailAttachment(file);
        const reference = {
          url: attachment.url,
          filename: attachment.filename,
          mime_type: attachment.mime_type,
          size_bytes: attachment.size_bytes,
        };
        setApprovalDraft((current) => {
          if (!current) return current;
          if (approval?.tool_name === "send_email_batch") {
            const emails = Array.isArray(current.emails)
              ? current.emails as Array<Record<string, unknown>>
              : [];
            return {
              ...current,
              emails: emails.map((email, index) => {
                if (index !== emailIndex) return email;
                const attachments = Array.isArray(email.attachments)
                  ? [...email.attachments, reference]
                  : [reference];
                return { ...email, attachments };
              }),
            };
          }
          const attachments = Array.isArray(current.attachments)
            ? [...current.attachments, reference]
            : [reference];
          return { ...current, attachments };
        });
      }
      onToast(t("attachmentAdded"));
    } catch (err) {
      onToast((err as Error).message, true);
    } finally {
      setApprovalAttachmentUploading(false);
    }
  };

  const removeApprovalAttachment = (index: number, emailIndex = 0) => {
    if (!approvalDraft) return;
    if (approval?.tool_name === "send_email_batch") {
      const emails = Array.isArray(approvalDraft.emails)
        ? approvalDraft.emails as Array<Record<string, unknown>>
        : [];
      setApprovalDraft({
        ...approvalDraft,
        emails: emails.map((email, currentIndex) => {
          if (currentIndex !== emailIndex || !Array.isArray(email.attachments)) {
            return email;
          }
          return {
            ...email,
            attachments: email.attachments.filter(
              (_, attachmentIndex) => attachmentIndex !== index,
            ),
          };
        }),
      });
      return;
    }
    if (!Array.isArray(approvalDraft.attachments)) return;
    const attachments = approvalDraft.attachments.filter((_, itemIndex) => itemIndex !== index);
    setApprovalDraft({ ...approvalDraft, attachments });
  };

  const proposeCreds = () => void decide(true);

  const uploadFile = async (file: File) => {
    const entry: PendingMedia = {
      id: "",
      name: file.name,
      kind: "document",
      uploading: true,
    };
    syncPending([...pendingRef.current, entry]);
    try {
      onToast(t("uploading").replace("{name}", file.name));
      const doc = await uploadDocument(file);
      if (doc.status !== "ready") {
        syncPending(pendingRef.current.filter((item) => item !== entry));
        onToast(`${doc.filename}: ${doc.error || doc.status}`, true);
        return;
      }
      syncPending(pendingRef.current.map((item) =>
        item === entry
          ? { ...item, id: doc.id, name: doc.filename, uploading: false }
          : item));
      onToast(t("docReady").replace("{name}", doc.filename));
    } catch (err) {
      syncPending(pendingRef.current.filter((item) => item !== entry));
      onToast((err as Error).message, true);
    }
  };

  /* ---- Phase H (§46-48): voice interface --------------------------------- */
  const patchVoice = async (changes: Partial<VoiceSettings>) => {
    const next = { ...voiceRef.current, ...changes };
    setVoice(next);
    try {
      const saved = await api<AppSettings>("/api/settings", {
        method: "PATCH", body: { voice: next },
      });
      if (saved.voice) setVoice(saved.voice);
    } catch (err) {
      onToast((err as Error).message, true);
    }
  };

  const deliverTranscript = (raw: string) => {
    const clean = raw.trim();
    if (!clean) return;
    if (voiceRef.current.enabled && voiceRef.current.continuous) {
      void send(clean);
    } else {
      setInput((prev) => (prev ? `${prev} ${clean}` : clean));
    }
  };

  const startRecognition = (): boolean => {
    const recognition = createRecognizer();
    if (!recognition) return false;
    let finalText = "";
    recognition.onresult = (event) => {
      const { finalText: done, interimText } = readRecognition(event);
      finalText += done;
      setVoicePartial((finalText + interimText).trim());
    };
    recognition.onerror = (event) => {
      setListening(false);
      setVoicePartial("");
      if (event.error !== "aborted" && event.error !== "no-speech" &&
          event.error !== "audio-capture") {
        onToast(t("voiceNoSpeech"), true);
      }
    };
    recognition.onend = () => {
      recognitionRef.current = null;
      setListening(false);
      setVoicePartial("");
      const text = finalText.trim();
      if (text) deliverTranscript(text);
    };
    recognitionRef.current = recognition;
    setListening(true);
    try {
      recognition.start();
    } catch {
      recognitionRef.current = null;
      setListening(false);
      return false;
    }
    return true;
  };

  const startClipRecording = async () => {
    try {
      const recorder = await recordUntilStopped();
      if (!recorder) {
        onToast(t("voiceNotSupported"), true);
        return;
      }
      recorderRef.current = recorder;
      setListening(true);
    } catch {
      onToast(t("voiceNotSupported"), true);
    }
  };

  const startListening = () => {
    if (listening || transcribing) return;
    stopSpeaking();
    setSpeaking(false);
    if (!startRecognition()) void startClipRecording();
  };

  const stopListening = () => {
    const recognition = recognitionRef.current;
    if (recognition) {
      recognition.stop(); // onend delivers the final transcript
      return;
    }
    const recorder = recorderRef.current;
    recorderRef.current = null;
    setListening(false);
    setVoicePartial("");
    if (!recorder) return;
    void recorder.stop().then(async (blob) => {
      if (!blob) return;
      setTranscribing(true);
      try {
        const result = await transcribeAudio(blob, "clip.webm");
        deliverTranscript(result.transcript);
      } catch (err) {
        onToast((err as Error).message, true);
      } finally {
        setTranscribing(false);
      }
    });
  };

  const toggleMic = () => {
    if (listening) stopListening();
    else startListening();
  };

  const toggleContinuous = () => {
    const next = !voiceRef.current.continuous;
    void patchVoice({ continuous: next });
    onToast(next ? t("voiceContinuousOn") : t("voiceContinuousOff"));
    if (next) startListening();
    else stopListening();
  };

  const speakNow = (text: string) => {
    stopSpeaking();
    if (!text.trim()) return;
    setSpeaking(true);
    void speak(text, voiceRef.current.voice).then(() => setSpeaking(false));
  };

  const mute = () => {
    stopSpeaking();
    setSpeaking(false);
  };

  useEffect(() => {
    // Auto-speak + continuous loop: run after each finalized assistant reply.
    afterReplyRef.current = (text: string) => {
      const v = voiceRef.current;
      if (!v.enabled || !text.trim()) return;
      if (v.auto_speak) {
        setSpeaking(true);
        void speak(text, v.voice).then(() => {
          setSpeaking(false);
          if (voiceRef.current.continuous) startListening();
        });
      } else if (v.continuous) {
        startListening();
      }
    };
  });

  const empty = messages.length === 0;

  // Kick off a coding run handed over from the Workspace page ("Start coding").
  useEffect(() => {
    if (!initialPrompt || autoSentRef.current) return;
    autoSentRef.current = true;
    onPromptConsumed?.();
    void send(initialPrompt);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <>
      <div className="view-header">
        {active?.title && <h2>{active.title}</h2>}
        {model && <span className="model-badge">{model}</span>}
      </div>

      <div className="msg-list">
        {empty && (
          <div className="chat-hero empty-state">
            <div className="hero-row">
              <div className="hero-greeting">
                <h1>{user?.name ? t("greetingName").replace("{name}", user.name) : t("greeting")}</h1>
                <p className="greeting-sub">{t("greetingHint")}</p>
              </div>
              <div className="robot-stage" aria-hidden="true">
                <svg
                  viewBox="0 0 120 120"
                  width="240"
                  height="240"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  <circle cx="60" cy="44" r="20" />
                  <path d="M40 64v20a20 20 0 0 0 40 0V64" />
                  <path d="M30 80h60" />
                  <circle cx="50" cy="96" r="2" />
                  <circle cx="70" cy="96" r="2" />
                  <path d="M38 100h44a6 6 0 0 1 6 6v2" />
                  <path d="M60 64V32a4 4 0 0 1 2-3.5h0a4 4 0 0 1 3.5 2l0 4.5" />
                  <rect x="46" y="78" width="28" height="4" rx="2" />
                </svg>
              </div>
              <div className="empty-logo" style={{ display: "none" }}><AssistantAvatar size={80} /></div>
            </div>
            <div className="quick-actions suggestions-grid">
              {primaryActions.map((a) => (
                <button
                  key={a.id}
                  className="quick-action suggestion"
                  onClick={() => activateQuickAction(a)}
                >
                  <span className="quick-icon">{a.icon}</span>
                  <strong>{a.label}</strong>
                  <p>{a.subtitle}</p>
                </button>
              ))}
              <button
                className="quick-action more"
                onClick={() => setShowMoreActions(true)}
              >
                <span className="quick-icon">⋯</span>
                <strong>{t("qaMore")}</strong>
                <p>{t("qaMoreSubtitle")}</p>
              </button>
            </div>

            {showMoreActions && (
              <div className="prompt-backdrop" onClick={() => setShowMoreActions(false)}>
                <div className="prompt-dialog" onClick={(e) => e.stopPropagation()}>
                  <h3>{t("qaMore")}</h3>
                  <div className="quick-actions suggestions-grid">
                    {moreActions.map((a) => (
                      <button
                        key={a.id}
                        className="quick-action suggestion"
                        onClick={() => activateQuickAction(a)}
                      >
                        <span className="quick-icon">{a.icon}</span>
                        <strong>{a.label}</strong>
                        <p>{a.subtitle}</p>
                      </button>
                    ))}
                  </div>
                  <div className="row gap" style={{ marginTop: "14px" }}>
                    <button className="btn small" onClick={() => setShowMoreActions(false)}>{t("qaCancel")}</button>
                  </div>
                </div>
              </div>
            )}
          </div>
        )}

        {messages.map((m, i) => {
          const messageDate = m.created_at ? new Date(m.created_at) : null;
          const previousDate = i > 0 && messages[i - 1].created_at
            ? new Date(messages[i - 1].created_at as string)
            : null;
          const formatDate = (value: Date) => new Intl.DateTimeFormat(undefined, {
            timeZone: displayTimezone, year: "numeric", month: "2-digit", day: "2-digit",
          }).format(value);
          const dateKey = messageDate ? formatDate(messageDate) : "";
          const previousDateKey = previousDate ? formatDate(previousDate) : "";
          const showDateSeparator = Boolean(dateKey && dateKey !== previousDateKey);
          const localTime = messageDate ? new Intl.DateTimeFormat(undefined, {
            timeZone: displayTimezone, dateStyle: "medium", timeStyle: "short",
          }).format(messageDate) : "";
          const planFull =
            Array.isArray(m.plan?.steps) &&
            m.plan.steps.length > 0 &&
            m.plan.steps.every((s) => s.status === "done" || s.status === "skipped");
          return (
          <Fragment key={i}>
            {showDateSeparator && <div className="date-separator">{dateKey}</div>}
            <div className={`msg ${m.role} message`}>
              <div className="avatar">{m.role === "user" ? "🧑" : <AssistantAvatar size={30} />}</div>
              <div className="bubble">
              <div className="who">{m.role === "user" ? t("you") : t("jalora")}</div>
              {localTime && <time className="msg-time" dateTime={m.created_at}>{localTime}</time>}
              <div className="content">
                {m.error
                  ? <div className="error-bubble">⚠ {m.text}</div>
                  : (
                    <>
                      <Markdown
                        text={m.role === "assistant" ? stripInternalModelMarkup(m.text).trim() : m.text}
                        copyLabel={t("copy")}
                        onCopyMath={(source) => void copyMessage(source)}
                      />
                      {m.role === "assistant" && Array.isArray(m.diagrams) && m.diagrams.length > 0 && (
                        <div className="diagrams">
                          {m.diagrams.map((d) => (
                            <DiagramRenderer key={d.id} diagram={d} downloadLabel={t("download")} />
                          ))}
                        </div>
                      )}
                      {m.role === "assistant" && !m.finalized && m.text && (
                        <span className="cursor">▌</span>
                      )}
                    </>
                  )}
                {m.intent && m.role === "assistant" && (
                  <div className="plan-icon-row">
                    <span className="plan-icon">◇</span>
                    <span className="intent-label">{t("intentLabel")}: {m.intent}</span>
                    {m.agentic && <span className="intent-label">{t("agentTasks")}</span>}
                  </div>
                )}
                {Array.isArray(m.plan?.steps) && m.plan.steps.length > 0 && (
                  <div className="task-plan">
                    <div className="task-bar small">
                      <div
                        className={`task-bar-fill${planFull ? " done" : m.finalized ? "" : " running"}`}
                        style={{ width: `${planFull ? 100 : Math.max(4, Math.min(100, m.plan.progress || 0))}%` }}
                      />
                    </div>
                    {(m.plan.steps ?? []).map((step, si) => (
                      <div key={si} className={`plan-step ${step.status}`}>
                        <span className="plan-icon">{STEP_ICON[step.status] || "○"}</span>
                        <span className="plan-desc">{step.description}</span>
                      </div>
                    ))}
                    {m.plan.reason && (
                      <div className="plan-reason">↻ {m.plan.reason}</div>
                    )}
                  </div>
                )}
                {Array.isArray(m.tools) && m.tools.length > 0 && (
                  <div>
                    {m.tools.map((c, ci) => (
                      <span key={ci} className={`tool-chip ${c.status === "running" ? "" : c.status}`}>
                        <span className="dot" />
                        <span className="label">{c.label}</span>
                      </span>
                    ))}
                    {m.tools.some((c) => toolDetailText(c.detail)) && (
                      <div className="ws-tool-details">
                        {m.tools.filter((c) => toolDetailText(c.detail)).map((c, ci) => (
                          <details key={ci} className="tool-detail">
                            <summary>{c.label}</summary>
                            <pre>{toolDetailText(c.detail)}</pre>
                          </details>
                        ))}
                      </div>
                    )}
                  </div>
                )}
                {m.role === "assistant" &&
                  !m.finalized &&
                  !m.text &&
                  !m.error &&
                  (!Array.isArray(m.tools) || m.tools.length === 0) &&
                  !m.plan &&
                  (m.artifacts ?? []).length === 0 &&
                  (m.sources ?? []).length === 0 && (
                    <div className="thinking">
                      <span className="pm-spinner">…</span> {m.intent === "research" ? t("searchingResearch") : t("searchingThink")}
                    </div>
                  )}
                {Array.isArray(m.artifacts) && m.artifacts.length > 0 && (
                  <div className="msg-artifacts">
                    {m.artifacts.map((a, ai) => {
                      const isImg =
                        a.kind === "screenshot" ||
                        a.kind === "plot" ||
                        (a.mime && a.mime.startsWith("image/"));
                      return isImg ? (
                        <figure key={ai} className="artifact">
                          <ArtifactImage url={a.url} alt={a.title || a.kind} />
                          <figcaption className="artifact-cap">
                            <span className="artifact-title">{a.title || a.kind}</span>
                            <button
                              type="button"
                              className="artifact-dl"
                              onClick={() => void downloadArtifact(a, t("download"))}
                              title={t("download")}
                            >
                              ⤓ {t("download")}
                            </button>
                          </figcaption>
                        </figure>
                      ) : (
                        <button
                          key={ai}
                          type="button"
                          className="artifact-link"
                          onClick={() => void downloadArtifact(a, t("download"))}
                          title={t("download")}
                        >
                          📎 {a.title || a.kind} <span aria-hidden="true">⤓</span>
                        </button>
                      );
                    })}
                  </div>
                )}
                {Array.isArray(m.sources) && m.sources.length > 0 && (
                  <div className="sources">
                    <div className="src-label">🔗 {t("sourcesLabel")} ({m.sources.length})</div>
                    {m.sources.slice(0, 8).map((s, si) => (
                      <a key={si} href={s.url} target="_blank" rel="noopener noreferrer">
                        ▸ {s.title || s.url}
                      </a>
                    ))}
                  </div>
                )}
                {Array.isArray(m.citations) && m.citations.length > 0 && m.role === "assistant" && (
                  <div className="references">
                    <div className="src-label">📚 {t("references")} ({m.citations.length})</div>
                    {m.citations.map((c) => (
                      <a
                        key={c.index}
                        href={c.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="citation-ref"
                      >
                        [{c.index}] {c.title}
                      </a>
                    ))}
                  </div>
                )}
                {m.role === "assistant" && m.text && !m.error && (
                  <button
                    className="speak-btn"
                    title={t("voiceSpeak")}
                    onClick={() => speakNow(m.text)}
                  >
                    🔊
                  </button>
                )}
              </div>
          {m.finalized && m.text && !m.error && (
            <div className="message-actions">
              {m.role === "user" ? (
                <>
                  <button
                    type="button"
                    className="message-action"
                    title={t("editTask")}
                    aria-label={t("editTask")}
                    onClick={() => editPrompt(i, m.text)}
                  >
                    ✎ {t("edit")}
                  </button>
                  <button
                    type="button"
                    className="message-action"
                    title={t("copyTask")}
                    aria-label={t("copyTask")}
                    onClick={() => void copyMessage(m.text)}
                  >
                    ⧉ {t("copy")}
                  </button>
                </>
              ) : (
                <button
                  type="button"
                  className="message-action"
                  title={t("copyResponse")}
                  aria-label={t("copyResponse")}
                  onClick={() => void copyMessage(stripInternalModelMarkup(m.text))}
                >
                  ⧉ {t("copy")}
                </button>
              )}
            </div>
          )}
          </div>
          </div>
          </Fragment>
          );
        })}
        <div ref={bottomRef} />
      </div>

      <div className="composer-wrap">
        {browserStatus?.session_open && (
          <div className="browser-strip">
            <span className="browser-dot" />
            <span className="browser-site">
              {browserStatus.title ? `${browserStatus.title} — ` : ""}
              <span className="browser-url">{browserStatus.url || ""}</span>
            </span>
          </div>
        )}
        {callResult && (
          <div className="call-result-strip">
            <span className="call-dot" />
            <div className="call-info">
              <span className="call-purpose">
                {callResult.purpose ? `${callResult.purpose}` : ""}
              </span>
              {callResult.to_phone && (
                <span className="call-phone">
                  {callResult.to_phone}
                </span>
              )}
              {callResult.status && (
                <span className={`call-status ${callResult.status}`}>
                  {callResult.status}
                </span>
              )}
              {callResult.duration_seconds != null && callResult.duration_seconds != undefined && (
                <span className="call-duration">
                  {Math.round(callResult.duration_seconds)}s
                </span>
              )}
              {callResult.summary && (
                <div className="call-summary">
                  {callResult.summary}
                </div>
              )}
            </div>
          </div>
        )}

        {approval && (
          <div className="approval-banner">
            <span>
              ⏳ <strong>{approval.tool_name}</strong> {t("approvalBanner")}
              {approval.credential?.domain && (
                <span className="banner-cred">
                  {" "}🔐 {approval.credential.domain}
                  {approval.field ? ` · ${approval.field}` : ""}
                </span>
              )}
            </span>
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
              <button className="btn small" onClick={() => decide(false)}>{t("deny")}</button>{" "}
              <button className="btn primary small" onClick={() => decide(true)}>{t("approve")}</button>
            </span>
          </div>
        )}
        {pausedRun && (
          <div className="approval-banner">
            <span>⏸ {t("pausedBanner")}</span>
            <span>
              {onOpenRun && (
                <button className="btn small" onClick={() => onOpenRun(pausedRun)}>
                  {t("taskOpenDetails")}
                </button>
              )}{" "}
              <button className="btn primary small" onClick={() => void resume()}>{t("resume")}</button>
            </span>
          </div>
        )}
        {(listening || transcribing) && (
          <div className="voice-strip">
            <span className="voice-dot" />
            <span className="voice-text">
              {transcribing ? t("voiceTranscribing") : t("voiceListening")}
            </span>
            {voicePartial && <span className="voice-partial">{voicePartial}</span>}
          </div>
        )}
        <div className="composer">
          {promptAction && (
            <div className="quick-action-context" role="status">
              <span>{promptAction.icon} {promptAction.label}</span>
              <button
                type="button"
                className="message-action"
                aria-label={t("qaCancel")}
                title={t("qaCancel")}
                onClick={() => setPromptAction(null)}
              >
                {t("qaCancel")}
              </button>
            </div>
          )}
          {editingPromptIndex !== null && (
            <div className="editing-prompt-banner">
              <span>
                <strong>{t("editingTask")}</strong>
                <span className="editing-prompt-hint">{t("editingTaskHint")}</span>
              </span>
              <button
                type="button"
                className="message-action"
                onClick={() => {
                  setEditingPromptIndex(null);
                  setInput("");
                }}
              >
                {t("cancelEdit")}
              </button>
            </div>
          )}
          {pendingMedia.length > 0 && (
            <div className="pending-media">
              {pendingMedia.map((p) => (
                <div key={p.id || p.name} className={`pm-chip ${p.kind}`}>
                  {p.uploading ? (
                    <span className="pm-spinner">…</span>
                  ) : p.kind === "image" ? (
                    <img className="pm-thumb" src={mediaUrl(p.url)} alt={p.name} />
                  ) : (
                    <span className="pm-icon">{p.kind === "audio" ? "🎵" : p.kind === "document" ? "📄" : "🎬"}</span>
                  )}
                  <span className="pm-name">{p.name}</span>
                  {!p.uploading && (
                    <button
                      className="pm-x"
                      title={t("remove")}
                      onClick={() =>
                        syncPending(pendingRef.current.filter((x) => x !== p))
                      }
                    >
                      ✕
                    </button>
                  )}
                </div>
              ))}
            </div>
          )}
          <button className="icon-btn" title={t("attachFile")} onClick={() => fileRef.current?.click()}>
            📎
          </button>
          <button
            id="btn-mic"
            className={`icon-btn mic-btn ${listening ? "mic-on" : ""}`}
            title={listening ? t("voiceStop") : t("voiceSpeak")}
            onClick={toggleMic}
            disabled={transcribing}
          >
            {transcribing ? "…" : "🎤"}
          </button>
          {voice.enabled && (
            <button
              id="btn-continuous"
              className={`icon-btn ${voice.continuous ? "mic-on" : ""}`}
              title={t("voiceContinuous")}
              onClick={toggleContinuous}
            >
              ♾
            </button>
          )}
          {speaking && (
            <button
              id="btn-mute"
              className="icon-btn"
              title={t("voiceMute")}
              onClick={mute}
            >
              🔇
            </button>
          )}
          <div className="mode-selector">
            <button
              className={`mode-btn ${mode === "chat" ? "active" : ""}`}
              onClick={() => setMode("chat")}
              title={t("modeChat")}
            >
              {t("modeChat")}
            </button>
            <button
              className={`mode-btn ${mode === "research" ? "active" : ""}`}
              onClick={() => setMode("research")}
              title={t("modeResearch")}
            >
              {t("modeResearch")}
            </button>
            <button
              className={`mode-btn ${mode === "agent" ? "active" : ""}`}
              onClick={() => setMode("agent")}
              title={t("modeAgent")}
            >
              {t("modeAgent")}
            </button>
          </div>
          <div className="math-controls">
            <label className="math-level" title={t("mathLevel")}>
              <span>{t("mathLevel")}</span>
              <select
                value={mathLevel}
                onChange={(e) => {
                  setMathLevel(e.target.value);
                  localStorage.setItem("jalora_math_level", e.target.value);
                }}
              >
                {MATH_LEVELS.map((l) => (
                  <option key={l.value} value={l.value}>{t(l.key)}</option>
                ))}
              </select>
            </label>
            <label className="show-working" title={t("showWorking")}>
              <input
                type="checkbox"
                checked={showWorking}
                onChange={(e) => {
                  setShowWorking(e.target.checked);
                  localStorage.setItem("jalora_show_working", String(e.target.checked));
                }}
              />
              <span>{t("showWorking")}</span>
            </label>
          </div>
          {ramRoute && (
            <span className={`ram-indicator ${ramRoute.tier === "light" || ramRoute.tier === "survival" ? "warning" : ""}`}>
              {t("ramIndicator").replace("{free}", ramRoute.free_memory_gb.toFixed(1))}
            </span>
          )}
          <textarea
            ref={composerRef}
            id="composer"
            rows={1}
            placeholder={promptAction
              ? `${promptAction.label}: ${t("placeholder")}`
              : t("placeholder")}
            value={input}
            onChange={(e) => {
              setInput(e.target.value);
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send(input);
              }
            }}
          />
          {busy ? (
            <>
              <button
                id="btn-pause"
                className="send-btn stop-btn"
                title={t("pause")}
                disabled={!runRef.current?.runId}
                onClick={() => void pause()}
              >
                ⏸
              </button>
              <button
                id="btn-stop"
                className="send-btn stop-btn"
                title={t("stop")}
                onClick={stop}
              >
                ■
              </button>
            </>
          ) : (
            <button
              id="btn-send"
              className="send-btn"
              title={t("send")}
              disabled={busy}
              onClick={() => send(input)}
            >
              ➤
            </button>
          )}
        </div>
        <div className="composer-hint">{t("hint")}</div>
      </div>

      <input
        ref={fileRef}
        type="file"
        multiple
        accept=".pdf,.txt,.md,.csv,.json,.docx,.xlsx,.pptx,image/*,audio/*,video/*"
        style={{ display: "none" }}
        onChange={(e) => {
          attachFiles(e.target.files);
          e.target.value = "";
        }}
      />

      {(() => {
        if (!approval) return null;
        const needsCredentials =
          approval?.kind === "credentials" || Boolean(approval?.credential);
        const domain = approval?.credential?.domain || "";
        return (
          <div className="modal-overlay">
            <div className={`modal${["send_email", "send_email_batch"].includes(approval?.tool_name || "") ? " approval-modal" : approval?.tool_name === "request_user_input" ? " user-input-modal" : ""}`}>
              <h3>
                {approval?.tool_name === "request_user_input"
                  ? t("userInputTitle")
                  : needsCredentials
                    ? t("credTitle")
                    : t("approvalTitle")}
              </h3>
              {needsCredentials ? (
                <div className="cred-form">
                  <p>
                    {t("credModal")
                      .replaceAll("{tool}", approval?.tool_name || "browser_fill")
                      .replaceAll("{domain}", domain || "?")}
                  </p>
                  {domain && (
                    <div className="cred-domain">
                      🔐 {domain}
                      {approval?.field ? ` · ${approval.field}` : ""}
                    </div>
                  )}
                  <label className="field">
                    <span>{t("credUsername")}</span>
                    <input
                      value={credUsername}
                      autoComplete="username"
                      onChange={(e) => setCredUsername(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" && !e.shiftKey) {
                          e.preventDefault();
                          proposeCreds();
                        }
                      }}
                    />
                  </label>
                  <label className="field">
                    <span>{t("credSecret")}</span>
                    <input
                      type="password"
                      value={credSecret}
                      autoComplete="current-password"
                      onChange={(e) => setCredSecret(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" && !e.shiftKey) {
                          e.preventDefault();
                          proposeCreds();
                        }
                      }}
                    />
                  </label>
                  <p className="cred-hint">{t("credHint")}</p>
                </div>
              ) : (
                <>
                  <div className={["send_email", "send_email_batch"].includes(approval?.tool_name || "") ? "approval-modal-content" : undefined}>
                    {approval?.tool_name !== "request_user_input" && (
                      <p>
                        {approval?.tool_name === "send_email_batch"
                          ? t("emailBatchApprovalReview")
                          : approval?.tool_name === "send_email"
                            ? t("emailApprovalReview")
                          : t("approvalModal")
                            .replace("{name}", approval?.tool_name || "")
                            .replace("{level}", String(approval?.level || ""))}
                      </p>
                    )}
                    {approval?.tool_name === "make_call" ? (
                      <div className="approval-call">
                        <div className="approval-call-line">
                          📞{" "}
                          {t("callApprovalModal")
                            .replace("{phone}", String(approval.arguments?.phone_number || "?"))
                            .replace("{purpose}", String(approval.arguments?.purpose || t("callNosay")))}
                        </div>
                        {Array.isArray(approval.arguments?.questions) &&
                          (approval.arguments.questions as string[]).length > 0 && (
                          <ul className="approval-call-qs">
                            {(approval.arguments.questions as string[]).map((q) => (
                              <li key={q}>"{q}"</li>
                            ))}
                          </ul>
                        )}
                      </div>
                    ) : approval?.tool_name === "send_email" ? (
                      <EmailApprovalCard
                        arguments={approvalDraft || approval?.arguments || {}}
                        t={t}
                        saving={approvalDraftSaving}
                        uploadingAttachment={approvalAttachmentUploading}
                        onChange={(next) => {
                          setApprovalDraft(next);
                        }}
                        onSave={() => void saveApprovalDraft()}
                        onAttach={(files) => void addApprovalAttachments(files)}
                        onRemoveAttachment={removeApprovalAttachment}
                      />
                    ) : approval?.tool_name === "send_email_batch" ? (
                      <EmailBatchApprovalCard
                        arguments={approvalDraft || approval?.arguments || {}}
                        t={t}
                        saving={approvalDraftSaving}
                        uploadingAttachment={approvalAttachmentUploading}
                        onChange={setApprovalDraft}
                        onSave={() => void saveApprovalDraft()}
                        onAttach={(files, emailIndex) =>
                          void addApprovalAttachments(files, emailIndex)
                        }
                        onRemoveAttachment={removeApprovalAttachment}
                      />
                    ) : approval?.tool_name === "request_user_input" ? (
                      <div className="user-input-form">
                        {(() => {
                          const prompt = approvalDraft || approval.arguments || {};
                          const inputType = String(prompt.input_type || "text");
                          const answer = String(prompt.answer ?? "");
                          const options = Array.isArray(prompt.options)
                            ? prompt.options.map(String)
                            : [];
                          const updateAnswer = (value: string) => {
                            setApprovalDraft({ ...prompt, answer: value });
                          };
                          return (
                            <>
                              <p>{String(prompt.question || "")}</p>
                              <label className="field">
                                <span>{String(prompt.title || t("userInputTitle"))}</span>
                                {inputType === "choice" ? (
                                  <select
                                    value={answer}
                                    required={prompt.required !== false}
                                    onChange={(event) => updateAnswer(event.target.value)}
                                  >
                                    <option value="" disabled>
                                      {t("chooseOption")}
                                    </option>
                                    {options.map((option) => (
                                      <option key={option} value={option}>{option}</option>
                                    ))}
                                  </select>
                                ) : inputType === "textarea" ? (
                                  <textarea
                                    value={answer}
                                    required={prompt.required !== false}
                                    onChange={(event) => updateAnswer(event.target.value)}
                                  />
                                ) : (
                                  <input
                                    type={inputType === "email" || inputType === "number" ? inputType : "text"}
                                    value={answer}
                                    required={prompt.required !== false}
                                    onChange={(event) => updateAnswer(event.target.value)}
                                  />
                                )}
                              </label>
                            </>
                          );
                        })()}
                      </div>
                    ) : (
                      <pre>{JSON.stringify(approval?.arguments || {}, null, 2)}</pre>
                    )}
                  </div>
                </>
              )}
              {!needsCredentials && (
                <p className="muted small">{t("taskApprovalScopeHint")}</p>
              )}
              <div className={`row end gap${["send_email", "send_email_batch"].includes(approval?.tool_name || "") ? " approval-modal-actions" : ""}`}>
                {approval?.run_id && onOpenRun && (
                  <button
                    className="btn small"
                    onClick={() => {
                      const runId = approval.run_id;
                      if (runId) onOpenRun(runId);
                    }}
                  >
                    {t("taskOpenDetails")}
                  </button>
                )}
                <button className="btn danger" disabled={approving} onClick={() => decide(false)}>
                  {approval?.tool_name === "request_user_input" ? t("userInputCancel") : t("deny")}
                </button>
                <button className="btn primary" disabled={approving} onClick={() => decide(true)}>
                  {approval?.tool_name === "request_user_input"
                    ? t("userInputContinue")
                    : needsCredentials
                      ? t("saveAndApprove")
                      : t("approve")}
                </button>
              </div>
            </div>
          </div>
        );
      })()}
    </>
  );
}

function markdownEsc(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function stripInternalModelMarkup(text: string): string {
  return text.replace(
    /<(thinking|analysis|reasoning|syntax_call)\b[^>]*>[\s\S]*?(?:<\/\1\s*>|$)/gi,
    "",
  );
}

/* Lightweight, dependency-free Markdown for chat replies. It understands just
 * enough: bold, headings, lists, fenced/inline code, `$…$` / `$$…$$` math (left
 * intact and wrapped so MathJax v3 can typeset it), and numbered research
 * citations `[N](url)` / `[N]` rendered as superscript links. It never throws
 * on arbitrary plain text. */
function Markdown({
  text,
  copyLabel,
  onCopyMath,
}: {
  text: string;
  copyLabel: string;
  onCopyMath: (source: string) => void;
}) {
  if (!text) return <span />;

  // 1. Protect fenced code, inline code, and math with opaque placeholders so
  //    escaping / linkification can't touch their contents.
  const holes: string[] = [];
  const hole = (s: string) => {
    const i = holes.length;
    holes.push(s);
    return `\u0000${i}\u0000`;
  };

  let t = text.replace(/(`{3,}|~{3,})\n[\s\S]*?\n\1/g, (m) => hole(m)); // fenced code
  t = t.replace(/`([^`\n]+)`/g, (m) => hole(m)); // inline code
  t = t.replace(/\$\$(.*?)\$\$/g, (m) => hole(m)); // display math $$…$$
  t = t.replace(/\$(.+?)\$/g, (m) => hole(m)); // inline math $…$

  // 2. Escape raw HTML, then apply lightweight markdown.
  let html = markdownEsc(t);
  html = html.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");

  // 3. Numbered research citations become superscript links. Process the
  //    `[N](url)` form first; bare `[N]` only when not already inside markup we
  //    just generated (lookbehind avoids re-matching generated `[N]` text).
  html = html.replace(
    /\[(\d+)\]\(([^)\s]+)\)/g,
    '<sup class="citation"><a href="$2" target="_blank" rel="noopener">[$1]</a></sup>',
  );
  html = html.replace(/(?<!["'>])\[(\d+)\]/g, '<sup class="citation">[$1]</sup>');

  // 4. Ordinary markdown links and bare URLs.
  html = html.replace(
    /\[([^\]]+)\]\((https?:\/\/[^)]+)\)/g,
    '<a href="$2" target="_blank" rel="noopener">$1</a>',
  );
  html = html.replace(
    /(^|[\s(])(https?:\/\/[^\s<)]+)/g,
    '$1<a href="$2" target="_blank" rel="noopener">$2</a>',
  );

  // 5. Split into paragraphs / lists / headings.
  const blocks = html.split(/\n{2,}/).map((block) => {
    const lines = block.split("\n");
    const table = renderMarkdownTable(lines);
    if (table) return table;
    if (lines.every((l) => /^[-*] /.test(l.trim()))) {
      return `<ul>${lines.map((l) => `<li>${l.trim().slice(2)}</li>`).join("")}</ul>`;
    }
    if (/^#{1,3} /.test(block)) return `<p><strong>${block.replace(/^#{1,3} /, "")}</strong></p>`;
    const filled = block.replace(/^[ \t]*>[ \t]?/gm, "").replace(/\n/g, "<br>");
    // `> **Answer:** …` (produced by the math engine) renders as a highlighted
    // `.math-answer` callout (spec #5: clearly highlighted final answers);
    // any other blockquote is wrapped neutrally.
    const trimmed = block.trim();
    if (trimmed.toLowerCase().includes("> **answer:") || trimmed.startsWith("> **answer")) {
      return `<div class="math-answer">${filled}</div>`;
    }
    if (trimmed.startsWith(">")) {
      return `<div class="blockquote">${filled}</div>`;
    }
    return `<p>${filled}</p>`;
  });
  html = blocks.join("");

  // 6. Re-inject protected holes (code stays escaped; math stays raw for MathJax).
  html = html.replace(/\u0000(\d+)\u0000/g, (_, i) => {
    const raw = holes[Number(i)];
    if (/^(```+|~~~+)/.test(raw)) return `<pre>${markdownEsc(raw).replace(/^```+|~~~+|`/g, "")}</pre>`;
    if (/^\$\$.*\$\$$/.test(raw)) {
      const source = raw.slice(2, -2).trim();
      return `<div class="math math-display">${raw}<button type="button" class="math-copy" data-math-source="${htmlAttributeEsc(source)}" aria-label="${htmlAttributeEsc(copyLabel)}">${markdownEsc(copyLabel)}</button></div>`;
    }
    if (/^\$.*\$/.test(raw)) {
      const source = raw.slice(1, -1).trim();
      return `<span class="math math-inline">${raw}<button type="button" class="math-copy" data-math-source="${htmlAttributeEsc(source)}" aria-label="${htmlAttributeEsc(copyLabel)}">${markdownEsc(copyLabel)}</button></span>`;
    }
    return `<code>${markdownEsc(raw).replace(/`/g, "")}</code>`;
  });

  return (
    <div
      onClick={(event) => {
        const target = event.target;
        if (!(target instanceof Element)) return;
        const button = target.closest<HTMLButtonElement>(".math-copy");
        if (button) onCopyMath(button.dataset.mathSource || "");
      }}
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}

function renderMarkdownTable(lines: string[]): string | null {
  if (lines.length < 2) return null;
  const cells = (line: string) =>
    line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((cell) => cell.trim());
  const headers = cells(lines[0]);
  const separator = cells(lines[1]);
  if (
    headers.length === 0
    || separator.length !== headers.length
    || !separator.every((cell) => /^:?-{3,}:?$/.test(cell))
  ) return null;
  const rows = lines.slice(2)
    .filter((line) => line.includes("|"))
    .map(cells)
    .filter((row) => row.length === headers.length);
  const head = headers.map((cell) => `<th scope="col">${cell}</th>`).join("");
  const body = rows.map((row) =>
    `<tr>${row.map((cell) => `<td>${cell}</td>`).join("")}</tr>`
  ).join("");
  return `<div class="table-wrap"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
}

function htmlAttributeEsc(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/"/g, "&quot;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

/* Renders an extracted vector-diagram block returned by the backend
 * (app/services/visuals.apply_visual_postprocessing). SVGs are injected
 * straight into the DOM; canvas payloads are shown as annotated source so the
 * raw JSON is inspectable; remote images become <img>. SVG/canvas payloads can
 * be saved client-side (their content already lives in the message). */
function DiagramRenderer({ diagram, downloadLabel = "Download" }:
  { diagram: DiagramRef; downloadLabel?: string }) {
  const canDownload = diagram.kind === "svg" || diagram.kind === "canvas";
  return (
    <div className={`diagram diagram-${diagram.kind}`} title={diagram.title}>
      {diagram.kind === "svg" && (
        <div dangerouslySetInnerHTML={{ __html: diagram.content }} />
      )}
      {diagram.kind === "image" && (
        <img src={diagram.content} alt={diagram.title || "diagram"} />
      )}
      {diagram.kind === "canvas" && (
        <pre className="diagram-canvas">{diagram.content}</pre>
      )}
      {canDownload && (
        <button
          type="button"
          className="diagram-dl"
          onClick={() => downloadDiagram(diagram)}
        >
          ⤓ {downloadLabel}
        </button>
      )}
    </div>
  );
}

/* Loads an artifact through the authenticated artifacts endpoint as a blob
 * object URL, because the route requires a Bearer token (a plain <img src=…>
 * would get 401). The object URL is revoked when the URL changes or unmounts. */
function ArtifactImage({ url, alt }: { url: string; alt: string }) {
  const [src, setSrc] = useState("");
  useEffect(() => {
    let objectUrl = "";
    let cancelled = false;
    void (async () => {
      try {
        const resp = await fetch(url, {
          headers: { Authorization: `Bearer ${getToken()}` },
        });
        if (!resp.ok) return;
        const blob = await resp.blob();
        if (cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        setSrc(objectUrl);
      } catch {
        /* leave the placeholder in place on failure */
      }
    })();
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [url]);
  if (!src) return <span className="artifact-img-loading" aria-hidden="true">…</span>;
  return <img className="artifact-img" src={src} alt={alt} />;
}

/* Authenticated artifact download: fetch the bytes with the Bearer token (the
 * artifacts endpoint is owner-only) and trigger a client-side save using the
 * server-provided file name. */
async function downloadArtifact(a: Artifact, fallbackName = "artifact") {
  try {
    const resp = await fetch(a.download_url || a.url, {
      headers: { Authorization: `Bearer ${getToken()}` },
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const blob = await resp.blob();
    triggerDownload(blob, a.file_name || a.title || fallbackName);
  } catch (err) {
    console.error("artifact download failed", err);
  }
}

/* Save an inline vector diagram (SVG/canvas) client-side; its payload already
 * lives in the message, so no server round-trip is required. */
function downloadDiagram(diagram: DiagramRef) {
  const isCanvas = diagram.kind === "canvas";
  const blob = new Blob([diagram.content],
    { type: isCanvas ? "text/plain" : "image/svg+xml" });
  const base = (diagram.title || "diagram").replace(/[^\w.-]+/g, "_").slice(0, 80);
  triggerDownload(blob, `${base || "diagram"}.${isCanvas ? "txt" : "svg"}`);
}

function triggerDownload(blob: Blob, fileName: string) {
  const href = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = href;
  link.download = fileName;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(href);
}
