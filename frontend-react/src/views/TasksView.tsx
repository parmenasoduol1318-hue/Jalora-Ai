import { useCallback, useEffect, useRef, useState } from "react";
import { api, transcribeAudio, uploadEmailAttachment } from "../lib/api";
import { errorMessage, friendlyError } from "../lib/errors";
import type { Call, ChatResponse, OutreachQueueEntry, Task } from "../lib/types";
import { TaskExecutionDetails } from "../components/TaskExecutionDetails";
import {
  createRecognizer,
  readRecognition,
  recordUntilStopped,
  type ClipRecorder,
  type SpeechRecognitionLike,
} from "../lib/voice";
import { LiveCallView } from "./LiveCallView";

const CHIP: Record<string, string> = {
  queued: "warn", running: "ok", waiting_for_approval: "warn",
  paused: "warn", completed: "ok", partial: "warn", failed: "fail",
  stopped: "warn", cancelled: "",
};
const LABEL: Record<string, string> = {
  queued: "stQueued", running: "stRunning", waiting_for_approval: "stWaiting",
  paused: "stPaused", completed: "stCompleted", partial: "stPartial", failed: "stFailed",
  stopped: "stStopped", cancelled: "stCancelled",
};
const CALL_CHIP: Record<string, string> = {
  dialing: "warn", in_progress: "ok", completed: "ok",
  failed: "fail", cancelled: "warn", no_answer: "warn",
};
const CALL_LABEL: Record<string, string> = {
  dialing: "callStatusDialing", in_progress: "callStatusInProgress",
  completed: "callStatusCompleted", failed: "callStatusFailed",
  cancelled: "callStatusCancelled", no_answer: "callStatusNoAnswer",
};
const STEP_ICON: Record<string, string> = {
  pending: "○", active: "◐", done: "✓", failed: "✗", skipped: "⊘",
};

export function TasksView({
  t, onToast, onStartTask, focusTaskId, focusTaskKey, onOpenConversation,
}: {
  t: (k: string) => string;
  onToast: (m: string, e?: boolean) => void;
  onStartTask: (prompt: string) => void;
  focusTaskId?: string | null;
  focusTaskKey: number;
  onOpenConversation: (conversationId: string) => void;
}) {
  const [tasks, setTasks] = useState<Task[] | null>(null);
  const [calls, setCalls] = useState<Call[]>([]);
  const [outreachQueueTask, setOutreachQueueTask] = useState<string | null>(null);
  const [outreachQueues, setOutreachQueues] = useState<Record<string, OutreachQueueEntry[]>>({});
  const [outreachDrafts, setOutreachDrafts] = useState<
    Record<string, NonNullable<OutreachQueueEntry["draft"]>>
  >({});
  const [savingOutreachDraft, setSavingOutreachDraft] = useState<string | null>(null);
  const [uploadingOutreachAttachment, setUploadingOutreachAttachment] = useState<string | null>(null);
  const [decidingOutreachApproval, setDecidingOutreachApproval] = useState<string | null>(null);
  const [reconcilingOutreachApproval, setReconcilingOutreachApproval] = useState<string | null>(null);
  const [taskPrompt, setTaskPrompt] = useState("");
  const [detailsTaskId, setDetailsTaskId] = useState<string | null>(null);
  const [listening, setListening] = useState(false);
  const [transcribing, setTranscribing] = useState(false);
  const recognizerRef = useRef<SpeechRecognitionLike | null>(null);
  const recorderRef = useRef<ClipRecorder | null>(null);
  const lastFocusedTaskKey = useRef<number | null>(null);

  const load = useCallback(async () => {
    try {
      setTasks(await api<Task[]>("/api/tasks"));
    } catch (err) {
      onToast(errorMessage(err, t), true);
    }
    try {
      setCalls(await api<Call[]>("/api/calls?limit=20"));
    } catch {
      /* calls panel is optional */
    }
  }, [onToast]);

  useEffect(() => {
    load();
    const timer = window.setInterval(load, 3000);
    return () => {
      window.clearInterval(timer);
      recognizerRef.current?.abort();
      recorderRef.current?.cancel();
    };
  }, [load]);

  useEffect(() => {
    if (!focusTaskId || !tasks) {
      if (!focusTaskId) lastFocusedTaskKey.current = null;
      return;
    }
    if (lastFocusedTaskKey.current !== focusTaskKey) {
      lastFocusedTaskKey.current = focusTaskKey;
      setDetailsTaskId(focusTaskId);
    }
    document.getElementById(`task-${focusTaskId}`)?.scrollIntoView({
      behavior: "smooth",
      block: "center",
    });
  }, [focusTaskId, focusTaskKey, tasks]);

  const stopVoiceInput = () => {
    const recognizer = recognizerRef.current;
    if (recognizer) {
      recognizer.stop();
      return;
    }
    const recorder = recorderRef.current;
    recorderRef.current = null;
    setListening(false);
    if (!recorder) return;
    void recorder.stop().then(async (blob) => {
      if (!blob) return;
      setTranscribing(true);
      try {
        const result = await transcribeAudio(blob, "task-instruction.webm");
        setTaskPrompt((current) =>
          current ? `${current.trim()} ${result.transcript}` : result.transcript,
        );
      } catch (err) {
        onToast((err as Error).message, true);
      } finally {
        setTranscribing(false);
      }
    });
  };

  const toggleVoiceInput = async () => {
    if (listening) {
      stopVoiceInput();
      return;
    }
    const recognizer = createRecognizer();
    if (recognizer) {
      recognizer.lang = "en-KE";
      recognizer.onstart = () => setListening(true);
      recognizer.onerror = (event) => {
        setListening(false);
        onToast(`Voice input failed: ${event.error}.`, true);
      };
      recognizer.onend = () => {
        setListening(false);
        recognizerRef.current = null;
      };
      recognizer.onresult = (event) => {
        const { finalText } = readRecognition(event);
        if (finalText.trim()) {
          setTaskPrompt((current) =>
            current ? `${current.trim()} ${finalText.trim()}` : finalText.trim(),
          );
        }
      };
      recognizerRef.current = recognizer;
      try {
        recognizer.start();
      } catch (err) {
        recognizerRef.current = null;
        setListening(false);
        onToast(`Could not start voice input: ${(err as Error).message}`, true);
      }
      return;
    }
    try {
      const recorder = await recordUntilStopped();
      if (!recorder) {
        onToast("Voice input is unavailable in this browser.", true);
        return;
      }
      recorderRef.current = recorder;
      setListening(true);
    } catch (err) {
      onToast(`Could not start voice input: ${(err as Error).message}`, true);
    }
  };

  const submitTask = (event: React.FormEvent) => {
    event.preventDefault();
    const prompt = taskPrompt.trim();
    if (!prompt || listening || transcribing) return;
    onStartTask(prompt);
    setTaskPrompt("");
  };

  const cancel = async (task: Task) => {
    try {
      await api(`/api/tasks/${task.id}/cancel`, { method: "POST" });
      onToast(t("stCancelled"));
      load();
    } catch (err) {
      onToast(errorMessage(err, t), true);
    }
  };

  const pause = async (task: Task) => {
    if (!task.run_id) return;
    try {
      await api(`/api/runs/${task.run_id}/pause`, { method: "POST" });
      onToast(t("pauseRequested"));
      load();
    } catch (err) {
      onToast(errorMessage(err, t), true);
    }
  };

  const resumeTask = async (task: Task) => {
    if (!task.run_id) return;
    try {
      const data = await api<ChatResponse>(`/api/runs/${task.run_id}/resume`, { method: "POST" });
      onToast(
        data.status === "completed" ? t("stCompleted")
          : data.status === "partial" ? t("stPartial") : t("stWaiting"),
      );
      load();
    } catch (err) {
      onToast(errorMessage(err, t), true);
    }
  };

  const refreshOutreachQueue = async (task: Task) => {
    const detail = await api<Task>(`/api/tasks/${task.id}`);
    const queue = detail.outreach_queue || [];
    setOutreachQueues((current) => ({ ...current, [task.id]: queue }));
    setOutreachDrafts((current) => {
      const next: typeof current = {};
      for (const item of queue) {
        if (!item.approval_id || !item.draft) continue;
        next[item.approval_id] = current[item.approval_id] || item.draft;
      }
      return next;
    });
  };

  const toggleOutreachQueue = async (task: Task) => {
    if (outreachQueueTask === task.id) {
      setOutreachQueueTask(null);
      return;
    }
    setOutreachQueueTask(task.id);
    if (outreachQueues[task.id]) return;
    try {
      await refreshOutreachQueue(task);
    } catch (err) {
      setOutreachQueueTask(null);
      onToast(errorMessage(err, t), true);
    }
  };

  const updateOutreachDraft = (
    approvalId: string,
    field: string,
    value: unknown,
  ) => {
    setOutreachDrafts((current) => ({
      ...current,
      [approvalId]: { ...current[approvalId], [field]: value },
    }));
  };

  const saveOutreachDraft = async (task: Task, item: OutreachQueueEntry) => {
    if (!item.approval_id) return;
    const draft = outreachDrafts[item.approval_id] || item.draft;
    if (!draft) return;
    setSavingOutreachDraft(item.approval_id);
    try {
      const updated = await api<{ arguments: NonNullable<OutreachQueueEntry["draft"]> }>(
        `/api/approvals/${item.approval_id}`,
        { method: "PATCH", body: { arguments: draft } },
      );
      setOutreachDrafts((current) => ({
        ...current,
        [item.approval_id as string]: updated.arguments,
      }));
      await refreshOutreachQueue(task);
      onToast(t("changesSaved"));
    } catch (err) {
      onToast(errorMessage(err, t), true);
    } finally {
      setSavingOutreachDraft(null);
    }
  };

  const addOutreachAttachments = async (
    approvalId: string,
    files: File[],
  ) => {
    setUploadingOutreachAttachment(approvalId);
    try {
      let attachments = [...(outreachDrafts[approvalId]?.attachments || [])];
      for (const file of files) {
        const attachment = await uploadEmailAttachment(file);
        attachments = [
          ...attachments,
          {
            url: attachment.url,
            filename: attachment.filename,
            mime_type: attachment.mime_type,
            size_bytes: attachment.size_bytes,
          },
        ];
        updateOutreachDraft(approvalId, "attachments", attachments);
      }
      onToast(t("attachmentAdded"));
    } catch (err) {
      onToast(errorMessage(err, t), true);
    } finally {
      setUploadingOutreachAttachment(null);
    }
  };

  const removeOutreachAttachment = (
    approvalId: string,
    index: number,
  ) => {
    const draft = outreachDrafts[approvalId];
    if (!draft || !Array.isArray(draft.attachments)) return;
    updateOutreachDraft(
      approvalId,
      "attachments",
      draft.attachments.filter((_, attachmentIndex) => attachmentIndex !== index),
    );
  };

  const decideOutreachDraft = async (
    task: Task,
    item: OutreachQueueEntry,
    decision: "approve" | "reject" | "skip",
  ) => {
    if (!item.approval_id) return;
    if (uploadingOutreachAttachment === item.approval_id) {
      onToast(t("uploadingAttachment"), true);
      return;
    }
    setDecidingOutreachApproval(item.approval_id);
    try {
      const approve = decision === "approve";
      if (approve) {
        const draft = outreachDrafts[item.approval_id] || item.draft;
        if (!draft) return;
        await api(`/api/approvals/${item.approval_id}`, {
          method: "PATCH",
          body: { arguments: draft },
        });
      }
      const result = await api<ChatResponse>(
        `/api/approvals/${item.approval_id}/${
          decision === "approve" ? "approve" : decision === "skip" ? "skip" : "reject"
        }`,
        { method: "POST" },
      );
      onToast(
        result.status === "completed"
          ? t("stCompleted")
          : result.status === "paused_approval"
            ? t("stWaiting")
            : result.reply || result.status,
      );
      await refreshOutreachQueue(task);
      load();
    } catch (err) {
      onToast(errorMessage(err, t), true);
      void refreshOutreachQueue(task).catch((refreshError) =>
        onToast(errorMessage(refreshError, t), true),
      );
    } finally {
      setDecidingOutreachApproval(null);
    }
  };

  const reconcileOutreachSend = async (
    task: Task,
    item: OutreachQueueEntry,
  ) => {
    if (!item.approval_id) return;
    setReconcilingOutreachApproval(item.approval_id);
    try {
      const result = await api<{
        status: string;
        matches?: number;
      }>(`/api/emails/send/${item.approval_id}/reconcile`, { method: "POST" });
      onToast(
        result.status === "provider_confirmed"
          ? t("outreachReconciled")
          : result.matches
            ? t("outreachReconcileAmbiguous")
            : t("outreachStillUnknown"),
        result.status !== "provider_confirmed",
      );
      await refreshOutreachQueue(task);
      load();
    } catch (err) {
      onToast(errorMessage(err, t), true);
    } finally {
      setReconcilingOutreachApproval(null);
    }
  };

  const hangupCall = async (call: Call) => {
    try {
      await api(`/api/calls/${call.id}/hangup`, { method: "POST" });
      setCalls((prev) =>
        prev.map((c) => (c.id === call.id ? { ...c, status: "cancelled" } : c)),
      );
      load();
    } catch (err) {
      onToast(errorMessage(err, t), true);
    }
  };

  const callsFor = (task: Task) => calls.filter((c) => c.task_id === task.id);

  return (
    <>
      <div className="view-header">
        <h2>{t("tasks")}</h2>
        <button className="btn small" onClick={load}>{t("refresh")}</button>
      </div>
      <form className="task-launcher" onSubmit={submitTask}>
        <div>
          <h3>Give Jalora a task</h3>
          <p className="muted small">
            Type an instruction or dictate it. Jalora will work in Agent Tasks and
            pause for clarification or your approval when needed.
          </p>
        </div>
        <label className="task-launcher-label" htmlFor="task-instruction">
          What would you like Jalora to do?
        </label>
        <textarea
          id="task-instruction"
          value={taskPrompt}
          maxLength={4000}
          rows={4}
          placeholder={'For example: Message Parmenas on WhatsApp to come tomorrow for his gift, then message George and ask him to bring the gift I asked him about.'}
          onChange={(event) => setTaskPrompt(event.target.value)}
        />
        <div className="row gap task-launcher-actions">
          <button
            className={`btn small${listening ? " danger" : ""}`}
            type="button"
            disabled={transcribing}
            onClick={() => void toggleVoiceInput()}
          >
            {transcribing ? "Transcribing…" : listening ? "Stop voice input" : "🎤 Dictate task"}
          </button>
          <span className="muted small">
            {listening ? "Listening; review the transcript before submitting." : "The microphone is active only while voice input is on. Review the transcript before submitting."}
          </span>
          <button
            className="btn primary task-launcher-submit"
            type="submit"
            disabled={!taskPrompt.trim() || listening || transcribing}
          >
            Start task
          </button>
        </div>
        <p className="task-launcher-safety">
          External messages are not sent automatically. Review the recipient and exact
          message in Jalora’s approval prompt before approving each send.
        </p>
      </form>
      <LiveCallView t={t} onToast={onToast} />
      <div className="task-list">
        {!tasks && <div className="activity-empty">…</div>}
        {tasks && tasks.length === 0 && (
          <div className="activity-empty">{t("tasksEmpty")}</div>
        )}
        {tasks?.map((task) => {
          const done = task.status === "completed";
          const pct = done ? 100 : Math.max(0, Math.min(100, task.progress || 0));
          const running = ["queued", "running", "waiting_for_approval"].includes(task.status);
          const cancellable = [
            "queued", "running", "waiting_for_approval", "paused",
          ].includes(task.status);
          const pausable = task.status === "running";
          return (
            <div
              key={task.id}
              id={`task-${task.id}`}
              className="task-card"
              style={focusTaskId === task.id ? { outline: "2px solid var(--cyan)" } : undefined}
            >
              <div className="row between">
                <div className="task-goal">{task.goal || task.id}</div>
                <span className={`badge ${CHIP[task.status] ?? ""}`}>
                  {t(LABEL[task.status] || task.status)}
                </span>
              </div>
              <div className="task-bar">
                <div
                  className={`task-bar-fill${done ? " done" : ""}${running ? " running" : ""}`}
                  style={{ width: `${running && pct === 0 ? 30 : pct}%` }}
                />
              </div>
              <div className="meta">
                {running && <span className="task-live-dot" />}
                {pct}% · {new Date(task.created_at).toLocaleString()}
              </div>
              {task.plan && task.plan.length > 0 && (
                <div className="task-plan">
                  {task.plan.map((step, i) => (
                    <div key={i} className={`plan-step ${step.status}`}>
                      <span className="plan-icon">{STEP_ICON[step.status] || "○"}</span>
                      <span className="plan-desc">{step.description}</span>
                    </div>
                  ))}
                </div>
              )}
              <button
                className="btn small task-details-toggle"
                type="button"
                aria-expanded={detailsTaskId === task.id}
                aria-controls={
                  detailsTaskId === task.id ? `task-detail-${task.id}` : undefined
                }
                onClick={() => setDetailsTaskId((current) =>
                  current === task.id ? null : task.id,
                )}
              >
                {t(detailsTaskId === task.id ? "taskHideDetails" : "taskShowDetails")}
              </button>
              {detailsTaskId === task.id && (
                <div id={`task-detail-${task.id}`}>
                  <TaskExecutionDetails
                    task={task}
                    t={t}
                    onToast={onToast}
                    onChanged={load}
                    onOpenConversation={onOpenConversation}
                  />
                </div>
              )}
              {task.plan?.some((step) => step.tool === "search_public_contacts")
                && task.plan.some((step) => step.tool === "send_email") && (
                <>
                  <button
                    className="btn small"
                    type="button"
                    aria-expanded={outreachQueueTask === task.id}
                    onClick={() => void toggleOutreachQueue(task)}
                  >
                    {t(outreachQueueTask === task.id ? "hideOutreachQueue" : "outreachQueue")}
                  </button>
                  {outreachQueueTask === task.id && (
                    <div className="task-plan outreach-queue">
                      {(outreachQueues[task.id] || []).map((item) => {
                        const statusLabel = item.status === "awaiting_approval"
                          ? t("stWaiting")
                          : item.status === "sent"
                            ? t("stCompleted")
                            : item.status === "skipped"
                              ? t("outreachSkipped")
                            : item.status === "rejected"
                              ? t("rejectedMsg")
                              : item.status === "outcome_unknown"
                                ? t("outreachOutcomeUnknown")
                                : item.status === "done"
                                  ? t("stCompleted")
                                : t(LABEL[item.status] || item.status);
                        return (
                          <div key={item.plan_step_id} className="plan-step">
                            <span className="plan-icon">{item.position}.</span>
                            <div className="plan-desc">
                              <strong>{item.organization || item.recipient}</strong>
                              <div>{item.recipient} · {statusLabel}</div>
                              {item.status === "outcome_unknown" && item.approval_id && (
                                <button
                                  className="btn small"
                                  type="button"
                                  disabled={reconcilingOutreachApproval === item.approval_id}
                                  onClick={() => void reconcileOutreachSend(task, item)}
                                >
                                  {t(
                                    reconcilingOutreachApproval === item.approval_id
                                      ? "outreachReconciling"
                                      : "outreachReconcile",
                                  )}
                                </button>
                              )}
                              {item.provider_message_id && (
                                <div>
                                  {t("outreachProviderReceipt")}: {item.provider || "email"} · {item.provider_message_id}
                                </div>
                              )}
                              {item.source_url ? (
                                <div>
                                  {t("outreachSource")}:{" "}
                                  <a href={item.source_url} target="_blank" rel="noreferrer">
                                    {item.source_title || item.source_url}
                                  </a>
                                </div>
                              ) : (
                                <div>{t("outreachAddressMissing")}</div>
                              )}
                              <small className="muted">
                                {item.address_found_on_source
                                  ? t("outreachAddressFound")
                                  : t("outreachAddressMissing")}
                                {" · "}
                                {item.organization_match_on_source
                                  ? t("outreachOrganizationMatched")
                                  : t("outreachOrganizationUnmatched")}
                                {" · "}
                                {t("outreachSourceCaveat")}
                              </small>
                              {item.approval_id && item.draft && (
                                <div className="approval-email-card">
                                  <div className="approval-email-field">
                                    <label>{t("toLabel")}</label>
                                    <input
                                      className="approval-email-input"
                                      value={String(
                                        (outreachDrafts[item.approval_id] || item.draft).to || "",
                                      )}
                                      onChange={(event) =>
                                        updateOutreachDraft(
                                          item.approval_id as string,
                                          "to",
                                          event.target.value,
                                        )
                                      }
                                    />
                                  </div>
                                  <div className="approval-email-field">
                                    <label>{t("subjectLabel")}</label>
                                    <input
                                      className="approval-email-input"
                                      value={String(
                                        (outreachDrafts[item.approval_id] || item.draft).subject || "",
                                      )}
                                      onChange={(event) =>
                                        updateOutreachDraft(
                                          item.approval_id as string,
                                          "subject",
                                          event.target.value,
                                        )
                                      }
                                    />
                                  </div>
                                  <div className="approval-email-field">
                                    <label>{t("messageLabel")}</label>
                                    <textarea
                                      className="approval-email-input approval-email-textarea"
                                      rows={6}
                                      value={String(
                                        (outreachDrafts[item.approval_id] || item.draft).body || "",
                                      )}
                                      onChange={(event) =>
                                        updateOutreachDraft(
                                          item.approval_id as string,
                                          "body",
                                          event.target.value,
                                        )
                                      }
                                    />
                                  </div>
                                  <div className="approval-email-attachments">
                                    <strong>{t("attachments")}</strong>
                                    {(outreachDrafts[item.approval_id] || item.draft)
                                      .attachments?.map((attachment, attachmentIndex) => (
                                        <div
                                          className="row between"
                                          key={`${String(attachment.filename || "attachment")}-${attachmentIndex}`}
                                        >
                                          <span>
                                            {String(attachment.filename || attachment.name || "attachment")}
                                          </span>
                                          <button
                                            className="btn small"
                                            type="button"
                                            onClick={() =>
                                              removeOutreachAttachment(
                                                item.approval_id as string,
                                                attachmentIndex,
                                              )
                                            }
                                          >
                                            {t("remove")}
                                          </button>
                                        </div>
                                      ))}
                                    <label className="btn small">
                                      {uploadingOutreachAttachment === item.approval_id
                                        ? t("uploadingAttachment")
                                        : t("addAttachment")}
                                      <input
                                        type="file"
                                        multiple
                                        hidden
                                        disabled={uploadingOutreachAttachment === item.approval_id}
                                        onChange={(event) => {
                                          const files = Array.from(event.target.files || []);
                                          if (files.length) {
                                            void addOutreachAttachments(
                                              item.approval_id as string,
                                              files,
                                            );
                                          }
                                          event.target.value = "";
                                        }}
                                      />
                                    </label>
                                  </div>
                                  <div className="row gap">
                                    <button
                                      className="btn small"
                                      type="button"
                                      disabled={
                                        savingOutreachDraft === item.approval_id
                                        || decidingOutreachApproval === item.approval_id
                                        || uploadingOutreachAttachment === item.approval_id
                                      }
                                      onClick={() => void saveOutreachDraft(task, item)}
                                    >
                                      {savingOutreachDraft === item.approval_id
                                        ? t("saving")
                                        : t("outreachSaveWithoutSending")}
                                    </button>
                                    <button
                                      className="btn small danger"
                                      type="button"
                                      disabled={
                                        decidingOutreachApproval === item.approval_id
                                        || uploadingOutreachAttachment === item.approval_id
                                      }
                                      onClick={() =>
                                        void decideOutreachDraft(task, item, "reject")
                                      }
                                    >
                                      {t("outreachRejectDraft")}
                                    </button>
                                    <button
                                      className="btn small"
                                      type="button"
                                      disabled={
                                        decidingOutreachApproval === item.approval_id
                                        || uploadingOutreachAttachment === item.approval_id
                                      }
                                      onClick={() =>
                                        void decideOutreachDraft(task, item, "skip")
                                      }
                                    >
                                      {t("outreachSkipDraft")}
                                    </button>
                                    <button
                                      className="btn small primary"
                                      type="button"
                                      disabled={
                                        decidingOutreachApproval === item.approval_id
                                        || uploadingOutreachAttachment === item.approval_id
                                      }
                                      onClick={() =>
                                        void decideOutreachDraft(task, item, "approve")
                                      }
                                    >
                                      {t("outreachApproveAndSend")}
                                    </button>
                                  </div>
                                </div>
                              )}
                            </div>
                          </div>
                        );
                      })}
                      {outreachQueues[task.id]?.length === 0 && (
                        <div className="muted small">{t("outreachAddressMissing")}</div>
                      )}
                    </div>
                  )}
                </>
              )}
              {callsFor(task).map((call) => {
                const live = ["dialing", "in_progress"].includes(call.status);
                const visible = call.transcript.filter(
                  (tr) => tr.kind !== "system",
                );
                return (
                  <div key={call.id} className={`call-panel ${call.status}`}>
                    <div className="call-head">
                      <span className="call-phone">📞 {call.to_phone}</span>
                      {call.provider === "mock" && (
                        <span className="call-mock">demo</span>
                      )}
                      <span className={`badge ${CALL_CHIP[call.status] ?? ""}`}>
                        {t(CALL_LABEL[call.status] || call.status)}
                      </span>
                      {live && (
                        <button
                          className="btn small danger"
                          onClick={() => void hangupCall(call)}
                        >
                          {t("hangup")}
                        </button>
                      )}
                    </div>
                    {call.purpose && (
                      <div className="call-purpose">{call.purpose}</div>
                    )}
                    {call.summary && (
                      <div className="call-summary">{call.summary}</div>
                    )}
                    {visible.length > 0 && (
                      <div className={`call-transcript ${live ? "live" : ""}`}>
                        {visible.map((tr, ti) => (
                          <div key={ti} className={`turn ${tr.role}`}>
                            <span className="turn-who">{tr.role === "agent" ? "◆" : "🧑"}</span>
                            <span className="turn-text">{tr.text}</span>
                          </div>
                        ))}
                        {live && <span className="turn-live" />}
                      </div>
                    )}
                  </div>
                );
              })}
              {(task.status === "failed" || (task.status === "partial" && task.error)) && (
                <div className="error-bubble" style={{ marginTop: 8 }}>
                  ⚠ {task.error
                    ? friendlyError(undefined, task.error, t)
                    : t("taskFailed")}
                </div>
              )}
              <div className="row end gap" style={{ marginTop: 8 }}>
                {task.resumable && task.run_id && (
                  <button
                    className="btn small primary"
                    onClick={() => void resumeTask(task)}
                  >
                    {t("resume")}
                  </button>
                )}
                {pausable && task.run_id && (
                  <button
                    className="btn small"
                    style={{ marginRight: "auto" }}
                    onClick={() => void pause(task)}
                  >
                    {t("pause")}
                  </button>
                )}
                <button
                  className="btn small danger"
                  style={cancellable ? undefined : { display: "none" }}
                  onClick={() => cancel(task)}
                >
                  {t("cancel")}
                </button>
              </div>
            </div>
          );
        })}
      </div>
    </>
  );
}
