import { useCallback, useEffect, useState } from "react";
import {
  api, createIdeaProject, listIdeaProjects, setIdeaProjectArchived, updateIdeaProject,
} from "../lib/api";
import type { Conversation, IdeaProject, Task } from "../lib/types";

type ProjectDraft = Omit<IdeaProject, "id" | "archived" | "created_at" | "updated_at">;

const EMPTY_DRAFT: ProjectDraft = {
  title: "",
  brief: "",
  goals: "",
  next_steps: "",
  notes: "",
  milestones: [],
  task_ids: [],
  conversation_ids: [],
};

function lineItems(value: string): string[] {
  return value.split("\n").map((item) => item.trim()).filter(Boolean);
}

export function IdeaProjectsView({
  t, onToast, onOpenConversation, onOpenTask,
}: {
  t: (key: string) => string;
  onToast: (message: string, error?: boolean) => void;
  onOpenConversation: (id: string) => void;
  onOpenTask: (id: string) => void;
}) {
  const [includeArchived, setIncludeArchived] = useState(false);
  const [projects, setProjects] = useState<IdeaProject[]>([]);
  const [draft, setDraft] = useState<ProjectDraft | null>(null);
  const [projectId, setProjectId] = useState<string | null>(null);
  const [selectedArchived, setSelectedArchived] = useState(false);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  const loadProjects = useCallback(async () => {
    setLoading(true);
    try {
      setProjects(await listIdeaProjects(includeArchived));
    } catch (error) {
      onToast((error as Error).message || "Couldn't load idea projects.", true);
    } finally {
      setLoading(false);
    }
  }, [includeArchived, onToast]);

  useEffect(() => { void loadProjects(); }, [loadProjects]);

  useEffect(() => {
    void Promise.all([
      api<Task[]>("/api/tasks?limit=100"),
      api<Conversation[]>("/api/conversations?include_archived=true"),
    ]).then(([taskRows, conversationRows]) => {
      setTasks(taskRows);
      setConversations(conversationRows);
    }).catch((error: unknown) => {
      onToast(
        error instanceof Error ? error.message : "Couldn't load related tasks or chats.",
        true,
      );
    });
  }, [onToast]);

  const openProject = async (id: string) => {
    try {
      const project = await api<IdeaProject>(`/api/idea-projects/${encodeURIComponent(id)}`);
      setProjectId(project.id);
      setSelectedArchived(project.archived);
      setDraft(project);
    } catch (error) {
      onToast((error as Error).message, true);
    }
  };

  const save = async () => {
    if (!draft) return;
    setSaving(true);
    try {
      const saved = projectId
        ? await updateIdeaProject(projectId, draft)
        : await createIdeaProject(draft);
      setProjectId(saved.id);
      setSelectedArchived(saved.archived);
      setDraft(saved);
      await loadProjects();
      onToast(t(projectId ? "ideaSaved" : "ideaCreated"));
    } catch (error) {
      onToast((error as Error).message, true);
    } finally {
      setSaving(false);
    }
  };

  const toggleArchive = async () => {
    if (!projectId || !draft) return;
    setSaving(true);
    try {
      const updated = await setIdeaProjectArchived(projectId, !selectedArchived);
      setSelectedArchived(updated.archived);
      setDraft(updated);
      await loadProjects();
      onToast(t(updated.archived ? "ideaArchived" : "ideaReopened"));
    } catch (error) {
      onToast((error as Error).message, true);
    } finally {
      setSaving(false);
    }
  };

  const setField = <K extends keyof ProjectDraft>(key: K, value: ProjectDraft[K]) => {
    setDraft((current) => current ? { ...current, [key]: value } : current);
  };

  return (
    <section className="idea-projects">
      <header className="idea-projects-header">
        <div>
          <h1>{t("ideaHubTitle")}</h1>
          <p>{t("ideaIntro")}</p>
        </div>
        <div className="idea-projects-actions">
          <button
            className="btn"
            type="button"
            aria-pressed={includeArchived}
            onClick={() => setIncludeArchived((current) => !current)}
          >
            {t(includeArchived ? "ideaHideArchived" : "ideaShowArchived")}
          </button>
          <button
            className="btn primary"
            type="button"
            onClick={() => {
              setProjectId(null);
              setSelectedArchived(false);
              setDraft({ ...EMPTY_DRAFT });
            }}
          >
            {t("ideaNew")}
          </button>
        </div>
      </header>

      <div className="idea-projects-layout">
        <aside className="idea-project-list" aria-label={t("ideaHubTitle")}>
          {loading && <p className="idea-muted">{t("ideaLoading")}</p>}
          {!loading && projects.length === 0 && (
            <p className="idea-muted">{t("ideaEmpty")}</p>
          )}
          {projects.map((project) => (
            <button
              key={project.id}
              type="button"
              className={`idea-project-row ${project.id === projectId ? "selected" : ""}`}
              onClick={() => void openProject(project.id)}
            >
              <strong>{project.title}</strong>
              <span>{project.brief || t("ideaNoBrief")}</span>
              {project.archived && <small>{t("ideaArchivedLabel")}</small>}
            </button>
          ))}
        </aside>

        {draft ? (
          <div className="idea-editor">
            <div className="idea-editor-actions">
              <button
                className="btn primary"
                type="button"
                disabled={saving}
                onClick={() => void save()}
              >
                {saving ? t("ideaSaving") : t("ideaSave")}
              </button>
              {projectId && (
                <button
                  className="btn"
                  type="button"
                  disabled={saving}
                  onClick={() => void toggleArchive()}
                >
                  {selectedArchived ? t("ideaReopen") : t("ideaArchive")}
                </button>
              )}
              <button
                className="btn"
                type="button"
                onClick={() => {
                  setProjectId(null);
                  setSelectedArchived(false);
                  setDraft(null);
                }}
              >
                {t("ideaClose")}
              </button>
            </div>

            <label className="idea-field">
              <span>{t("ideaTitle")}</span>
              <input
                data-testid="idea-title"
                value={draft.title}
                maxLength={255}
                onChange={(event) => setField("title", event.target.value)}
              />
            </label>
            <label className="idea-field">
              <span>{t("ideaBrief")}</span>
              <textarea
                data-testid="idea-brief"
                value={draft.brief}
                onChange={(event) => setField("brief", event.target.value)}
              />
            </label>
            <label className="idea-field">
              <span>{t("ideaGoals")}</span>
              <textarea
                value={draft.goals}
                onChange={(event) => setField("goals", event.target.value)}
              />
            </label>
            <label className="idea-field">
              <span>{t("ideaNextSteps")}</span>
              <textarea
                value={draft.next_steps}
                onChange={(event) => setField("next_steps", event.target.value)}
              />
            </label>
            <label className="idea-field">
              <span>{t("ideaMilestones")}</span>
              <textarea
                value={draft.milestones.join("\n")}
                onChange={(event) => setField("milestones", lineItems(event.target.value))}
              />
              <small>{t("ideaMilestonesHint")}</small>
            </label>
            <label className="idea-field">
              <span>{t("ideaNotes")}</span>
              <textarea
                value={draft.notes}
                onChange={(event) => setField("notes", event.target.value)}
              />
            </label>

            <div className="idea-related">
              <label className="idea-field">
                <span>{t("ideaRelatedTasks")}</span>
                <select
                  multiple
                  value={draft.task_ids}
                  onChange={(event) => setField(
                    "task_ids",
                    Array.from(event.currentTarget.selectedOptions, (option) => option.value),
                  )}
                >
                  {tasks.map((task) => (
                    <option key={task.id} value={task.id}>{task.goal || task.id}</option>
                  ))}
                </select>
              </label>
              <label className="idea-field">
                <span>{t("ideaRelatedChats")}</span>
                <select
                  multiple
                  value={draft.conversation_ids}
                  onChange={(event) => setField(
                    "conversation_ids",
                    Array.from(event.currentTarget.selectedOptions, (option) => option.value),
                  )}
                >
                  {conversations.map((conversation) => (
                    <option key={conversation.id} value={conversation.id}>
                      {conversation.title || conversation.id}
                    </option>
                  ))}
                </select>
              </label>
            </div>

            {(draft.task_ids.length > 0 || draft.conversation_ids.length > 0) && (
              <div className="idea-linked-open">
                {draft.task_ids.map((id) => {
                  const task = tasks.find((item) => item.id === id);
                  return (
                    <button className="btn small" type="button" key={id} onClick={() => onOpenTask(id)}>
                      {t("ideaOpenTask")}: {task?.goal || id}
                    </button>
                  );
                })}
                {draft.conversation_ids.map((id) => {
                  const conversation = conversations.find((item) => item.id === id);
                  return (
                    <button
                      className="btn small"
                      type="button"
                      key={id}
                      onClick={() => onOpenConversation(id)}
                    >
                      {t("ideaOpenChat")}: {conversation?.title || id}
                    </button>
                  );
                })}
              </div>
            )}
          </div>
        ) : (
          <div className="idea-editor idea-empty-selection">
            <p>{t("ideaSelectHint")}</p>
          </div>
        )}
      </div>
    </section>
  );
}
