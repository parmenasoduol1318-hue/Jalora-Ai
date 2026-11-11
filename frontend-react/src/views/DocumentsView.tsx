import { useEffect, useState } from "react";
import { api } from "../lib/api";
import type { DocItem } from "../lib/types";

export function DocumentsView({
  t, onToast,
}: {
  t: (k: string) => string;
  onToast: (m: string, e?: boolean) => void;
}) {
  const [docs, setDocs] = useState<DocItem[]>([]);
  const [asking, setAsking] = useState<string | null>(null);
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState("");
  const [answering, setAnswering] = useState(false);
  const [dragActive, setDragActive] = useState(false);
  const [query, setQuery] = useState("");
  const [searching, setSearching] = useState(false);
  const [results, setResults] = useState<DocItem[] | null>(null);

  const searchDocs = async (q: string) => {
    setQuery(q);
    if (!q.trim()) {
      setResults(null);
      return;
    }
    setSearching(true);
    try {
      setResults(await api<DocItem[]>(`/api/documents/search?q=${encodeURIComponent(q)}`));
    } catch (err) {
      setResults([]);
      onToast((err as Error).message, true);
    } finally {
      setSearching(false);
    }
  };

  const load = async () => {
    try {
      setDocs(await api<DocItem[]>("/api/documents"));
    } catch (err) {
      onToast((err as Error).message, true);
    }
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const upload = async (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    try {
      onToast(t("uploading").replace("{name}", file.name));
      const doc = await api<DocItem>("/api/documents", { method: "POST", formData: fd });
      onToast(
        doc.status === "ready"
          ? t("docReady").replace("{name}", doc.filename)
          : `${doc.filename}: ${doc.error || doc.status}`,
        doc.status !== "ready",
      );
      load();
    } catch (err) {
      onToast((err as Error).message, true);
    }
  };

  const openAsk = (doc: DocItem) => {
    setAsking(doc.id);
    setQuestion("");
    setAnswer("");
  };

  const submitQuestion = async (doc: DocItem) => {
    if (!question.trim() || answering) return;
    setAnswering(true);
    setAnswer("");
    try {
      const data = await api<{ answer: string }>(
        `/api/documents/${doc.id}/query`,
        { method: "POST", body: { question } },
      );
      setAnswer(data.answer);
    } catch (err) {
      onToast((err as Error).message, true);
    } finally {
      setAnswering(false);
    }
  };

  return (
    <>
      <div className="view-header">
        <h2>{t("documentsTitle")}</h2>
      </div>
      <div className="panel">
        <p style={{ color: "var(--text-dim)", fontSize: 13, marginBottom: 12 }}>
          {t("uploadHint")}
        </p>
        <div
          className={`upload-zone${dragActive ? " drag" : ""}`}
          onDragOver={(e) => {
            e.preventDefault();
            e.dataTransfer.dropEffect = "copy";
            setDragActive(true);
          }}
          onDragLeave={() => setDragActive(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragActive(false);
            const files = e.dataTransfer.files;
            if (files && files.length) for (const f of Array.from(files)) upload(f);
          }}
        >
          <span>📄</span>
          <div>{t("dropHere")}</div>
          <label className="btn primary" style={{ cursor: "pointer" }}>
            {t("or")} {t("chooseFile")}
            <input
              type="file"
              multiple
              accept=".pdf,.txt,.md,.csv,.json,.docx,.xlsx,.pptx"
              style={{ display: "none" }}
              onChange={(e) => {
                const files = e.target.files;
                if (files) for (const f of Array.from(files)) upload(f);
                e.target.value = "";
              }}
            />
          </label>
        </div>
        <div className="row gap" style={{ marginTop: 14 }}>
          <input
            type="text"
            value={query}
            placeholder={t("docSearchPlaceholder")}
            onChange={(e) => searchDocs(e.target.value)}
          />
        </div>
        {searching && <div className="meta" style={{ fontSize: 12, color: "var(--text-dim)" }}>{t("searching")}…</div>}
        {results !== null && results.length === 0 && (
          <div className="item-list"><div className="activity-empty">{t("docSearchEmpty")}</div></div>
        )}
        {results !== null && results.length > 0 && (
          <div className="item-list" style={{ marginTop: 10 }}>
            {results.map((d) => (
              <div key={d.id} className="item">
                <div className="content">
                  <div className="primary-text">🔎 {d.filename}</div>
                  <div className="meta">
                    {(d.size_bytes / 1024).toFixed(1)} KB ·{" "}
                    <span className={`badge ${d.status === "ready" ? "ok" : d.status === "failed" ? "fail" : "warn"}`}>
                      {d.status}
                    </span>
                  </div>
                </div>
                <button className="btn small" onClick={() => openAsk(d)}>{t("docAsk")}</button>
              </div>
            ))}
          </div>
        )}
        <div className="item-list">
          {docs.length === 0 && <div className="activity-empty">{t("docEmpty")}</div>}
          {docs.map((d) => (
            <div key={d.id}>
              <div className="item">
                <div className="content">
                  <div className="primary-text">📄 {d.filename}</div>
                  <div className="meta">
                    {(d.size_bytes / 1024).toFixed(1)} KB · {d.page_count || 0} {t("pagesUnit")} ·{" "}
                    <span className={`badge ${d.status === "ready" ? "ok" : d.status === "failed" ? "fail" : "warn"}`}>
                      {d.status}
                    </span>
                    {d.mime_type && <span> · {d.mime_type}</span>}
                  </div>
                  {d.progress !== undefined && d.progress < 100 && (
                    <div className="doc-progress">
                      <div className="doc-progress-fill" style={{ width: `${d.progress}%` }} />
                    </div>
                  )}
                </div>
                <div className="row gap">
                  <button
                    className="btn small"
                    disabled={d.status !== "ready"}
                    onClick={() => openAsk(d)}
                  >
                    {t("docAsk")}
                  </button>
                  <button
                    className="btn small del"
                    onClick={async () => {
                      try {
                        const result = await api<{ status?: string }>(
                          `/api/documents/${d.id}`,
                          { method: "DELETE" },
                        );
                        if (result.status === "paused_approval") {
                          onToast(t("docDeleteAwaitingApproval"));
                        } else {
                          if (asking === d.id) setAsking(null);
                          load();
                        }
                      } catch (err) {
                        onToast((err as Error).message, true);
                      }
                    }}
                  >
                    {t("delete")}
                  </button>
                </div>
              </div>
              {asking === d.id && (
                <div className="qa-panel">
                  <div className="qa-title">{t("askAboutLabel")} “{d.filename}”</div>
                  <div className="row gap">
                    <textarea
                      rows={2}
                      value={question}
                      placeholder={t("qaPlaceholder")}
                      onChange={(e) => setQuestion(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" && !e.shiftKey) {
                          e.preventDefault();
                          submitQuestion(d);
                        }
                      }}
                    />
                    <button
                      className="btn primary small"
                      disabled={answering || !question.trim()}
                      onClick={() => submitQuestion(d)}
                    >
                      {answering ? t("searching") : t("send")}
                    </button>
                    <button className="btn small" onClick={() => setAsking(null)}>
                      {t("cancel")}
                    </button>
                  </div>
                  {answer && (
                    <div className="qa-answer">
                      <div className="qa-answer-label">{t("answerLabel")}</div>
                      <div className="content-text">{answer}</div>
                    </div>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      </div>
    </>
  );
}
