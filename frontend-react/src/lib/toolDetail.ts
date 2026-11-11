/* Formats the structured `detail` a coding/exec tool returns into readable
   console text for the live coding view (Chat + Workspace). */
export function toolDetailText(detail: Record<string, unknown> | undefined): string {
  if (!detail) return "";
  const parts: string[] = [];
  if (detail.command) parts.push(`$ ${detail.command}`);
  if (detail.stdout) parts.push(String(detail.stdout));
  if (detail.stderr) parts.push(String(detail.stderr));
  if (detail.exit_code != null) parts.push(`(exit ${detail.exit_code})`);
  if (detail.diff) parts.push(String(detail.diff));
  else if (detail.path) parts.push(String(detail.path));
  if (Array.isArray(detail.files) && detail.files.length) {
    parts.push((detail.files as unknown[]).join("\n"));
  }
  if (Array.isArray(detail.file_changes)) {
    for (const change of detail.file_changes as Array<Record<string, unknown>>) {
      parts.push(
        `${change.status || "change"} ${change.path || ""}`
        + (change.diff ? `\n${String(change.diff)}` : ""),
      );
    }
  }
  if (detail.reply) parts.push(String(detail.reply));
  if (detail.reply_truncated) parts.push("(provider response truncated)");
  if (Array.isArray(detail.applied)) {
    for (const a of detail.applied as Array<Record<string, unknown>>) {
      parts.push(`• ${a.path}${a.diff ? "\n" + String(a.diff) : ""}`);
    }
  }
  if (Array.isArray(detail.failures) && detail.failures.length) {
    parts.push(`${detail.failures.length} edit(s) failed`);
  }
  if (Array.isArray(detail.diagnostics) && detail.diagnostics.length) {
    parts.push(
      (detail.diagnostics as Array<Record<string, unknown>>)
        .map((d) => `${d.path || d.file || "?"} ${d.message || d.detail || ""}`)
        .join("\n"),
    );
  }
  return parts.join("\n").trim();
}
