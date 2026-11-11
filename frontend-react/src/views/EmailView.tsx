import { useCallback, useEffect, useState } from "react";
import { api, uploadDocument } from "../lib/api";
import type {
  AutoReplyRuleT,
  EmailMessageT,
  EmailSendResultT,
} from "../lib/types";

type RulesForm = {
  name: string;
  sender_pattern: string;
  topic_pattern: string;
  template: string;
  tone: string;
  max_per_hour: number;
  max_per_day: number;
  requires_approval: boolean;
  enabled: boolean;
};

type EmailRuleActionResult = {
  status: string;
  approval?: { id?: string } | null;
};

const emptyForm: RulesForm = {
  name: "",
  sender_pattern: "",
  topic_pattern: "",
  template: "",
  tone: "professional",
  max_per_hour: 0,
  max_per_day: 10,
  requires_approval: true,
  enabled: true,
};

export function EmailView({
  t,
  onToast,
}: {
  t: (k: string) => string;
  onToast: (m: string, e?: boolean) => void;
}) {
  const [tab, setTab] = useState<"inbox" | "rules">("inbox");
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<EmailMessageT[]>([]);
  const [selected, setSelected] = useState<EmailMessageT | null>(null);
  const [composeOpen, setComposeOpen] = useState(false);
  const [draftingReply, setDraftingReply] = useState(false);
  const [to, setTo] = useState("");
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [attachments, setAttachments] = useState<Array<{ document_id: string; filename: string; mime_type: string; size_bytes: number }>>([]);
  const [uploadingAttachment, setUploadingAttachment] = useState(false);
  const [approvalId, setApprovalId] = useState<string | null>(null);
  const [rules, setRules] = useState<AutoReplyRuleT[]>([]);
  const [form, setForm] = useState<RulesForm>(emptyForm);

  const loadRules = useCallback(async () => {
    try {
      setRules(await api<AutoReplyRuleT[]>("/api/emails/rules"));
    } catch (err) {
      onToast((err as Error).message, true);
    }
  }, [onToast]);

  useEffect(() => {
    if (tab === "rules") loadRules();
  }, [tab, loadRules]);

  const runSearch = async (q: string) => {
    try {
      const data = await api<{ provider: string; results: EmailMessageT[]; query: string }>(
        `/api/emails/search?q=${encodeURIComponent(q || "")}&limit=20`,
      );
      setResults(data.results);
    } catch (err) {
      onToast((err as Error).message, true);
    }
  };

  const openEmail = async (id: string) => {
    try {
      const data = await api<{ provider: string; email: EmailMessageT }>(`/api/emails/${id}`);
      setSelected(data.email);
    } catch (err) {
      onToast((err as Error).message, true);
    }
  };

  const draftReply = async () => {
    if (!selected) return;
    setDraftingReply(true);
    try {
      const draft = await api<{
        status: "editable_draft";
        to: string;
        subject: string;
        body: string;
        task_id: string;
      }>(`/api/emails/${encodeURIComponent(selected.id)}/reply-draft`, {
        method: "POST",
        body: {},
      });
      setTo(draft.to);
      setSubject(draft.subject);
      setBody(draft.body);
      setAttachments([]);
      setApprovalId(null);
      setComposeOpen(true);
      onToast("Reply draft created for review. It has not been saved or sent.");
    } catch (err) {
      onToast((err as Error).message, true);
    } finally {
      setDraftingReply(false);
    }
  };

  const saveDraft = async () => {
    try {
      await api("/api/emails/compose", {
        method: "POST",
        body: { to, subject, body, attachments },
      });
      onToast(t("draftSaved"));
    } catch (err) {
      onToast((err as Error).message, true);
    }
  };

  const send = async () => {
    try {
      const res = await api<EmailSendResultT>("/api/emails/send", {
        method: "POST",
        body: { to, subject, body, attachments },
      });
      if (res.status === "paused_approval") {
        setApprovalId(res.approval?.id || null);
        onToast(t("sendPending"));
      } else {
        onToast(t("emailSent"));
        setComposeOpen(false);
        setTo(""); setSubject(""); setBody(""); setAttachments([]);
      }
    } catch (err) {
      onToast((err as Error).message, true);
    }
  };

  const decide = async (approve: boolean) => {
    if (!approvalId) return;
    try {
      if (approve) {
        await api(`/api/emails/send/${approvalId}`, {
          method: "PATCH", body: { to, subject, body, attachments },
        });
      }
      const res = await api<EmailSendResultT>(
        `/api/emails/send/${approvalId}/${approve ? "approve" : "reject"}`,
        { method: "POST" },
      );
      const ok = approve
        ? res.status === "sent"
        : res.status === "rejected";
      onToast(
        approve
          ? ok
            ? t("emailSent")
            : t("integrationError")
          : t("emailRejected"),
        !ok,
      );
      setApprovalId(null);
      if (ok && approve) {
        setComposeOpen(false);
        setTo(""); setSubject(""); setBody(""); setAttachments([]);
      }
    } catch (err) {
      onToast((err as Error).message, true);
    }
  };

  const toggleRule = async (rule: AutoReplyRuleT) => {
    try {
      const result = await api<EmailRuleActionResult>(`/api/emails/rules/${rule.id}`, {
        method: "PATCH",
        body: { enabled: !rule.enabled },
      });
      if (result.status === "paused_approval") {
        onToast(t("sendPending"));
      } else if (result.status === "completed") {
        await loadRules();
      } else {
        onToast(t("integrationError"), true);
      }
    } catch (err) {
      onToast((err as Error).message, true);
    }
  };

  const deleteRule = async (rule: AutoReplyRuleT) => {
    try {
      const result = await api<EmailRuleActionResult>(`/api/emails/rules/${rule.id}`, {
        method: "DELETE",
      });
      if (result.status === "paused_approval") {
        onToast(t("sendPending"));
      } else if (result.status === "completed") {
        await loadRules();
      } else {
        onToast(t("integrationError"), true);
      }
    } catch (err) {
      onToast((err as Error).message, true);
    }
  };

  const createRule = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      const result = await api<EmailRuleActionResult>("/api/emails/rules", {
        method: "POST",
        body: form,
      });
      if (result.status === "paused_approval") {
        onToast(t("sendPending"));
      } else if (result.status === "completed") {
        setForm(emptyForm);
        await loadRules();
      } else {
        onToast(t("integrationError"), true);
      }
    } catch (err) {
      onToast((err as Error).message, true);
    }
  };

  return (
    <>
      <div className="view-header">
        <h2>{t("emailTitle")}</h2>
        <div className="row gap">
          <button
            className={`btn small ${tab === "inbox" ? "primary" : ""}`}
            onClick={() => setTab("inbox")}
          >
            {t("emailTitle")}
          </button>
          <button
            className={`btn small ${tab === "rules" ? "primary" : ""}`}
            onClick={() => setTab("rules")}
          >
            {t("autoReplyTitle")}
          </button>
        </div>
      </div>

      {tab === "inbox" && (
        <>
          <div className="panel">
            <div className="row gap">
              <input
                className="grow"
                placeholder={t("emailSearchPlaceholder")}
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && runSearch(query)}
              />
              <button className="btn" onClick={() => runSearch(query)}>
                {t("searchLabel")}
              </button>
              <button className="btn" onClick={() => setComposeOpen((v) => !v)}>
                {t("compose")}
              </button>
            </div>

            {composeOpen && (
              <div className="compose-form">
                <div className="field">
                  <label>{t("toLabel")}</label>
                  <input
                    value={to}
                    onChange={(e) => setTo(e.target.value)}
                    placeholder="name@example.com"
                  />
                </div>
                <div className="field">
                  <label>{t("subjectLabel")}</label>
                  <input value={subject} onChange={(e) => setSubject(e.target.value)} />
                </div>
                <div className="field">
                  <label>Attachments</label>
                  <input
                    type="file"
                    multiple
                    disabled={uploadingAttachment}
                    onChange={async (e) => {
                      const files = Array.from(e.target.files || []);
                      if (!files.length) return;
                      setUploadingAttachment(true);
                      try {
                        for (const file of files) {
                          const doc = await uploadDocument(file);
                          if (doc.status !== "ready") throw new Error(`${doc.filename} is not ready for attachment`);
                          setAttachments((current) => [...current, {
                            document_id: doc.id, filename: doc.filename,
                            mime_type: doc.mime_type, size_bytes: doc.size_bytes,
                          }]);
                        }
                        onToast("Attachment ready");
                      } catch (err) {
                        onToast((err as Error).message, true);
                      } finally {
                        setUploadingAttachment(false);
                        e.target.value = "";
                      }
                    }}
                  />
                  {attachments.length > 0 && (
                    <div className="small muted">
                      {attachments.map((attachment) => (
                        <div className="row gap" key={attachment.document_id}>
                          <span>{attachment.filename}</span>
                          <button className="btn small" onClick={() => setAttachments((current) => current.filter((item) => item.document_id !== attachment.document_id))}>Remove</button>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
                <div className="field">
                  <textarea
                    value={body}
                    onChange={(e) => setBody(e.target.value)}
                    rows={4}
                  />
                </div>
                {approvalId && <div className="notice warn">{t("sendPending")}</div>}
                <div className="row gap">
                  <button className="btn" onClick={saveDraft}>
                    {t("saveDraft")}
                  </button>
                  {approvalId ? (
                    <>
                      <button className="btn small primary" onClick={() => decide(true)}>
                        {t("approve")}
                      </button>
                      <button className="btn small danger" onClick={() => decide(false)}>
                        {t("deny")}
                      </button>
                    </>
                  ) : (
                    <button className="btn primary" onClick={send} title={t("sendPending")}>
                      {t("sendEmailAction")}
                    </button>
                  )}
                </div>
              </div>
            )}
          </div>

          <div className="panel email-layout">
            <div className="email-list">
              {results.length === 0 && (
                <div className="muted small">{t("emailEmpty")}</div>
              )}
              {results.map((m) => (
                <button
                  key={m.id}
                  className={`email-item ${selected?.id === m.id ? "active" : ""}`}
                  onClick={() => openEmail(m.id)}
                >
                  <div className="email-subject">{m.subject || "(no subject)"}</div>
                  <div className="email-from">{m.from_addr}</div>
                  <div className="muted small">{m.snippet}</div>
                </button>
              ))}
            </div>
            <div className="email-msg">
              {selected ? (
                <>
                  <h3>{selected.subject || "(no subject)"}</h3>
                  <div className="muted small">
                    {selected.from_addr} → {selected.to_addr}
                  </div>
                  <pre className="email-body">{selected.body}</pre>
                  <button
                    className="btn small"
                    disabled={draftingReply}
                    onClick={() => void draftReply()}
                  >
                    {draftingReply ? "Drafting reply…" : "Draft reply with Jalora"}
                  </button>
                </>
              ) : (
                <div className="muted small">{t("emailEmpty")}</div>
              )}
            </div>
          </div>
        </>
      )}

      {tab === "rules" && (
        <div className="panel">
          <form onSubmit={createRule}>
            <h3>{t("newRule")}</h3>
            <div className="field">
              <label>{t("ruleName")}</label>
              <input
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                required
              />
            </div>
            <div className="field">
              <label>{t("ruleSender")}</label>
              <input
                value={form.sender_pattern}
                onChange={(e) => setForm({ ...form, sender_pattern: e.target.value })}
                placeholder={t("any")}
              />
            </div>
            <div className="field">
              <label>{t("ruleTopic")}</label>
              <input
                value={form.topic_pattern}
                onChange={(e) => setForm({ ...form, topic_pattern: e.target.value })}
                placeholder={t("any")}
              />
            </div>
            <div className="field">
              <label>{t("ruleTemplate")}</label>
              <textarea
                value={form.template}
                onChange={(e) => setForm({ ...form, template: e.target.value })}
                rows={3}
                placeholder="Thank you for reaching out, {name}. …"
              />
            </div>
            <div className="row gap">
              <div className="field">
                <label>{t("ruleTone")}</label>
                <select
                  value={form.tone}
                  onChange={(e) => setForm({ ...form, tone: e.target.value })}
                >
                  <option value="professional">{t("ruleToneProfessional")}</option>
                  <option value="friendly">{t("ruleToneFriendly")}</option>
                  <option value="brief">{t("ruleToneBrief")}</option>
                </select>
              </div>
              <div className="field">
                <label>{t("maxPerDay")}</label>
                <input
                  type="number" min={0}
                  value={form.max_per_day}
                  onChange={(e) => setForm({ ...form, max_per_day: Number(e.target.value) })}
                />
              </div>
              <div className="field">
                <label>{t("maxPerHour")}</label>
                <input
                  type="number" min={0}
                  value={form.max_per_hour}
                  onChange={(e) => setForm({ ...form, max_per_hour: Number(e.target.value) })}
                />
              </div>
              <label className="check">
                <input
                  type="checkbox"
                  checked={form.requires_approval}
                  onChange={(e) =>
                    setForm({ ...form, requires_approval: e.target.checked })
                  }
                />
                {t("requiresApprovalLabel")}
              </label>
            </div>
            <button type="submit" className="btn primary">
              {t("newRule")}
            </button>
          </form>

          <h3>{t("autoReplyTitle")}</h3>
          {rules.length === 0 && <div className="muted small">{t("autoReplyEmpty")}</div>}
          {rules.map((r) => (
            <div key={r.id} className="rule-row">
              <div className="grow">
                <div className="name">
                  {r.name}
                  {!r.enabled && <span className="muted"> · {t("disabled")}</span>}
                </div>
                <div className="model small">
                  {r.sender_pattern || t("any")} · {r.topic_pattern || t("any")} ·{" "}
                  {r.requires_approval ? t("requiresApprovalLabel") : t("polAutomatic")}
                </div>
              </div>
              <button className="btn small" onClick={() => toggleRule(r)}>
                {r.enabled ? t("disabled") : t("enable")}
              </button>
              <button className="btn small danger" onClick={() => deleteRule(r)}>
                {t("delete")}
              </button>
            </div>
          ))}
        </div>
      )}
    </>
  );
}
