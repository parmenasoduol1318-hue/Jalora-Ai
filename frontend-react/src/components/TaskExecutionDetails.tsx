import { useCallback, useEffect, useState } from "react";
import { api } from "../lib/api";
import { errorMessage } from "../lib/errors";
import type { ChatResponse, Task, TaskApproval, TaskStep } from "../lib/types";

type Props = {
  task: Task;
  t: (key: string) => string;
  onToast: (message: string, error?: boolean) => void;
  onChanged: () => Promise<void> | void;
  onOpenConversation: (conversationId: string) => void;
};

function outcomeLabel(value: string | null | undefined, t: Props["t"]) {
  if (!value) return t("taskOutcomeNotRecorded");
  const key = `taskOutcome_${value}`;
  const translated = t(key);
  return translated === key ? value.replace(/_/g, " ") : translated;
}

function evidenceUrl(item: Record<string, unknown>) {
  const value = typeof item.url === "string" ? item.url : "";
  if (!value) return null;
  try {
    const url = new URL(value);
    return url.protocol === "http:" || url.protocol === "https:" ? url.href : null;
  } catch {
    return null;
  }
}

export function TaskExecutionDetails({
  task, t, onToast, onChanged, onOpenConversation,
}: Props) {
  const [detail, setDetail] = useState<Task | null>(null);
  const [loadError, setLoadError] = useState("");
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState<string | null>(null);
  const [deciding, setDeciding] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const current = await api<Task>(`/api/tasks/${task.id}`);
      setDetail(current);
      setLoadError("");
      setDrafts((existing) => {
        const next = { ...existing };
        for (const approval of current.approvals || []) {
          if (!(approval.id in next)) {
            next[approval.id] = JSON.stringify(approval.arguments, null, 2);
          }
        }
        return next;
      });
    } catch (err) {
      setLoadError(errorMessage(err, t));
    }
  }, [task.id, t]);

  useEffect(() => {
    void load();
  }, [load]);

  const saveDraft = async (approval: TaskApproval) => {
    let argumentsValue: unknown;
    try {
      argumentsValue = JSON.parse(drafts[approval.id] || "{}");
    } catch {
      onToast(t("taskApprovalInvalidJson"), true);
      return;
    }
    if (
      !argumentsValue
      || typeof argumentsValue !== "object"
      || Array.isArray(argumentsValue)
    ) {
      onToast(t("taskApprovalInvalidJson"), true);
      return;
    }
    setSaving(approval.id);
    try {
      await api(`/api/approvals/${approval.id}`, {
        method: "PATCH",
        body: {
          arguments: argumentsValue,
          payload_version: approval.payload_version,
        },
      });
      onToast(t("taskApprovalSaved"));
      await load();
    } catch (err) {
      onToast(errorMessage(err, t), true);
    } finally {
      setSaving(null);
    }
  };

  const decide = async (approval: TaskApproval, approve: boolean) => {
    setDeciding(approval.id);
    try {
      const result = await api<ChatResponse>(
        `/api/approvals/${approval.id}/${approve ? "approve" : "reject"}`,
        { method: "POST" },
      );
      onToast(
        result.status === "completed" ? t("stCompleted")
          : result.status === "partial" ? t("stPartial")
            : result.status === "paused_approval" ? t("stWaiting")
              : result.status,
      );
      await onChanged();
      await load();
    } catch (err) {
      onToast(errorMessage(err, t), true);
      await load();
    } finally {
      setDeciding(null);
    }
  };

  if (loadError) {
    return (
      <section className="task-detail" aria-live="polite">
        <p className="error-bubble">{loadError}</p>
        <button className="btn small" type="button" onClick={() => void load()}>
          {t("retry")}
        </button>
      </section>
    );
  }
  if (!detail) {
    return <section className="task-detail" role="status">{t("taskLoading")}</section>;
  }

  const approvals = detail.approvals || [];
  const pendingApprovals = approvals.filter((approval) => approval.status === "pending");
  const nextAction = pendingApprovals.some(
    (approval) => approval.claim_status === "unclaimed",
  )
    ? t("taskNextReviewApproval")
    : pendingApprovals.length > 0
      ? t("taskNextApprovalProcessing")
      : detail.status === "waiting_for_approval"
        ? t("taskNextApprovalUnavailable")
    : detail.resumable
      ? t("taskNextResume")
      : detail.status === "running" || detail.status === "queued"
        ? t("taskNextMonitor")
        : detail.status === "partial"
          ? t("taskNextReviewPartial")
          : detail.status === "failed"
            ? t("taskNextReviewFailure")
          : t("taskNextNone");

  return (
    <section className="task-detail" aria-label={t("taskExecutionDetails")}>
      <div className="task-detail-summary">
        <div>
          <strong>{t("taskNextAction")}</strong>
          <p>{nextAction}</p>
        </div>
        {detail.result && (
          <div>
            <strong>{t("taskResult")}</strong>
            <p>{detail.result}</p>
          </div>
        )}
        {detail.error && (
          <div className="error-bubble" role="status">
              {t("taskFailed")}: {detail.error}
          </div>
        )}
      </div>

      <h4>{t("taskStepsAndEvidence")}</h4>
      {!detail.steps?.length && <p className="muted">{t("taskNoStepEvidence")}</p>}
      {detail.steps?.map((step: TaskStep) => (
        <article className="task-detail-step" key={step.id}>
          <div className="row between">
            <strong>{step.idx + 1}. {step.description}</strong>
            <span className={`badge ${step.status === "failed" ? "fail" : ""}`}>
              {step.status}
            </span>
          </div>
          <dl className="task-detail-facts">
            <div>
              <dt>{t("taskCapability")}</dt>
              <dd>{step.capability || step.tool_id || t("taskNotRecorded")}</dd>
            </div>
            <div>
              <dt>{t("taskOutcome")}</dt>
              <dd>{outcomeLabel(step.outcome_status, t)}</dd>
            </div>
            <div>
              <dt>{t("taskVerification")}</dt>
              <dd>{step.verification_status
                ? outcomeLabel(step.verification_status, t)
                : t("taskOutcomeNotRecorded")}</dd>
            </div>
            {step.approval_id && (
              <div>
                <dt>{t("taskApprovalStatus")}</dt>
                <dd>{step.approval_status || t("taskApprovalUnavailable")}</dd>
              </div>
            )}
            {(step.dependency_step_ids.length > 0 || step.dependencies.length > 0) && (
              <div>
                <dt>{t("taskDependencies")}</dt>
                <dd>{(step.dependency_step_ids.length
                  ? step.dependency_step_ids
                  : step.dependencies.map((index) => String(index + 1))
                ).join(", ")}</dd>
              </div>
            )}
            {step.error_code && (
              <div>
                <dt>{t("taskErrorCode")}</dt>
                <dd>{step.error_code}</dd>
              </div>
            )}
          </dl>
          {(step.safe_summary || step.detail) && (
            <p>{step.safe_summary || step.detail}</p>
          )}
          {step.evidence.length > 0 && (
            <div>
              <strong>{t("taskEvidence")}</strong>
              <ul className="task-evidence-list">
                {step.evidence.map((item, index) => {
                  const url = evidenceUrl(item);
                  const label = String(
                    item.title || item.label || item.type || t("taskEvidenceItem"),
                  );
                  return (
                    <li key={`${step.id}-evidence-${index}`}>
                      {url
                        ? <a href={url} target="_blank" rel="noreferrer">{label}</a>
                        : label}
                      {typeof item.summary === "string" && ` — ${item.summary}`}
                    </li>
                  );
                })}
              </ul>
            </div>
          )}
        </article>
      ))}

      <h4>{t("taskApprovals")}</h4>
      {!approvals.length && <p className="muted">{t("taskNoApprovals")}</p>}
      {approvals.map((approval) => {
        const pending = approval.status === "pending";
        const actionable = pending && approval.claim_status === "unclaimed";
        const draftIsDirty = approval.can_edit
          && drafts[approval.id] !== JSON.stringify(approval.arguments, null, 2);
        return (
          <article className="task-approval" key={approval.id}>
            <div className="row between">
              <strong>{approval.tool_name}</strong>
              <span className={`badge ${pending ? "warn" : ""}`}>{approval.status}</span>
            </div>
            <p className="muted small">
              {t("taskApprovalReview")} · {t("taskApprovalLevel")} {approval.level}
            </p>
            {pending && <p className="muted small">{t("taskApprovalScopeHint")}</p>}
            {approval.kind !== "credentials" && (
              <pre className="task-approval-payload">
                {JSON.stringify(approval.arguments, null, 2)}
              </pre>
            )}
            {pending && approval.kind === "credentials" && (
              <p>{t("taskCredentialApprovalHint")}</p>
            )}
            {pending && approval.kind !== "credentials" && !approval.can_approve && (
              <p>{t("taskApprovalHiddenPayloadHint")}</p>
            )}
            {approval.can_edit && actionable && (
              <div className="task-approval-edit">
                <label htmlFor={`approval-payload-${approval.id}`}>
                  {t("taskEditApproval")}
                </label>
                <textarea
                  id={`approval-payload-${approval.id}`}
                  aria-label={`${t("taskEditApproval")}: ${approval.tool_name}`}
                  value={drafts[approval.id] ?? ""}
                  rows={5}
                  spellCheck={false}
                  onChange={(event) => setDrafts((current) => ({
                    ...current,
                    [approval.id]: event.target.value,
                  }))}
                />
                <button
                  className="btn small"
                  type="button"
                  disabled={saving === approval.id || deciding === approval.id || !draftIsDirty}
                  onClick={() => void saveDraft(approval)}
                >
                  {saving === approval.id ? t("saving") : t("taskSaveApproval")}
                </button>
                {draftIsDirty && (
                  <p className="muted small" role="status">{t("taskSaveBeforeApprove")}</p>
                )}
              </div>
            )}
            {actionable && (
              <div className="row gap">
                {!approval.can_approve && detail.conversation_id && (
                  <button
                    className="btn small"
                    type="button"
                    onClick={() => onOpenConversation(detail.conversation_id as string)}
                  >
                    {t("taskOpenOrigin")}
                  </button>
                )}
                <button
                  className="btn small danger"
                  type="button"
                  disabled={deciding === approval.id || saving === approval.id}
                  onClick={() => void decide(approval, false)}
                >
                  {t("taskRejectApproval")}
                </button>
                {approval.can_approve && (
                  <button
                    className="btn small primary"
                    type="button"
                    disabled={
                      deciding === approval.id
                      || saving === approval.id
                      || draftIsDirty
                    }
                    onClick={() => void decide(approval, true)}
                  >
                    {deciding === approval.id ? t("taskLoading") : t("approve")}
                  </button>
                )}
              </div>
            )}
            {pending && !actionable && (
              <p className="muted">{t("taskApprovalInProgress")}</p>
            )}
          </article>
        );
      })}
    </section>
  );
}
