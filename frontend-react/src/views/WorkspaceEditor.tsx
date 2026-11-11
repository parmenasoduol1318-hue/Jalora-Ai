/** PHASE J (§55+): Monaco-backed code editor surface.
 *
 * Replaces the plain <textarea> editor in WorkspaceView with an in-place VS
 * Code-style editor (tabs are handled by the parent's file tree; Monaco gives
 * us syntax highlighting, minimap, IntelliSense, Ctrl/Cmd+S save and a
 * Ctrl/Cmd+P-style feel).
 *
 * Monaco is a heavy (~10-15 MB) chunk, so it is lazy-loaded via React.Suspense.
 * While the chunk streams in we fall back to the original textarea (unchanged
 * behaviour — the file is still editable and saveable), so the rest of the
 * workspace never blocks on the editor.
 *
 * The editor is *controlled* by `value` (the parent's `editorText`). The
 * parent only ever changes `value` when a different file is opened or the
 * model is saved — never on every keystroke — so Monaco's own equality guard
 * keeps the cursor stable while the user types.
 */
import { lazy, Suspense, useEffect } from "react";
import loader from "@monaco-editor/loader";

const MonacoEditor = lazy(() =>
  import("@monaco-editor/react").then((mod) => ({ default: mod.Editor })),
);

/**
 * Pin Monaco's AMD loader to the jsDelivr CDN (reachable in this deployment).
 * @monaco-editor/loader injects `<script src="<paths.vs>/loader.js">` at
 * runtime and is NOT statically traced by Rollup, so without an explicit path
 * Monaco would only load via the package's own default. Setting it here makes
 * Monaco render deterministically in BOTH the Vite dev server and the
 * production build (the bundle intentionally stays small — Monaco is ~10-15 MB
 * and is fetched lazily from the CDN, not inlined).
 */
const MONACO_VS = "https://cdn.jsdelivr.net/npm/monaco-editor@0.57.0/min/vs";
let monacoLoaderConfigured = false;
function ensureMonacoLoaderConfigured() {
  if (monacoLoaderConfigured) return;
  monacoLoaderConfigured = true;
  loader.config({ paths: { vs: MONACO_VS } });
}
// This module is React.lazy'd by WorkspaceView, so its top level executes
// before <MonacoEditor> mounts — i.e. before the react wrapper calls init().
ensureMonacoLoaderConfigured();

export interface WorkspaceEditorProps {
  /** File path (used for extension -> language mapping); "" when no file. */
  path: string;
  /** Current editor text (controlled by the parent). */
  value: string;
  /** Disable while a save is in flight. */
  saving: boolean;
  /** Called with the new text on every change. */
  onChange: (value: string | undefined) => void;
  /** Persist the current buffer. */
  onSave: () => void;
  /** Translator function from the host view. */
  t: (key: string) => string;
}

const LANG: Record<string, string> = {
  ".ts": "typescript",
  ".tsx": "typescript",
  ".js": "javascript",
  ".jsx": "javascript",
  ".py": "python",
  ".css": "css",
  ".html": "html",
  ".json": "json",
  ".md": "markdown",
  ".sql": "sql",
  ".sh": "shell",
  ".yaml": "yaml",
  ".yml": "yaml",
  ".xml": "xml",
  ".go": "go",
  ".rs": "rust",
  ".java": "java",
  ".c": "c",
  ".cpp": "cpp",
  ".h": "c",
  ".hpp": "cpp",
  ".toml": "ini",
};

function langFromPath(path: string): string {
  if (!path) return "plaintext";
  const ext = path.slice(path.lastIndexOf("."));
  return LANG[ext.toLowerCase()] || "plaintext";
}

function EditorSkeleton({ value, t }: Pick<WorkspaceEditorProps, "value" | "t">) {
  return (
    <textarea
      className="ws-monaco-fallback"
      value={value}
      placeholder={t("wsMonacoLoading")}
      spellCheck
      readOnly
    />
  );
}

export function WorkspaceEditor({ path, value, saving, onChange, onSave, t }: WorkspaceEditorProps) {
  // Ctrl/Cmd+S save — mirrors the onKeyDown handler the textarea used to own.
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s" && !saving) {
        e.preventDefault();
        onSave();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onSave, saving]);

  return (
    <Suspense fallback={<EditorSkeleton value={value} t={t} />}>
      <div className="ws-monaco">
        <MonacoEditor
          theme="vs-dark"
          language={langFromPath(path)}
          value={value}
          onChange={onChange}
        />
      </div>
    </Suspense>
  );
}
