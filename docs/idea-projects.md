# Idea projects

Idea projects are durable, owner-scoped records for work that spans multiple
chats or tasks. They are separate from Coding Workspace projects and Creative
Studio projects; no agent or task executor is created for them.

The **Idea Projects** view stores a title, brief, goals, next steps, notes,
milestone lines, and links to the owner's existing tasks and conversations.
Those links open the existing task or conversation. IDs are checked against the
authenticated owner before they are stored.

The API is under `/api/idea-projects`:

- `GET /` lists active projects; `?include_archived=true` includes archived ones.
- `POST /` creates a project.
- `GET /{id}` opens a project and `PATCH /{id}` edits it.
- `POST /{id}/archive` and `POST /{id}/reopen` preserve and restore projects.

Records are stored in the `idea_projects` table. Migration
`v1l_idea_projects` is additive, so project data survives logout, service
restarts, and normal database migrations.
