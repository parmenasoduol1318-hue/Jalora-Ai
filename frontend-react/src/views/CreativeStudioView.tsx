/* Jalora Creative Studio View — professional multitrack editor + AI generation.

Models the industry-standard workspace layout (DaVinci Resolve / Adobe Premiere /
Final Cut Pro): a media bin, a source/program preview + multitrack timeline, and a
context-aware inspector, plus AI image/video generation, transcription, versioned
history and collaboration shares. All editing is persisted to the real Creative
Studio backend (see plans/jalora-creative-studio-implementation-plan.md).
*/
import { useState, useEffect, useRef, useCallback } from "react";
import type { Dispatch, FormEvent, MutableRefObject, SetStateAction } from "react";
import { api, mediaUrl } from "../lib/api";
// --------------------------------------------------------------------------- types

type MediaType = "image" | "video" | "audio";

interface MediaAsset {
  id: string;
  project_id?: string | null;
  filename: string;
  file_type: MediaType;
  source?: string;
  size_bytes: number;
  duration?: number | null;
  width?: number | null;
  height?: number | null;
  thumbnail_url?: string | null;
  metadata?: Record<string, unknown>;
  url?: string;
  file_path?: string;
  created_at?: string | null;
}

interface TimelineClip {
  id: string;
  track_id: string;
  asset_id?: string | null;
  clip_type: string;
  name?: string | null;
  start_time: number;
  duration: number;
  trim_start: number;
  trim_end: number;
  speed: number;
  opacity: number;
  z_index: number;
  link_group?: string | null;
  transform: Record<string, unknown>;
  effects: Record<string, unknown>;
  keyframes: Record<string, unknown>;
  text: Record<string, unknown>;
}

interface TimelineTrack {
  id: string;
  track_index: number;
  track_type: string;
  name: string;
  is_muted: boolean;
  is_locked: boolean;
  is_hidden: boolean;
  is_solo: boolean;
  volume: number;
  clips: TimelineClip[];
}

interface ProjectDetail {
  id: string;
  title: string;
  description?: string | null;
  project_type: string;
  aspect_ratio: string;
  status?: string;
  settings: Record<string, unknown>;
  thumbnail_url?: string | null;
  tracks: TimelineTrack[];
  assets: MediaAsset[];
  transcripts: TranscriptSegment[];
  created_at?: string | null;
  updated_at?: string | null;
}

interface Project {
  id: string;
  title: string;
  description?: string | null;
  project_type: string;
  thumbnail_url?: string | null;
  created_at?: string | null;
}

interface Template {
  id: string;
  title: string;
  category: string;
  kind: string;
  aspect_ratio?: string;
  preview_url?: string | null;
  tags?: unknown[];
  is_public?: boolean;
  data?: Record<string, unknown>;
}

interface EffectPreset {
  id: string;
  name: string;
  kind: string;
  category: string;
  params: Record<string, unknown>;
  is_builtin: boolean;
}

interface BrandKit {
  id: string;
  name: string;
  colors: string[];
  fonts: string[];
  logo_asset_id?: string | null;
  created_at?: string | null;
}

interface TranscriptSegment {
  id?: string;
  asset_id?: string | null;
  start_time: number;
  end_time: number;
  text: string;
  is_filler: boolean;
}

interface Share {
  id: string;
  role: string;
  token: string;
  shared_with_email?: string | null;
  expires_at?: string | null;
  created_at?: string | null;
}

interface Version {
  id: string;
  version_no: number;
  label?: string | null;
  created_at?: string | null;
}

interface GenJob {
  id: string;
  kind: string;
  provider?: string;
  model?: string;
  status: string;
  progress_pct?: number;
  message?: string;
  outputs?: { asset_id?: string; url?: string; meta?: Record<string, unknown> }[];
  created_at?: string;
}

interface RenderJob {
  id: string;
  kind: string;
  status: string;
  progress_pct?: number;
  message?: string;
  output_url?: string | null;
  output_asset_id?: string | null;
  settings?: Record<string, unknown>;
  error_message?: string | null;
  created_at?: string;
}

interface PlanOp {
  op: string;
  [k: string]: unknown;
}

interface AiPlan {
  ops: PlanOp[];
  planner: string;
  errors?: string[];
  explanation: string;
}

interface ToastFn {
  (msg: string, err?: boolean): void;
}

// Drag state shared between timeline rail and clip blocks.
type ClipDrag = {
  kind: "move" | "trim";
  clip: TimelineClip;
  startX: number;
  start: number;
  width: number;
} | null;

// --------------------------------------------------------------------------- helpers

function fmtTime(t: number | null | undefined): string {
  if (t == null || !isFinite(t)) return "00:00.00";
  const total = Math.max(0, t);
  const m = Math.floor(total / 60);
  const s = total - m * 60;
  return `${String(m).padStart(2, "0")}:${String(s.toFixed(2)).padStart(5, "0")}`;
}

function num(v: unknown, def = 0): number {
  const n = Number(v);
  return Number.isFinite(n) ? n : def;
}

function assetDuration(a: MediaAsset | null | undefined): number {
  return num(a?.duration, 5) || 5;
}

function friendlyPreset(p: string): string {
  return p.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

// --------------------------------------------------------------------------- Projects

function ProjectsView({
  projects, onOpen, onUpdated, toast,
}: {
  projects: Project[];
  onOpen: (id: string) => void;
  onUpdated: () => void;
  toast: ToastFn;
}) {
  const [showNew, setShowNew] = useState(false);
  const [title, setTitle] = useState("");
  const [type, setType] = useState("video");
  const [busy, setBusy] = useState(false);

  const create = async (e: FormEvent) => {
    e.preventDefault();
    if (!title.trim()) return;
    setBusy(true);
    try {
      const res = await api<{ success: boolean; project: Project }>(
        "/api/creative/projects", { method: "POST", body: { title, project_type: type } });
      if (res.success) {
        toast("Project created"); setShowNew(false); setTitle("");
        onUpdated(); onOpen(res.project.id);
      }
    } catch (err: unknown) {
      toast((err as Error).message || "Create failed", true);
    } finally {
      setBusy(false);
    }
  };

  const duplicate = async (id: string) => {
    try {
      await api<{ success: boolean }>("/api/creative/projects/" + id + "/duplicate", { method: "POST" });
      toast("Project duplicated"); onUpdated();
    } catch (err: unknown) {
      toast((err as Error).message || "Duplicate failed", true);
    }
  };

  const rename = async (id: string, newTitle: string) => {
    try {
      await api<{ success: boolean }>("/api/creative/projects/" + id,
        { method: "PATCH", body: { title: newTitle } });
      onUpdated();
    } catch (err: unknown) {
      toast((err as Error).message || "Rename failed", true);
    }
  };

  const del = async (id: string) => {
    if (!confirm("Delete this project? This cannot be undone.")) return;
    try {
      await api<{ success: boolean }>("/api/creative/projects/" + id, { method: "DELETE" });
      toast("Project deleted"); onUpdated();
    } catch (err: unknown) {
      toast((err as Error).message || "Delete failed", true);
    }
  };

  return (
    <div style={{ maxWidth: 1200, width: "100%", margin: "0 auto" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 20 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700 }}>Your Creative Projects</h1>
          <p style={{ fontSize: 13, color: "var(--text-dim)", marginTop: 4 }}>
            Videos, images, and AI generation sessions.
          </p>
        </div>
        <button onClick={() => setShowNew(true)} className="btn primary"
          style={{ padding: "10px 18px", fontWeight: 600, fontSize: 13 }}>+ New Project</button>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(280px,1fr))", gap: 20 }}>
        {projects.map((p) => (
          <div key={p.id} onClick={() => onOpen(p.id)}
            style={{ background: "var(--bg-2)", border: "1px solid var(--border)", borderRadius: 12,
              padding: 16, cursor: "pointer" }}>
            <div style={{ height: 140, borderRadius: 8, background: "var(--bg-3)", marginBottom: 12,
              border: "1px solid var(--border)", display: "flex", alignItems: "center",
              justifyContent: "center", position: "relative" }}>
              {p.thumbnail_url ? (
                <img src={mediaUrl(p.thumbnail_url)} alt={p.title}
                  style={{ width: "100%", height: "100%", objectFit: "cover" }} />
              ) : (
                <span style={{ fontSize: 36 }}>🎬</span>
              )}
              <span style={{ position: "absolute", top: 8, right: 8, fontSize: 10,
                textTransform: "uppercase", padding: "2px 6px", borderRadius: 4,
                background: "rgba(0,0,0,0.7)" }}>{p.project_type}</span>
            </div>
            <h3 style={{ fontSize: 15, fontWeight: 600, whiteSpace: "nowrap", overflow: "hidden",
              textOverflow: "ellipsis" }}>{p.title}</h3>
            <p style={{ fontSize: 12, color: "var(--text-dim)", marginTop: 4 }}>
              Updated {p.created_at ? new Date(p.created_at).toLocaleDateString() : "Recently"}
            </p>
            <div style={{ display: "flex", gap: 6, marginTop: 8 }}>
              <button onClick={(e) => { e.stopPropagation(); duplicate(p.id); }}
                className="btn small" title="Duplicate">📄</button>
              <button onClick={(e) => { e.stopPropagation(); const t = prompt("New title", p.title); if (t) rename(p.id, t); }}
                className="btn small" title="Rename">✏️</button>
              <button onClick={(e) => { e.stopPropagation(); del(p.id); }}
                className="btn small danger" title="Delete">🗑</button>
            </div>
          </div>
        ))}
        {projects.length === 0 && (
          <div style={{ gridColumn: "span 3", textAlign: "center", padding: 60, color: "var(--text-dim)" }}>
            No projects found. Create your first project to get started.
          </div>
        )}
      </div>

      {showNew && (
        <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.8)", display: "flex",
          alignItems: "center", justifyContent: "center", zIndex: 1000, padding: 16 }}>
          <div style={{ background: "var(--bg-2)", border: "1px solid var(--border)", borderRadius: 16,
            maxWidth: 420, width: "100%", padding: 24 }}>
            <h2 style={{ fontSize: 18, fontWeight: 700, marginBottom: 16 }}>New Creative Project</h2>
            <form onSubmit={create} style={{ display: "flex", flexDirection: "column", gap: 14 }}>
              <div>
                <label style={{ fontSize: 12, fontWeight: 600, color: "var(--text-dim)" }}>Title</label>
                <input type="text" value={title} onChange={(e) => setTitle(e.target.value)}
                  placeholder="e.g. Cinematic Travel Vlog" style={{ width: "100%" }} required />
              </div>
              <div>
                <label style={{ fontSize: 12, fontWeight: 600, color: "var(--text-dim)" }}>Project Type</label>
                <select value={type} onChange={(e) => setType(e.target.value)} style={{ width: "100%" }}>
                  <option value="video">Multitrack Video Editor</option>
                  <option value="image">Advanced Image Studio</option>
                  <option value="audio">Audio & Voice Studio</option>
                </select>
              </div>
              <div style={{ display: "flex", justifyContent: "flex-end", gap: 10 }}>
                <button type="button" onClick={() => setShowNew(false)} className="btn">Cancel</button>
                <button type="submit" disabled={busy} className="btn primary">
                  {busy ? "Creating..." : "Create"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}

// ---- AI Image generation -----------------------------------------------------

function OfflineBanner() {
  return (
    <div style={{ background: "rgba(255,184,0,0.15)", border: "1px solid rgba(255,184,0,0.4)",
      borderRadius: 8, padding: "10px 14px", fontSize: 12, color: "#ffd43b" }}>
      <strong>Offline mode:</strong> No cloud image provider configured or quota exhausted.
      Falling back to the local procedural engine which draws prompt-aware shapes
      (tree, house, person, car, etc.) using Pillow. Install torch+diffusers or set
      an API key for photorealistic results.
    </div>
  );
}

function Sel({ label, value, onChange, options }: {
  label: string; value: string; onChange: (v: string) => void;
  options: [string, string][];
}) {
  return (
    <div style={{ flex: 1, minWidth: 140 }}>
      <label style={{ fontSize: 12, fontWeight: 600, color: "var(--text-dim)", marginBottom: 6 }}>
        {label}
      </label>
      <select value={value} onChange={(e) => onChange(e.target.value)} style={{ width: "100%" }}>
        {options.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
      </select>
    </div>
  );
}

function ImageGenView({ toast }: { toast: ToastFn }) {
  const [prompt, setPrompt] = useState("");
  const [negative, setNegative] = useState("");
  const [aspect, setAspect] = useState("16:9");
  const [provider, setProvider] = useState("auto");
  const [seed, setSeed] = useState("");
  const [strength, setStrength] = useState(0.75);
  const [refAsset, setRefAsset] = useState<string>("");
  const [maskAsset, setMaskAsset] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<MediaAsset | null>(null);

  const gen = async () => {
    if (!prompt.trim()) return;
    setBusy(true);
    try {
      const body: Record<string, unknown> = {
        prompt, aspect_ratio: aspect, provider, num_variations: 1, strength,
      };
      if (negative.trim()) body.negative_prompt = negative;
      if (seed.trim()) body.seed = parseInt(seed, 10);
      if (refAsset) body.reference_asset_id = refAsset;
      if (maskAsset) body.mask_asset_id = maskAsset;
      if (provider === "openai" || provider === "google") {
        // cloud providers ignore strength/mask; harmless to omit
        delete body.strength;
      }
      const res = await api<{ success: boolean; asset?: MediaAsset }>(
        "/api/creative/generate/image", { method: "POST", body });
      if (res.success) { setResult(res.asset ?? null); toast("Image generated"); }
    } catch (err: unknown) {
      toast((err as Error).message || "Image generation failed", true);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ maxWidth: 820, width: "100%", margin: "0 auto" }}>
      <h1 style={{ fontSize: 22, fontWeight: 700, marginBottom: 16 }}>AI Image Generator &amp; Inpainting Studio</h1>
      <div style={{ background: "var(--bg-2)", border: "1px solid var(--border)", borderRadius: 16,
        padding: 24, display: "flex", flexDirection: "column", gap: 16 }}>
        <div>
          <label style={{ fontSize: 12, fontWeight: 600, color: "var(--text-dim)" }}>Prompt / Description</label>
          <textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} rows={3}
            placeholder="Cinematic portrait of a cyberpunk explorer in neon rain, 8k..." style={{ width: "100%" }} />
        </div>
        <div>
          <label style={{ fontSize: 12, fontWeight: 600, color: "var(--text-dim)" }}>Negative Prompt</label>
          <textarea value={negative} onChange={(e) => setNegative(e.target.value)} rows={2}
            placeholder="deformed hands, blurry, low quality, watermark..." style={{ width: "100%" }} />
        </div>
        <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
          <Sel label="Aspect" value={aspect} onChange={setAspect}
            options={[["16:9", "16:9 Landscape"], ["9:16", "9:16 Vertical"], ["1:1", "1:1 Square"], ["4:3", "4:3 Standard"]]} />
          <Sel label="Provider" value={provider} onChange={setProvider}
            options={[["auto", "Auto"], ["openai", "OpenAI (GPT Image 1)"],
              ["google", "Google (Gemini)"], ["local-ai", "Local AI (SDXL-Lightning)"],
              ["local", "Local (Procedural)"]]} />
          <div style={{ flex: 1, minWidth: 100 }}>
            <label style={{ fontSize: 12, fontWeight: 600, color: "var(--text-dim)" }}>Seed</label>
            <input type="number" value={seed} onChange={(e) => setSeed(e.target.value)}
              placeholder="Random" style={{ width: "100%" }} />
          </div>
          <div style={{ flex: 1, minWidth: 100 }}>
            <label style={{ fontSize: 12, fontWeight: 600, color: "var(--text-dim)" }}>Reference asset id</label>
            <input type="text" value={refAsset} onChange={(e) => setRefAsset(e.target.value)}
              placeholder="for img2img" style={{ width: "100%" }} />
          </div>
          <div style={{ flex: 1, minWidth: 100 }}>
            <label style={{ fontSize: 12, fontWeight: 600, color: "var(--text-dim)" }}>Mask asset id</label>
            <input type="text" value={maskAsset} onChange={(e) => setMaskAsset(e.target.value)}
              placeholder="for in-painting" style={{ width: "100%" }} />
          </div>
        </div>
        <div style={{ display: "flex", gap: 16, alignItems: "flex-end" }}>
          <div style={{ flex: 1 }}>
            <label style={{ fontSize: 12, fontWeight: 600, color: "var(--text-dim)" }}>
              Img2Img Strength ({strength.toFixed(2)})
            </label>
            <input type="range" min={0} max={1} step={0.05} value={strength}
              onChange={(e) => setStrength(parseFloat(e.target.value) || 0)} style={{ width: "100%" }} />
          </div>
          <button onClick={gen} disabled={busy} className="btn primary" style={{ padding: "11px 20px" }}>
            {busy ? "Generating..." : "Generate Image"}
          </button>
        </div>
        {provider === "local" && <OfflineBanner />}
        {result && (
          <div style={{ marginTop: 20, border: "1px solid var(--border)", borderRadius: 12, padding: 16,
            background: "var(--bg-3)", display: "flex", flexDirection: "column", alignItems: "center" }}>
            <div style={{ position: "relative", display: "inline-block" }}>
              <img src={mediaUrl(result.url)} alt="generated"
                style={{ maxHeight: 380, borderRadius: 8, border: "1px solid var(--border)" }} />
              {(result.metadata?.ai === false || result.metadata?.mode === "procedural") && (
                <span style={{ position: "absolute", top: 4, right: 4, fontSize: 10,
                  fontWeight: 700, textTransform: "uppercase", padding: "3px 8px", borderRadius: 4,
                  background: "rgba(255,184,0,0.85)", color: "#1a1a1a",
                  border: "1px solid rgba(255,184,0,1)" }}>⚠ Procedural</span>
              )}
            </div>
            <p style={{ fontSize: 11, color: "var(--text-dim)", marginTop: 8 }}>
              {(result.metadata?.provider as string) || "unknown"} ·
              AI: {String(result.metadata?.ai)} ·
              Model: {(result.metadata?.model as string) || "n/a"}
              {result.metadata?.mode ? (" · Mode: " + String(result.metadata.mode)) : null}
            </p>
            {result.metadata?.fallback ? (
              <p style={{ fontSize: 10, color: "#ffd43b", marginTop: 2, marginBottom: 0 }}>
                Fallback: {String((result.metadata?.fallback as Record<string, unknown>)?.reason || "unknown")}
                {" from " + String((result.metadata?.fallback as Record<string, unknown>)?.from || "unknown")}
              </p>
            ) : null}
            <p style={{ fontSize: 12, color: "var(--text-dim)", marginTop: 4 }}>{result.filename}</p>
          </div>
        )}
      </div>
    </div>
  );
}

// ---- AI Video generation (async, polled) -------------------------------------

function VideoGenView({ toast }: { toast: ToastFn }) {
  const [prompt, setPrompt] = useState("");
  const [aspect, setAspect] = useState("16:9");
  const [duration, setDuration] = useState(5);
  const [provider, setProvider] = useState("auto");
  const [refAsset, setRefAsset] = useState("");
  const [busy, setBusy] = useState(false);
  const [job, setJob] = useState<GenJob | null>(null);
  const [pollId, setPollId] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [result, setResult] = useState<{ url: string; provider?: string; model?: string; meta?: Record<string, unknown> } | null>(null);

  const gen = async () => {
    if (!prompt.trim()) return;
    setBusy(true);
    try {
      const body: Record<string, unknown> = { prompt, aspect_ratio: aspect,
        duration: parseFloat(String(duration)) || 5 };
      if (provider !== "auto") body.provider = provider;
      if (refAsset) body.reference_asset_id = refAsset;
      const res = await api<{ success: boolean; job?: GenJob }>(
        "/api/creative/generate/video", { method: "POST", body });
      if (res.success && res.job) {
        setJob(res.job); setPollId(res.job.id); setResult(null);
        toast("Video generation started");
      }
    } catch (err: unknown) {
      toast((err as Error).message || "Video generation failed", true);
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    if (!pollId) return;
    const iv = setInterval(async () => {
      try {
        const res = await api<{ success: boolean; job?: GenJob }>(
          "/api/creative/generation-jobs/" + pollId);
        const j = res.job;
        if (j) {
          setJob(j);
          if (["completed", "failed", "cancelled"].includes(j.status) && (!j.outputs || j.outputs.length === 0)) {
            setPollId(null);
          }
          if (j.status === "completed" && j.outputs && j.outputs.length) {
            setPollId(null);
            setResult({ url: mediaUrl(j.outputs[0].url), provider: j.provider, model: j.model, meta: j.outputs[0].meta });
            toast("Video generated");
          }
          if (j.status === "failed" || j.status === "cancelled") {
            setPollId(null);
            toast(j.message || "Video generation failed", true);
          }
          if (j.status === "failed" && j.message) {
            setErrorMessage(j.message);
          }
        }
      } catch (err: unknown) {
        setPollId(null); toast((err as Error).message || "Polling failed", true);
      }
    }, 1500);
    return () => clearInterval(iv);
  }, [pollId, toast]);

  const cancel = async () => {
    if (!job) return;
    try {
      await api<{ success: boolean }>(
        "/api/creative/generation-jobs/" + job.id + "/cancel", { method: "POST" });
    } catch (err: unknown) {
      toast((err as Error).message || "Cancel failed", true);
    }
    setPollId(null);
  };

  const isRunning = job && (job.status === "running" || job.status === "queued");

  return (
    <div style={{ maxWidth: 820, width: "100%", margin: "0 auto" }}>
      <h1 style={{ fontSize: 22, fontWeight: 700, marginBottom: 16 }}>AI Video Generator &amp; Storyboard Studio</h1>
      <div style={{ background: "var(--bg-2)", border: "1px solid var(--border)", borderRadius: 16,
        padding: 24, display: "flex", flexDirection: "column", gap: 16 }}>
        <div>
          <label style={{ fontSize: 12, fontWeight: 600, color: "var(--text-dim)" }}>Video Prompt / Script</label>
          <textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} rows={3}
            placeholder="Drone shot flying over a futuristic neon city at sunset..." style={{ width: "100%" }} />
        </div>
        <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
          <Sel label="Format" value={aspect} onChange={setAspect}
            options={[["16:9", "16:9 Widescreen"], ["9:16", "9:16 TikTok / Reels"]]} />
          <div style={{ flex: 1, minWidth: 110 }}>
            <label style={{ fontSize: 12, fontWeight: 600, color: "var(--text-dim)" }}>Duration (s)</label>
            <input type="number" min={1} max={60} value={duration}
              onChange={(e) => setDuration(parseInt(e.target.value, 10) || 5)} style={{ width: "100%" }} />
          </div>
          <Sel label="Provider" value={provider} onChange={setProvider}
            options={[["auto", "Auto (best available)"], ["openai", "OpenAI (Sora)"],
              ["google", "Google (Veo)"], ["local", "Local (Ken Burns)"]]} />
          <div style={{ flex: 1, minWidth: 140 }}>
            <label style={{ fontSize: 12, fontWeight: 600, color: "var(--text-dim)" }}>Reference asset id</label>
            <input type="text" value={refAsset} onChange={(e) => setRefAsset(e.target.value)}
              placeholder="image/video to drive motion" style={{ width: "100%" }} />
          </div>
        </div>
        <div style={{ display: "flex", gap: 10, alignItems: "flex-end" }}>
          <button onClick={gen} disabled={busy || !!job} className="btn primary"
            style={{ padding: "11px 20px" }}>{busy ? "Starting..." : "Generate Video Clip"}</button>
          {isRunning && <button className="btn danger small" onClick={cancel}>Cancel</button>}
        </div>

        {job && isRunning && (
          <div style={{ border: "1px solid var(--border)", borderRadius: 8, padding: 12, background: "var(--bg-3)" }}>
            <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>
              {job.provider} · {job.model} · {job.status}
            </div>
            <div style={{ height: 6, background: "var(--bg-3)", borderRadius: 3, overflow: "hidden" }}>
              <div style={{ width: `${num(job.progress_pct, 0)}%`, height: "100%",
                background: "linear-gradient(90,#087cff,#00df91)" }} />
            </div>
            <p style={{ fontSize: 11, color: "var(--text-dim)", marginTop: 4 }}>{job.message || "working…"}</p>
          </div>
        )}

        {result && (
          <div style={{ border: "1px solid var(--border)", borderRadius: 12, padding: 16, background: "var(--bg-3)" }}>
            <h3 style={{ fontSize: 14, fontWeight: 600, marginBottom: 10 }}>Generated Video Clip</h3>
            <div style={{ position: "relative", display: "inline-block" }}>
              <video src={result.url} controls style={{ width: "100%", maxHeight: 320, borderRadius: 8,
                border: "1px solid var(--border)", background: "#000" }} />
              {result.meta?.fallback ? (
                <span style={{ position: "absolute", top: 4, right: 4, fontSize: 10,
                  fontWeight: 700, textTransform: "uppercase", padding: "3px 8px", borderRadius: 4,
                  background: "rgba(255,184,0,0.85)", color: "#1a1a1a",
                  border: "1px solid rgba(255,184,0,1)" }}>⚠ Procedural Fallback</span>
              ) : null}
            </div>
            <p style={{ fontSize: 11, color: "var(--text-dim)", marginTop: 6 }}>
              Provider: {result.provider || "unknown"} · Model: {result.model || "n/a"}
            </p>
            {result.meta?.fallback ? (
              <p style={{ fontSize: 10, color: "#ffd43b", marginTop: 2, marginBottom: 0 }}>
                Fallback: {(result.meta.fallback as Record<string, unknown>)?.reason as string || "unknown"}
                {" from " + String((result.meta.fallback as Record<string, unknown>)?.from || "unknown")}
              </p>
            ) : null}
          </div>
        )}
        {errorMessage && (
          <p style={{ fontSize: 11, color: "#ff6b6b", marginTop: 8, marginBottom: 0 }}>
            ⚠ {errorMessage}
          </p>
        )}
      </div>
    </div>
  );
}

// ---- Multitrack Editor -------------------------------------------------------

function MediaBin({
  assets, onUpload, onPick, onThumb,
}: {
  assets: MediaAsset[];
  onUpload: (files: FileList) => void;
  onPick: (a: MediaAsset) => void;
  onThumb: (a: MediaAsset) => void;
}) {
  const [dragOver, setDragOver] = useState(false);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10, height: "100%", overflow: "hidden" }}>
      <h3 style={{ fontSize: 13, fontWeight: 700, textTransform: "uppercase",
        letterSpacing: 0.5, color: "var(--text-dim)" }}>Media Bin</h3>
      <div
        onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault(); setDragOver(false); onUpload(e.dataTransfer.files);
        }}
        style={{ border: "1.5px dashed " + (dragOver ? "var(--accent)" : "var(--border)"),
          borderRadius: 8, padding: 12, background: "var(--bg-3)", minHeight: 90,
          display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center",
          gap: 6, cursor: "pointer" }}
        onClick={() => {
          const input = document.createElement("input");
          input.type = "file"; input.multiple = true;
          input.accept = "image/*,video/*,audio/*";
          input.onchange = () => { if (input.files && input.files.length) onUpload(input.files); };
          input.click();
        }}
      >
        <span style={{ fontSize: 22 }}>📁</span>
        <span style={{ fontSize: 12, color: "var(--text-dim)" }}>
          {dragOver ? "Drop to upload" : "Click or drag files to bin"}
        </span>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 8, overflowY: "auto", flex: 1 }}>
        {assets.map((a) => (
          <AssetRow key={a.id} a={a} onPick={onPick} onThumb={onThumb} />
        ))}
        {assets.length === 0 && (
          <p style={{ fontSize: 12, color: "var(--text-dim)", padding: "10px 4px" }}>No media assets yet.</p>
        )}
      </div>
    </div>
  );
}

function AssetRow({ a, onPick, onThumb }: {
  a: MediaAsset; onPick: (a: MediaAsset) => void; onThumb: (a: MediaAsset) => void;
}) {
  const typeIcon = { image: "🖼", video: "🎬", audio: "🎵" }[a.file_type] || "📄";
  const thumb = a.thumbnail_url || (a.file_type === "image" ? a.url : undefined);
  return (
    <div
      draggable
      onDragStart={(e) => { e.dataTransfer.setData("text/plain", a.id); }}
      onClick={() => onPick(a)}
      onContextMenu={(e) => { e.preventDefault(); onThumb(a); }}
      style={{ padding: 8, borderRadius: 6, background: "var(--bg-2)",
        border: "1px solid var(--border)", display: "flex", gap: 8, alignItems: "center",
        cursor: "grab" }}>
      {thumb ? (
        <img src={mediaUrl(thumb)} alt="" style={{ width: 40, height: 40, objectFit: "cover",
          borderRadius: 4, border: "1px solid var(--border)" }} />
      ) : <span style={{ fontSize: 20 }}>{typeIcon}</span>}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 12, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
          {a.filename}
        </div>
        <div style={{ fontSize: 10, color: "var(--text-dim)" }}>
          {a.file_type} · {Math.round(a.size_bytes / 1024)} KB
          {a.duration ? " · " + fmtTime(a.duration) : ""}
        </div>
      </div>
    </div>
  );
}

function PreviewMonitor({
  src, title,
}: { src: string | null; title: string }) {
  return (
    <div style={{ background: "var(--bg-2)", border: "1px solid var(--border)", borderRadius: 12,
      padding: 16, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center" }}>
      <div style={{ width: "100%", height: 260, background: "#000", borderRadius: 8,
        display: "flex", alignItems: "center", justifyContent: "center",
        border: "1px solid var(--border)", position: "relative", overflow: "hidden" }}>
        {src ? (
          <video src={src} controls style={{ width: "100%", height: "100%", objectFit: "contain" }} />
        ) : (
          <>
            <span style={{ fontSize: 48 }}>🎬</span>
            <p style={{ fontSize: 13, color: "var(--text-dim)", marginTop: 8 }}>{title}</p>
          </>
        )}
      </div>
    </div>
  );
}

function TimelineRail({
  project, setProject, pxPerSecond, setZoom, selectedClipId, onSelectClip, onAssetDrop, toast,
}: {
  project: ProjectDetail;
  setProject: Dispatch<SetStateAction<ProjectDetail | null>>;
  pxPerSecond: number;
  setZoom: (n: number) => void;
  selectedClipId: string | null;
  onSelectClip: (c: TimelineClip | null) => void;
  onAssetDrop: (track: TimelineTrack, assetId: string) => void;
  toast: ToastFn;
}) {
  const dragRef = useRef<ClipDrag>(null);
  const projectRef = useRef<ProjectDetail | null>(project);
  projectRef.current = project;

  const moveClip = useCallback((clip: TimelineClip, delta: number) => {
    const start = Math.max(0, clip.start_time + delta / pxPerSecond);
    setProject((p) => {
      if (!p) return p;
      return { ...p, tracks: p.tracks.map((t) => ({ ...t, clips: t.clips.map((c) =>
        c.id === clip.id ? { ...c, start_time: start } : c) })) };
    });
  }, [pxPerSecond, setProject]);

  const trimClip = useCallback((clip: TimelineClip, delta: number) => {
    const w = Math.max(24, clip.duration * pxPerSecond + delta);
    const duration = Math.max(0.1, w / pxPerSecond);
    setProject((p) => {
      if (!p) return p;
      return { ...p, tracks: p.tracks.map((t) => ({ ...t, clips: t.clips.map((c) =>
        c.id === clip.id ? { ...c, duration } : c) })) };
    });
  }, [pxPerSecond, setProject]);

  useEffect(() => {
    const onMove = (e: MouseEvent) => {
      const d = dragRef.current;
      if (!d) return;
      const dx = e.clientX - d.startX;
      if (d.kind === "move") moveClip(d.clip, dx);
      else trimClip(d.clip, dx);
    };
    const onUp = async () => {
      const d = dragRef.current;
      if (!d) return;
      const clip = d.clip;
      const cur = projectRef.current;
      const found = cur?.tracks.flatMap((t) => t.clips).find((c) => c.id === clip.id);
      if (found) {
        try {
          await api<{ success: boolean }>("/api/creative/clips/" + found.id, {
            method: "PATCH", body: { start_time: found.start_time, duration: found.duration },
          });
        } catch (err: unknown) {
          toast((err as Error).message || "Save clip failed", true);
        }
      }
      dragRef.current = null;
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => { window.removeEventListener("mousemove", onMove); window.removeEventListener("mouseup", onUp); };
  }, [moveClip, trimClip, toast]);

  const totalDur = project.tracks.flatMap((t) => t.clips)
    .reduce((max, c) => Math.max(max, c.start_time + c.duration), 0);
  const endHeadroom = totalDur < 10 ? 10 : totalDur + 2;
  const railW = Math.max(560, endHeadroom * pxPerSecond);
  const ticks = Math.ceil(endHeadroom);

  const toggleTrack = async (track: TimelineTrack, field: "is_muted" | "is_solo" | "is_locked") => {
    try {
      await api<{ success: boolean }>("/api/creative/tracks/" + track.id,
        { method: "PATCH", body: { [field]: !track[field as "is_muted" | "is_solo" | "is_locked"] } });
      setProject((p) => {
        if (!p) return p;
        return { ...p, tracks: p.tracks.map((t) =>
          t.id === track.id ? { ...t, [field]: !t[field as "is_muted" | "is_solo" | "is_locked"] } : t) };
      });
    } catch (err: unknown) {
      toast((err as Error).message || "Track update failed", true);
    }
  };

  return (
    <div style={{ background: "var(--bg-2)", border: "1px solid var(--border)", borderRadius: 12,
      padding: 12, display: "flex", flexDirection: "column", gap: 6 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
        <h3 style={{ fontSize: 13, fontWeight: 700, textTransform: "uppercase",
          letterSpacing: 0.5, color: "var(--text-dim)" }}>Multitrack Timeline</h3>
        <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
          <button className="btn small" onClick={() => setZoom(Math.max(20, pxPerSecond - 5))}>－</button>
          <span style={{ fontSize: 11, color: "var(--text-dim)" }}>{Math.round(pxPerSecond)} px/s</span>
          <button className="btn small" onClick={() => setZoom(Math.min(140, pxPerSecond + 5))}>＋</button>
        </div>
      </div>

      <div style={{ overflowX: "auto", overflowY: "hidden" }}>
        <div style={{ width: railW, display: "flex", flexDirection: "column" }}>
          {/* Ruler */}
          <div style={{ height: 26, background: "var(--bg-3)", borderRadius: 6,
            position: "relative", borderBottom: "1px solid var(--border)", width: railW }}>
            {Array.from({ length: ticks + 1 }).map((_, i) => {
              const t = i;
              return (
                <div key={i} style={{ position: "absolute", left: t * pxPerSecond, top: 0,
                  height: "100%", borderLeft: "1px solid var(--border)", fontSize: 9,
                  color: "var(--text-dim)", paddingLeft: 2 }}>
                  {fmtTime(t)}
                </div>
              );
            })}
          </div>
          {project.tracks.map((track) => (
            <TrackRow
              key={track.id} track={track} pxPerSecond={pxPerSecond} railW={railW}
              selectedClipId={selectedClipId} onSelectClip={onSelectClip}
              dragRef={dragRef}
              onToggle={(f) => toggleTrack(track, f)}
              onAssetDrop={onAssetDrop}
            />
          ))}
        </div>
      </div>
    </div>
  );
}

function TrackRow({
  track, pxPerSecond, railW, selectedClipId, onSelectClip, dragRef, onToggle, onAssetDrop,
}: {
  track: TimelineTrack; pxPerSecond: number; railW: number;
  selectedClipId: string | null; onSelectClip: (c: TimelineClip | null) => void;
  dragRef: MutableRefObject<ClipDrag>;
  onToggle: (f: "is_muted" | "is_solo" | "is_locked") => void;
  onAssetDrop: (track: TimelineTrack, assetId: string) => void;
}) {
  const typeIcon: Record<string, string> = { video: "🔊", audio: "🎧", text: "📝", subtitle: "📝" };
  const bodyH = track.track_type === "audio" ? 40 : 30;
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12,
      height: track.track_type === "audio" ? 64 : 56, opacity: track.is_hidden ? 0.5 : 1 }}>
      <div style={{ width: 110, fontWeight: 600, color: "var(--text-dim)", whiteSpace: "nowrap",
        overflow: "hidden", textOverflow: "ellipsis", display: "flex", alignItems: "center", gap: 4 }}>
        {typeIcon[track.track_type] || "🔷"} {track.name}
      </div>
      <div style={{ display: "flex", gap: 4, alignItems: "center" }}>
        <button className={`btn small ${track.is_muted ? "primary" : ""}`}
          onClick={() => onToggle("is_muted")}>{track.is_muted ? "🔊" : "🔇"}</button>
        <button className={`btn small ${track.is_solo ? "primary" : ""}`}
          onClick={() => onToggle("is_solo")}>S</button>
        <button className={`btn small ${track.is_locked ? "primary" : ""}`}
          onClick={() => onToggle("is_locked")}>L</button>
      </div>
      <div
        style={{ flex: 1, height: bodyH, background: "var(--bg-3)", borderRadius: 6,
          position: "relative", minWidth: 0, border: "1px solid var(--border)" }}
        onDragOver={(e) => { e.preventDefault(); }}
        onDrop={(e) => {
          e.preventDefault();
          const id = e.dataTransfer.getData("text/plain");
          if (id) onAssetDrop(track, id);
        }}
      >
        {track.clips.map((clip) => (
          <ClipBlock key={clip.id} clip={clip} trackType={track.track_type}
            locked={track.is_locked} pxPerSecond={pxPerSecond} railW={railW}
            selected={clip.id === selectedClipId} onSelect={() => onSelectClip(clip)}
            dragRef={dragRef} />
        ))}
        {track.clips.length === 0 && (
          <span style={{ fontSize: 11, color: "var(--text-dim)", fontStyle: "italic",
            padding: "0 8px" }}>empty track — drag an asset here</span>
        )}
      </div>
    </div>
  );
}

function ClipBlock({
  clip, trackType, locked, pxPerSecond, selected, onSelect, dragRef,
}: {
  clip: TimelineClip; trackType: string; locked: boolean;
  pxPerSecond: number; railW: number;
  selected: boolean; onSelect: () => void;
  dragRef: MutableRefObject<ClipDrag>;
}) {
  const left = clip.start_time * pxPerSecond;
  const width = Math.max(28, clip.duration * pxPerSecond);
  const isAudio = trackType === "audio";
  const isText = trackType === "text" || trackType === "subtitle";
  const baseColor = isText ? "rgba(130,80,255,0.55)"
    : isAudio ? "rgba(0,223,145,0.5)" : "rgba(8,127,255,0.55)";

  return (
    <div
      style={{ position: "absolute", left, top: 2, width, height: "calc(100% - 4px)",
        background: selected ? "var(--accent)" : baseColor,
        border: "1px solid " + (selected ? "#258dff" : "var(--border)"),
        borderRadius: 4, display: "flex", alignItems: "center", padding: "0 6px",
        cursor: locked ? "not-allowed" : "grab", fontSize: 10, color: "#fff",
        overflow: "hidden" }}
      onClick={onSelect}
      onMouseDown={(e) => {
        e.stopPropagation();
        if (locked) return;
        dragRef.current = { kind: "move", clip, startX: e.clientX, start: clip.start_time, width };
      }}
    >
      <span style={{ whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", flex: 1 }}>
        {clip.name || (clip.clip_type === "text" ? "Text" : "Clip")}
      </span>
      <div
        onMouseDown={(e) => {
          e.stopPropagation();
          if (locked) return;
          dragRef.current = { kind: "trim", clip, startX: e.clientX, start: clip.start_time, width };
        }}
        style={{ position: "absolute", right: -4, top: 0, bottom: 0, width: 10,
          background: "rgba(255,255,255,0.25)", cursor: "col-resize", borderRadius: 2 }}
        title="Trim"
      />
    </div>
  );
}

function Inspector({
  clip, onUpdate, project, toast, filters,
}: {
  clip: TimelineClip | null;
  onUpdate: (patch: Record<string, unknown>) => void;
  project: ProjectDetail;
  toast: ToastFn;
  filters: string[];
}) {
  if (!clip) {
    return (
      <div style={{ background: "var(--bg-2)", border: "1px solid var(--border)", borderRadius: 12,
        padding: 16 }}>
        <h3 style={{ fontSize: 13, fontWeight: 700, textTransform: "uppercase",
          letterSpacing: 0.5, color: "var(--text-dim)" }}>Inspector</h3>
        <p style={{ fontSize: 12, color: "var(--text-dim)", marginTop: 8 }}>
          Select a clip to edit its properties.
        </p>
      </div>
    );
  }

  const isText = clip.clip_type === "text";
  const isAudio = clip.clip_type === "audio";
  const isMedia = clip.clip_type === "video" || clip.clip_type === "image";
  const tr = (clip.transform || {}) as Record<string, number>;
  const fx = (clip.effects || {}) as Record<string, unknown>;
  const tx = (clip.text || {}) as Record<string, unknown>;
  const texts = (tx.texts as Array<Record<string, unknown>>) || [];
  const t0 = texts[0] || { text: "", x: 0, y: 0, size: 48, color: "#ffffff" };

  const setTransform = (k: string, v: number) => onUpdate({ transform_json: { ...tr, [k]: v } });
  const setEffect = (k: string, v: unknown) => onUpdate({ effects_json: { ...fx, [k]: v } });
  const setTxt = (k: string, v: unknown) =>
    onUpdate({ text_json: { ...tx, texts: [{ ...t0, [k]: v }] } });

  return (
    <div style={{ background: "var(--bg-2)", border: "1px solid var(--border)", borderRadius: 12,
      padding: 16 }}>
      <h3 style={{ fontSize: 13, fontWeight: 700, textTransform: "uppercase",
        letterSpacing: 0.5, color: "var(--text-dim)" }}>Inspector — {clip.name || "Clip"}</h3>
      <div style={{ display: "flex", flexDirection: "column", gap: 12, marginTop: 12, fontSize: 12 }}>

        <div>
          <label style={{ color: "var(--text-dim)" }}>Name</label>
          <input type="text" value={clip.name ?? ""} onChange={(e) => onUpdate({ name: e.target.value })}
            style={{ width: "100%", marginTop: 4 }} />
        </div>

        <div>
          <label style={{ color: "var(--text-dim)" }}>Duration (s)</label>
          <input type="number" step={0.1} value={clip.duration}
            onChange={(e) => onUpdate({ duration: parseFloat(e.target.value) || clip.duration })}
            style={{ width: "100%", marginTop: 4 }} />
        </div>

        <div>
          <label style={{ color: "var(--text-dim)" }}>Opacity ({Math.round(clip.opacity * 100)}%)</label>
          <input type="range" min={0} max={1} step={0.01} value={clip.opacity}
            onChange={(e) => onUpdate({ opacity: parseFloat(e.target.value) })} style={{ width: "100%" }} />
        </div>

        <div>
          <label style={{ color: "var(--text-dim)" }}>Speed ({clip.speed.toFixed(2)}x)</label>
          <input type="range" min={0.25} max={4} step={0.05} value={clip.speed}
            onChange={(e) => onUpdate({ speed: parseFloat(e.target.value) })} style={{ width: "100%" }} />
        </div>

        <div>
          <label style={{ color: "var(--text-dim)" }}>Transform</label>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6, marginTop: 4 }}>
            <Num label="X" v={num(tr.x)} onChange={(v) => setTransform("x", v)} />
            <Num label="Y" v={num(tr.y)} onChange={(v) => setTransform("y", v)} />
            <Num label="Scale" v={num(tr.scale, 1)} onChange={(v) => setTransform("scale", v)} />
            <Num label="Rotation" v={num(tr.rotation)} onChange={(v) => setTransform("rotation", v)} />
          </div>
        </div>

        {isMedia && (
          <div>
            <label style={{ color: "var(--text-dim)" }}>Filter / LUT</label>
            <select style={{ width: "100%", marginTop: 4 }}
              value={(fx.filter as string) || ""}
              onChange={(e) => setEffect("filter", e.target.value || null)}>
              <option value="">None</option>
              {filters.map((f) => <option key={f} value={f}>{f}</option>)}
            </select>
          </div>
        )}

        {isMedia && (
          <div>
            <label style={{ color: "var(--text-dim)" }}>Adjustments (render-time)</label>
            <AdjustInline fx={fx} setEffect={setEffect} />
          </div>
        )}

        {isText && (
          <div>
            <label style={{ color: "var(--text-dim)" }}>Caption Text</label>
            <input type="text" value={String((t0.text as string) || "")}
              onChange={(e) => setTxt("text", e.target.value)} style={{ width: "100%", marginTop: 4 }} />
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6, marginTop: 6 }}>
              <Num label="Size" v={num(t0.size as number, 48)} onChange={(v) => setTxt("size", v)} />
              <div style={{ display: "flex", gap: 6, alignItems: "flex-end" }}>
                <input type="color" value={(t0.color as string) || "#ffffff"}
                  onChange={(e) => setTxt("color", e.target.value)} />
                <label style={{ fontSize: 10, color: "var(--text-dim)" }}>Color</label>
              </div>
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6, marginTop: 6 }}>
              <Num label="X" v={num(t0.x)} onChange={(v) => setTxt("x", v)} />
              <Num label="Y" v={num(t0.y)} onChange={(v) => setTxt("y", v)} />
            </div>
          </div>
        )}

        {isAudio && (
          <AudioOps assetId={clip.asset_id} project={project} onUpdate={onUpdate} toast={toast} />
        )}

        {clip.asset_id && clip.clip_type === "image" && (
          <button className="btn small" style={{ marginTop: 4 }}
            onClick={async () => {
              try {
                const res = await api<{ success: boolean; asset?: MediaAsset }>(
                  "/api/creative/image/ops", {
                    method: "POST",
                    body: { asset_id: clip.asset_id, ops: [{ op: "background_remove", mode: "edge" }] },
                  });
                if (res.success && res.asset) { onUpdate({ asset_id: res.asset.id }); toast("Background removed"); }
              } catch (err: unknown) { toast((err as Error).message || "op failed", true); }
            }}>Remove Background</button>
        )}
      </div>
    </div>
  );
}

function Num({ label, v, onChange }: { label: string; v: number; onChange: (n: number) => void }) {
  return (
    <div style={{ display: "flex", flexDirection: "column" }}>
      <label style={{ fontSize: 10, color: "var(--text-dim)" }}>{label}</label>
      <input type="number" step={0.1} value={isNaN(v) ? "" : String(v)}
        onChange={(e) => onChange(parseFloat(e.target.value) || 0)}
        style={{ width: "100%", padding: "4px 6px" }} />
    </div>
  );
}

function AdjustInline({ fx, setEffect }: {
  fx: Record<string, unknown>; setEffect: (k: string, v: unknown) => void;
}) {
  const adj = (fx.adjust as Record<string, number> | undefined) || {};
  const set = (k: string, v: number) => setEffect("adjust", { ...adj, [k]: v });
  return (
    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6, marginTop: 4 }}>
      <Num label="Brightness" v={num(adj.brightness)} onChange={(v) => set("brightness", v)} />
      <Num label="Contrast" v={num(adj.contrast)} onChange={(v) => set("contrast", v)} />
      <Num label="Saturation" v={num(adj.saturation)} onChange={(v) => set("saturation", v)} />
    </div>
  );
}

function AudioOps({
  assetId, project, onUpdate, toast,
}: {
  assetId?: string | null; project: ProjectDetail;
  onUpdate: (patch: Record<string, unknown>) => void; toast: ToastFn;
}) {
  const [db, setDb] = useState(-6);
  const apply = async (op: string, params: Record<string, unknown> = {}) => {
    if (!assetId) return;
    try {
      const res = await api<{ success: boolean; asset?: MediaAsset }>(
        "/api/creative/audio/ops", {
          method: "POST",
          body: { asset_id: assetId, project_id: project.id, ops: [{ op, ...params }] },
        });
      if (res.success && res.asset) { onUpdate({ asset_id: res.asset.id }); toast("Audio processed"); }
    } catch (err: unknown) {
      toast((err as Error).message || "Audio op failed", true);
    }
  };
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <div style={{ display: "flex", gap: 6, alignItems: "flex-end" }}>
        <input type="range" min={-24} max={12} step={1} value={db}
          onChange={(e) => setDb(parseInt(e.target.value, 10))} style={{ flex: 1 }} />
        <span style={{ fontSize: 11, width: 70 }}>{db} dB</span>
      </div>
      <div style={{ display: "flex", gap: 6 }}>
        <button className="btn small" onClick={() => apply("volume", { db })}>Apply Gain</button>
        <button className="btn small" onClick={() => apply("normalize", { target_lufs: -16 })}>Normalize LUFS</button>
        <button className="btn small" onClick={() => apply("denoise", { strength: 0.6 })}>Denoise</button>
      </div>
    </div>
  );
}

// ---- Editor workspace --------------------------------------------------------

function EditorView({
  project, setProject, toast, pxPerSecond, setZoom,
}: {
  project: ProjectDetail;
  setProject: Dispatch<SetStateAction<ProjectDetail | null>>;
  toast: ToastFn;
  pxPerSecond: number;
  setZoom: (n: number) => void;
}) {
  const [selectedClip, setSelectedClip] = useState<TimelineClip | null>(null);
  const [selectedAsset, setSelectedAsset] = useState<MediaAsset | null>(null);
  const [filters, setFilters] = useState<string[]>([]);
  const [presets, setPresets] = useState<EffectPreset[]>([]);
  const [brands, setBrands] = useState<BrandKit[]>([]);
  const [exportOpts, setExportOpts] = useState<Record<string, Record<string, unknown>> | null>(null);
  const [showShare, setShowShare] = useState(false);
  const [showVersion, setShowVersion] = useState(false);
  const [showTranscribe, setShowTranscribe] = useState(false);
  const [showAiEdit, setShowAiEdit] = useState(false);
  const [versionLabel, setVersionLabel] = useState("");
  const [renderPreset, setRenderPreset] = useState("youtube_1080p");
  const [renderJob, setRenderJob] = useState<RenderJob | null>(null);
  const [renderPollId, setRenderPollId] = useState<string | null>(null);

  const [localProject, setLocalProject] = useState<ProjectDetail | null>(project);
  useEffect(() => { setLocalProject(project); }, [project]);

  useEffect(() => {
    (async () => {
      try {
        const f = await api<{ success: boolean; filters?: string[] }>("/api/creative/image/filters");
        if (f.success && f.filters) setFilters(f.filters);
      } catch { /* optional */ }
      try {
        const e = await api<{ success: boolean; presets?: EffectPreset[] }>("/api/creative/effect-presets");
        if (e.success && e.presets) setPresets(e.presets);
      } catch { /* optional */ }
      try {
        const b = await api<{ success: boolean; brand_kits?: BrandKit[] }>("/api/creative/brand-kits");
        if (b.success && b.brand_kits) setBrands(b.brand_kits);
      } catch { /* optional */ }
      try {
        const r = await api<{ success: boolean; presets?: Record<string, Record<string, unknown>> }>(
          "/api/creative/export-presets");
        if (r.success && r.presets) setExportOpts(r.presets);
      } catch { /* optional */ }
    })();
  }, []);

  const resolvePresetOpts = (key: string): Record<string, unknown> => {
    const social = exportOpts?.social as Record<string, Record<string, unknown>> | undefined;
    return (social && social[key]) || { resolution: "1080p", fps: 30, aspect_ratio: "16:9", kind: "video" };
  };

  const updateClip = useCallback(async (patch: Record<string, unknown>) => {
    if (!selectedClip || !localProject) return;
    const clipId = selectedClip.id;
    const prevLocal = localProject;
    setLocalProject((p) => {
      if (!p) return p;
      return { ...p, tracks: p.tracks.map((tr) => ({ ...tr, clips: tr.clips.map((c) => {
        if (c.id !== clipId) return c;
        const next: TimelineClip = { ...c };
        if (patch.name !== undefined) next.name = patch.name as string;
        if (patch.duration !== undefined) next.duration = patch.duration as number;
        if (patch.opacity !== undefined) next.opacity = patch.opacity as number;
        if (patch.speed !== undefined) next.speed = patch.speed as number;
        if (patch.asset_id !== undefined) next.asset_id = patch.asset_id as string | null;
        if (patch.transform_json) next.transform = { ...c.transform, ...(patch.transform_json as Record<string, number>) };
        if (patch.effects_json) next.effects = { ...c.effects, ...(patch.effects_json as Record<string, unknown>) };
        if (patch.text_json) next.text = { ...c.text, ...(patch.text_json as Record<string, unknown>) };
        return next;
      }) })) };
    });
    try {
      await api<{ success: boolean }>("/api/creative/clips/" + clipId, { method: "PATCH", body: patch });
    } catch (err: unknown) {
      setLocalProject(prevLocal); toast((err as Error).message || "Update failed", true);
    }
  }, [localProject, selectedClip, toast]);

  const uploadAssets = useCallback(async (files: FileList) => {
    if (!localProject) return;
    for (const f of Array.from(files)) {
      const fd = new FormData(); fd.append("file", f);
      try {
        const res = await api<{ success: boolean; asset?: MediaAsset }>(
          "/api/creative/projects/" + localProject.id + "/assets",
          { method: "POST", formData: fd });
        if (res.success && res.asset) {
          setLocalProject((p) => { if (!p) return p; return { ...p, assets: [res.asset!, ...p.assets] }; });
          toast("Uploaded " + res.asset.filename);
        }
      } catch (err: unknown) {
        toast((err as Error).message || "Upload failed", true); return;
      }
    }
  }, [localProject, toast]);

  const placeClip = useCallback(async (track: TimelineTrack, asset: MediaAsset) => {
    if (!localProject) return;
    try {
      const res = await api<{ success: boolean; clip?: TimelineClip }>(
        "/api/creative/tracks/" + track.id + "/clips", {
        method: "POST", body: {
          asset_id: asset.id, clip_type: asset.file_type, duration: assetDuration(asset),
        },
      });
      if (res.success && res.clip) {
        setLocalProject((p) => {
          if (!p) return p;
          return { ...p, tracks: p.tracks.map((t) =>
            t.id === track.id ? { ...t, clips: [...t.clips, res.clip!] } : t) };
        });
        toast("Clip added to " + track.name);
      }
    } catch (err: unknown) {
      toast((err as Error).message || "Place clip failed", true);
    }
  }, [localProject, toast]);

  const handleAssetDrop = useCallback((track: TimelineTrack, assetId: string) => {
    if (!localProject) return;
    const a = localProject.assets.find((x) => x.id === assetId);
    if (a) placeClip(track, a);
  }, [localProject, placeClip]);

  const addTrack = useCallback(async () => {
    if (!localProject) return;
    try {
      const res = await api<{ success: boolean; track?: TimelineTrack }>(
        "/api/creative/projects/" + localProject.id + "/tracks", {
        method: "POST", body: { track_type: "video", name: "Video " + (localProject.tracks.length + 1) },
      });
      if (res.success && res.track) {
        setLocalProject((p) => { if (!p) return p; return { ...p, tracks: [...p.tracks, res.track!] }; });
        toast("Track added");
      }
    } catch (err: unknown) {
      toast((err as Error).message || "Add track failed", true);
    }
  }, [localProject, toast]);

  const saveTimeline = useCallback(async () => {
    if (!localProject) return;
    try {
      const res = await api<{ success: boolean; project?: ProjectDetail }>(
        "/api/creative/projects/" + localProject.id + "/timeline", {
        method: "PUT", body: { tracks: localProject.tracks, clips: [] },
      });
      if (res.success && res.project) setProject(res.project);
      toast("Timeline saved");
    } catch (err: unknown) {
      toast((err as Error).message || "Save failed", true);
    }
  }, [localProject, setProject, toast]);

  const snapshot = useCallback(async () => {
    if (!localProject) return;
    try {
      await api<{ success: boolean }>("/api/creative/projects/" + localProject.id + "/versions",
        { method: "POST", body: { label: versionLabel || "Snapshot" } });
      toast("Version saved"); setVersionLabel("");
    } catch (err: unknown) {
      toast((err as Error).message || "Snapshot failed", true);
    }
  }, [localProject, versionLabel, toast]);

  const render = useCallback(async (presetKey: string) => {
    if (!localProject) return;
    try {
      const res = await api<{ success: boolean; render_job?: RenderJob }>(
        "/api/creative/render", {
        method: "POST",
        body: { project_id: localProject.id, kind: "video", options: resolvePresetOpts(presetKey) },
      });
      if (res.success && res.render_job) { setRenderJob(res.render_job); setRenderPollId(res.render_job.id); }
    } catch (err: unknown) {
      toast((err as Error).message || "Render failed", true);
    }
  }, [localProject, toast]);

  useEffect(() => {
    if (!renderPollId) return;
    const iv = setInterval(async () => {
      try {
        const res = await api<{ success: boolean; render_job?: RenderJob }>(
          "/api/creative/render-jobs/" + renderPollId);
        if (res.render_job) {
          setRenderJob(res.render_job);
          if (["completed", "failed", "cancelled"].includes(res.render_job.status)) setRenderPollId(null);
        }
      } catch (err: unknown) {
        setRenderPollId(null); toast((err as Error).message || "Render poll failed", true);
      }
    }, 1500);
    return () => clearInterval(iv);
  }, [renderPollId, toast]);

  const cancelRender = useCallback(async () => {
    if (!renderJob) return;
    try {
      await api<{ success: boolean }>(
        "/api/creative/render-jobs/" + renderJob.id + "/cancel", { method: "POST" });
    } catch (err: unknown) {
      toast((err as Error).message || "Cancel failed", true);
    }
  }, [renderJob, toast]);

  const handleThumb = useCallback((a: MediaAsset) => {
    api<{ success: boolean; thumbnail_url?: string }>(
      "/api/creative/assets/" + a.id + "/thumbnail", { method: "POST" })
      .then((r) => {
        if (r.success && r.thumbnail_url) {
          setLocalProject((p) => {
            if (!p) return p;
            return { ...p, assets: p.assets.map((x) =>
              x.id === a.id ? { ...x, thumbnail_url: r.thumbnail_url } : x) };
          });
        }
      }).catch(() => { /* ignore */ });
  }, [localProject]);

  const handleSelectClip = useCallback((c: TimelineClip | null) => {
    setSelectedClip(c);
    if (c?.asset_id && localProject) {
      const a = localProject.assets.find((x) => x.id === c.asset_id);
      setSelectedAsset(a ?? null);
    } else {
      setSelectedAsset(null);
    }
  }, [localProject]);

  const previewSrc = selectedAsset ? mediaUrl(selectedAsset.url)
    : (renderJob?.output_url ? mediaUrl(renderJob.output_url) : null);

  const dispProject = localProject;
  if (!dispProject) return null;

  return (
    <div style={{ display: "flex", flexDirection: "column", flex: 1, gap: 12 }}>
      {/* Workspace header / action bar */}
      <div style={{ display: "flex", flexWrap: "wrap", gap: 10, alignItems: "center",
        background: "var(--bg-2)", padding: "12px 16px", borderRadius: 12, border: "1px solid var(--border)" }}>
        <div style={{ flex: 1, minWidth: 200 }}>
          <h2 style={{ fontSize: 16, fontWeight: 700 }}>{dispProject.title}</h2>
          <p style={{ fontSize: 12, color: "var(--text-dim)" }}>
            Type: {dispProject.project_type} · Ratio: {dispProject.aspect_ratio}
          </p>
        </div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center" }}>
          <button className="btn small" onClick={addTrack}>＋ Track</button>
          <button className="btn small" onClick={saveTimeline}>Save Timeline</button>
          <button className="btn small" onClick={() => setShowVersion(true)}>Versions</button>
          <button className="btn small" onClick={() => setShowShare(true)}>Share</button>
          <button className="btn small" onClick={() => setShowTranscribe(true)}>Transcribe</button>
          <button className="btn small" onClick={() => setShowAiEdit(true)}>AI Auto-Edit</button>
          {brands.length > 0 && (
            <span style={{ fontSize: 11, color: "var(--cyan)" }}>
              {brands.length} brand kit{brands.length > 1 ? "s" : ""} · {presets.length} effect preset
              {presets.length === 1 ? "" : "s"}
            </span>
          )}
          <div style={{ display: "flex", alignItems: "center", gap: 4, background: "var(--bg-3)",
            borderRadius: 6, padding: "2px 6px" }}>
            <button className="btn small" onClick={() => setZoom(Math.max(20, pxPerSecond - 5))}>－</button>
            <span style={{ fontSize: 11, color: "var(--text-dim)" }}>{Math.round(pxPerSecond)}px/s</span>
            <button className="btn small" onClick={() => setZoom(Math.min(140, pxPerSecond + 5))}>＋</button>
          </div>
          <select value={renderPreset} onChange={(e) => setRenderPreset(e.target.value)} className="btn small">
            {(Object.keys((exportOpts?.social) || { youtube_1080p: {} }))
              .map((p) => <option key={p} value={p}>{friendlyPreset(p)}</option>)}
          </select>
          <button className="btn primary small" onClick={() => render(renderPreset)} style={{
            background: "linear-gradient(135deg,#00df91,#00b070)", borderColor: "#00df91" }}>
            Render
          </button>
        </div>
      </div>

      {/* 3-panel professional layout: bin | viewer+timeline | inspector */}
      <div style={{ display: "grid", gridTemplateColumns: "240px 1fr 320px", gap: 12, flex: 1, minHeight: 0 }}>
        <div style={{ background: "var(--bg-2)", border: "1px solid var(--border)", borderRadius: 12,
          padding: 12, display: "flex", flexDirection: "column", overflow: "hidden" }}>
          <MediaBin assets={dispProject.assets} onUpload={uploadAssets}
            onPick={(a) => { setSelectedAsset(a); setSelectedClip(null); }}
            onThumb={handleThumb} />
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 10, flex: 1, minHeight: 0 }}>
          <PreviewMonitor src={previewSrc} title={selectedAsset?.filename || "Source / Program Monitor"} />
          <TimelineRail project={dispProject} setProject={setLocalProject} pxPerSecond={pxPerSecond}
            setZoom={setZoom} selectedClipId={selectedClip?.id ?? null}
            onSelectClip={handleSelectClip} onAssetDrop={handleAssetDrop} toast={toast} />
        </div>

        <div style={{ overflowY: "auto" }}>
          <Inspector clip={selectedClip} onUpdate={updateClip} project={dispProject}
            toast={toast} filters={filters} />
        </div>
      </div>

      {renderJob && (
        <RenderStatus job={renderJob} onCancel={cancelRender} toast={toast} />
      )}

      {showTranscribe && dispProject && (
        <TranscribePanel project={dispProject} toast={toast} />
      )}

      {showShare && dispProject && (
        <ShareModal project={dispProject} onClose={() => setShowShare(false)} toast={toast} />
      )}

      {showVersion && dispProject && (
        <VersionModal project={dispProject} label={versionLabel} setLabel={setVersionLabel}
          onSnapshot={snapshot} onClose={() => setShowVersion(false)} toast={toast} />
      )}

      {showAiEdit && dispProject && (
        <AiEditModal project={dispProject} onClose={() => setShowAiEdit(false)}
          onApplied={(a) => setLocalProject((p) => p ? { ...p, assets: [a, ...p.assets] } : p)}
          toast={toast} />
      )}
    </div>
  );
}

// ---- Render status -----------------------------------------------------------

function RenderStatus({
  job, onCancel, toast,
}: { job: RenderJob; onCancel: () => void; toast: ToastFn; }) {
  void toast;
  return (
    <div style={{ border: "1px solid var(--border)", borderRadius: 8, padding: 10, background: "var(--bg-3)" }}>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12, fontWeight: 600 }}>
        <span>Export — {job.status}</span>
        {job.status === "running" || job.status === "queued" || job.status === "processing" ? (
          <button className="btn small danger" onClick={onCancel}>Cancel</button>
        ) : null}
      </div>
      <div style={{ height: 6, background: "var(--bg-3)", borderRadius: 3, overflow: "hidden", marginTop: 6 }}>
        <div style={{ width: `${num(job.progress_pct, 0)}%`, height: "100%",
          background: "linear-gradient(90,#087cff,#00df91)" }} />
      </div>
      {job.message && <p style={{ fontSize: 11, color: "var(--text-dim)", marginTop: 4 }}>{job.message}</p>}
      {job.status === "completed" && job.output_url && (
        <div style={{ marginTop: 6, fontSize: 12 }}>
          <a href={mediaUrl(job.output_url)} target="_blank" rel="noopener noreferrer"
            style={{ color: "var(--cyan)" }}>⬇ Open / download exported video</a>
        </div>
      )}
      {job.status === "failed" && (
        <p style={{ fontSize: 11, color: "var(--danger)", marginTop: 4 }}>
          {job.error_message || job.message || "Render failed"}
        </p>
      )}
    </div>
  );
}

// ---- Transcription -----------------------------------------------------------

function TranscribePanel({
  project, toast,
}: { project: ProjectDetail; toast: ToastFn; }) {
  const [assetId, setAssetId] = useState("");
  const [busy, setBusy] = useState(false);
  const [segments, setSegments] = useState<TranscriptSegment[]>(project.transcripts || []);
  const [fmt, setFmt] = useState("srt");

  const load = useCallback(async () => {
    try {
      const res = await api<{ success: boolean; transcripts?: TranscriptSegment[] }>(
        "/api/creative/projects/" + project.id + "/transcripts");
      if (res.success) setSegments(res.transcripts || []);
    } catch (err: unknown) {
      toast((err as Error).message || "Failed to load transcripts", true);
    }
  }, [project.id, toast]);
  useEffect(() => { load(); }, [load]);

  const run = async () => {
    if (!assetId) return;
    setBusy(true);
    try {
      const res = await api<{ success: boolean; saved?: number; language?: string }>(
        "/api/creative/transcribe", {
        method: "POST",
        body: { asset_id: assetId, project_id: project.id, save_to_project: true },
      });
      if (res.success) {
        toast(`Transcribed (${res.saved} segments) · ${res.language || "auto"}`);
        load();
      }
    } catch (err: unknown) {
      toast((err as Error).message || "Transcribe failed", true);
    } finally {
      setBusy(false);
    }
  };

  const exportSub = async () => {
    try {
      const res = await api<{ success: boolean; content?: string }>(
        "/api/creative/projects/" + project.id + "/subtitles?fmt=" + fmt);
      if (res.content !== undefined) {
        const blob = new Blob([res.content],
          { type: fmt === "vtt" ? "text/vtt" : "application/x-subrip" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a"); a.href = url; a.download = "captions." + fmt;
        a.click(); URL.revokeObjectURL(url);
      }
    } catch (err: unknown) {
      toast((err as Error).message || "Export failed", true);
    }
  };

  return (
    <div style={{ background: "var(--bg-2)", border: "1px solid var(--border)", borderRadius: 12,
      padding: 14, display: "flex", flexDirection: "column", gap: 10 }}>
      <h3 style={{ fontSize: 13, fontWeight: 700, margin: 0 }}>Transcription & Captions</h3>
      <div style={{ display: "flex", gap: 8, alignItems: "flex-end" }}>
        <select value={assetId} onChange={(e) => setAssetId(e.target.value)}
          style={{ flex: 1, minWidth: 160 }} className="btn small">
          <option value="">Select an audio/video asset…</option>
          {project.assets.filter((a) => a.file_type === "audio" || a.file_type === "video").map((a) =>
            <option key={a.id} value={a.id}>{a.filename}</option>)}
        </select>
        <button className="btn primary small" onClick={run} disabled={busy || !assetId}>
          {busy ? "Transcribing…" : "Transcribe"}
        </button>
        <select value={fmt} onChange={(e) => setFmt(e.target.value)} className="btn small">
          <option value="srt">SRT</option><option value="vtt">VTT</option>
        </select>
        <button className="btn small" onClick={exportSub} disabled={!segments.length}>Export</button>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 6, maxHeight: 170, overflowY: "auto" }}>
        {segments.map((s) => (
          <div key={s.id || s.start_time}
            style={{ fontSize: 11, padding: "4px 8px", borderRadius: 4,
              background: s.is_filler ? "rgba(210,151,34,0.15)" : "var(--bg-3)" }}>
            <span style={{ color: "var(--cyan)" }}>{fmtTime(s.start_time)}–{fmtTime(s.end_time)}</span>
            {" "} {s.text}
            {s.is_filler && <span style={{ color: "#ffd43b" }}> (filler)</span>}
          </div>
        ))}
        {segments.length === 0 && <p style={{ fontSize: 11, color: "var(--text-dim)" }}>No transcripts yet.</p>}
      </div>
    </div>
  );
}

// ---- AI Auto-Edit ------------------------------------------------------------

function AiEditModal({
  project, onClose, onApplied, toast,
}: {
  project: ProjectDetail;
  onClose: () => void;
  onApplied: (a: MediaAsset) => void;
  toast: ToastFn;
}) {
  const [assetId, setAssetId] = useState("");
  const [instr, setInstr] = useState("");
  const [exec, setExec] = useState(false);
  const [provider, setProvider] = useState("auto");
  const [model, setModel] = useState("");
  const [w, setW] = useState(1280);
  const [h, setH] = useState(720);
  const [busy, setBusy] = useState(false);
  const [plan, setPlan] = useState<AiPlan | null>(null);

  const submit = async () => {
    if (!instr.trim() || !assetId) { toast("Instruction + asset required", true); return; }
    setBusy(true);
    try {
      const body: Record<string, unknown> = {
        instruction: instr, asset_id: assetId, project_id: project.id,
        execute: exec, save_to_project: true, width: w, height: h,
      };
      if (provider !== "auto") body.provider = provider;
      if (model) body.model = model;
      const res = await api<{ success: boolean; plan?: AiPlan; applied?: unknown[];
        skipped?: unknown[]; asset?: MediaAsset }>(
        "/api/creative/ai-edit", { method: "POST", body });
      if (res.success) {
        setPlan(res.plan ?? null);
        if (res.asset) onApplied(res.asset);
        toast(exec ? "AI edit applied" : "Auto-edit plan ready");
      }
    } catch (err: unknown) {
      toast((err as Error).message || "AI edit failed", true);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.85)", zIndex: 1000,
      display: "flex", alignItems: "center", justifyContent: "center", padding: 16 }}>
      <div style={{ background: "var(--bg-2)", border: "1px solid var(--border)", borderRadius: 16,
        maxWidth: 520, width: "100%", padding: 22, maxHeight: "85vh", overflowY: "auto" }}>
        <h2 style={{ fontSize: 18, fontWeight: 700, marginBottom: 14 }}>AI Auto-Edit</h2>
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <div>
            <label style={{ fontSize: 12, fontWeight: 600, color: "var(--text-dim)" }}>Asset</label>
            <select value={assetId} onChange={(e) => setAssetId(e.target.value)} style={{ width: "100%" }}>
              <option value="">Choose an asset…</option>
              {project.assets.map((a) => <option key={a.id} value={a.id}>{a.filename}</option>)}
            </select>
          </div>
          <div>
            <label style={{ fontSize: 12, fontWeight: 600, color: "var(--text-dim)" }}>Instruction</label>
            <textarea value={instr} onChange={(e) => setInstr(e.target.value)} rows={3}
              placeholder="Make it cinematic with a warm color grade and add a subtle film grain."
              style={{ width: "100%" }} />
          </div>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <Sel label="Provider" value={provider} onChange={setProvider}
              options={[["auto", "Auto"], ["anthropic", "Anthropic"], ["openai", "OpenAI"]]} />
            <div style={{ flex: 1, minWidth: 110 }}>
              <label style={{ fontSize: 12, fontWeight: 600, color: "var(--text-dim)" }}>Model</label>
              <input type="text" value={model} onChange={(e) => setModel(e.target.value)}
                placeholder="e.g. gpt-image-edit" style={{ width: "100%" }} />
            </div>
            <Num label="Width" v={w} onChange={setW} />
            <Num label="Height" v={h} onChange={setH} />
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <input type="checkbox" id="aiedit-exec" checked={exec}
              onChange={(e) => setExec(e.target.checked)} />
            <label htmlFor="aiedit-exec" style={{ fontSize: 12 }}>Execute plan (renders a new asset)</label>
          </div>
        </div>

        {plan && (
          <div style={{ marginTop: 14, border: "1px solid var(--border)", borderRadius: 8,
            padding: 10, background: "var(--bg-3)" }}>
            <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>AI Plan</div>
            {plan.explanation && (
              <p style={{ fontSize: 11, color: "var(--text-dim)" }}>{plan.explanation}</p>
            )}
            <div style={{ display: "flex", flexDirection: "column", gap: 4, marginTop: 6 }}>
              {plan.ops.map((op, i) => (
                <div key={i} style={{ fontSize: 11, fontFamily: "monospace" }}>
                  {op.op}{op.name ? " · " + String(op.name) : ""}
                </div>
              ))}
            </div>
            {plan.errors && plan.errors.length > 0 && (
              <p style={{ fontSize: 11, color: "var(--danger)", marginTop: 4 }}>{plan.errors.join("; ")}</p>
            )}
          </div>
        )}

        <div style={{ display: "flex", justifyContent: "flex-end", gap: 10, marginTop: 18 }}>
          <button className="btn" onClick={onClose}>Close</button>
          <button className="btn primary" disabled={busy} onClick={submit}>
            {busy ? "Working…" : (exec ? "Plan &amp; Execute" : "Plan")}
          </button>
        </div>
      </div>
    </div>
  );
}

// ---- Share / Version modals --------------------------------------------------

function ShareModal({ project, onClose, toast }: {
  project: ProjectDetail; onClose: () => void; toast: ToastFn;
}) {
  const [shares, setShares] = useState<Share[]>([]);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("viewer");
  const [days, setDays] = useState("");

  const load = useCallback(async () => {
    try {
      const res = await api<{ success: boolean; shares?: Share[] }>(
        "/api/creative/projects/" + project.id + "/shares");
      if (res.success) setShares(res.shares || []);
    } catch (err: unknown) {
      toast((err as Error).message || "Failed to load shares", true);
    }
  }, [project.id, toast]);
  useEffect(() => { load(); }, [load]);

  const create = async () => {
    try {
      const res = await api<{ success: boolean; share?: Share }>(
        "/api/creative/projects/" + project.id + "/shares", {
        method: "POST", body: { shared_with_email: email || undefined, role,
          expires_in_days: days ? parseInt(days, 10) : undefined },
      });
      if (res.success && res.share) { setShares([res.share, ...shares]); toast("Share created"); setEmail(""); }
    } catch (err: unknown) {
      toast((err as Error).message || "Create share failed", true);
    }
  };
  const revoke = async (id: string) => {
    try {
      await api<{ success: boolean }>(
        "/api/creative/projects/" + project.id + "/shares/" + id, { method: "DELETE" });
      setShares(shares.filter((s) => s.id !== id)); toast("Share revoked");
    } catch (err: unknown) {
      toast((err as Error).message || "Revoke failed", true);
    }
  };

  return (
    <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.8)", zIndex: 1000,
      display: "flex", alignItems: "center", justifyContent: "center", padding: 16 }}>
      <div style={{ background: "var(--bg-2)", border: "1px solid var(--border)", borderRadius: 16,
        maxWidth: 420, width: "100%", padding: 20 }}>
        <h2 style={{ fontSize: 18, fontWeight: 700, marginBottom: 14 }}>Share Project</h2>
        <div style={{ display: "flex", gap: 8, marginBottom: 14 }}>
          <input type="email" placeholder="email@example.com" value={email}
            onChange={(e) => setEmail(e.target.value)} style={{ flex: 1 }} />
          <select value={role} onChange={(e) => setRole(e.target.value)}>
            <option value="viewer">Viewer</option><option value="editor">Editor</option>
            <option value="owner">Owner</option>
          </select>
        </div>
        <div style={{ display: "flex", gap: 8, marginBottom: 14 }}>
          <input type="number" min={1} placeholder="expires in days" value={days}
            onChange={(e) => setDays(e.target.value)} style={{ width: 140 }} />
          <button className="btn primary small" onClick={create} style={{ flex: 1 }}>Create Link</button>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 8, maxHeight: 180, overflowY: "auto" }}>
          {shares.map((s) => (
            <div key={s.id} style={{ fontSize: 12, padding: 8, borderRadius: 6, background: "var(--bg-3)",
              display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <div style={{ minWidth: 0 }}>
                <div style={{ fontFamily: "monospace", fontSize: 11, color: "var(--cyan)" }}>
                  {s.token?.slice(0, 6)}…{s.token?.slice(-4)}
                </div>
                <div style={{ color: "var(--text-dim)" }}>
                  {s.role} {s.shared_with_email ? "· " + s.shared_with_email : "· link"}
                </div>
              </div>
              <button className="btn small danger" onClick={() => revoke(s.id)}>✕</button>
            </div>
          ))}
          {shares.length === 0 && <p style={{ fontSize: 12, color: "var(--text-dim)" }}>No active shares.</p>}
        </div>
        <div style={{ marginTop: 14, textAlign: "right" }}>
          <button className="btn" onClick={onClose}>Close</button>
        </div>
      </div>
    </div>
  );
}

function VersionModal({ project, label, setLabel, onSnapshot, onClose, toast }: {
  project: ProjectDetail; label: string; setLabel: (v: string) => void;
  onSnapshot: () => void; onClose: () => void; toast: ToastFn;
}) {
  const [versions, setVersions] = useState<Version[]>([]);
  const load = useCallback(async () => {
    try {
      const res = await api<{ success: boolean; versions?: Version[] }>(
        "/api/creative/projects/" + project.id + "/versions");
      if (res.success) setVersions(res.versions || []);
    } catch (err: unknown) {
      toast((err as Error).message || "Failed to load versions", true);
    }
  }, [project.id, toast]);
  useEffect(() => { load(); }, [load]);

  const restore = async (v: Version) => {
    if (!confirm(`Restore version ${v.version_no}? The current timeline will be replaced.`)) return;
    try {
      const res = await api<{ success: boolean; project?: ProjectDetail }>(
        "/api/creative/projects/" + project.id + "/versions/" + v.id + "/restore",
        { method: "POST" });
      if (res.success) { toast("Version restored"); onClose(); }
    } catch (err: unknown) {
      toast((err as Error).message || "Restore failed", true);
    }
  };

  return (
    <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.8)", zIndex: 1000,
      display: "flex", alignItems: "center", justifyContent: "center", padding: 16 }}>
      <div style={{ background: "var(--bg-2)", border: "1px solid var(--border)", borderRadius: 16,
        maxWidth: 420, width: "100%", padding: 20 }}>
        <h2 style={{ fontSize: 18, fontWeight: 700, marginBottom: 14 }}>Version History</h2>
        <div style={{ display: "flex", gap: 8, marginBottom: 16 }}>
          <input type="text" placeholder="version label" value={label}
            onChange={(e) => setLabel(e.target.value)} style={{ flex: 1 }} />
          <button className="btn primary small" onClick={onSnapshot}>Snapshot</button>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 6, maxHeight: 200, overflowY: "auto" }}>
          {versions.map((v) => (
            <div key={v.id} style={{ fontSize: 12, padding: 8, borderRadius: 6, background: "var(--bg-3)",
              display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <div>v{v.version_no} {v.label ? "· " + v.label : ""}
                <span style={{ color: "var(--text-dim)", float: "right", fontSize: 10 }}>
                  {v.created_at ? new Date(v.created_at).toLocaleString() : ""}
                </span>
              </div>
              <button className="btn small danger" onClick={() => restore(v)}>Restore</button>
            </div>
          ))}
          {versions.length === 0 && <p style={{ fontSize: 12, color: "var(--text-dim)" }}>No snapshots yet.</p>}
        </div>
        <div style={{ marginTop: 14, textAlign: "right" }}>
          <button className="btn" onClick={onClose}>Close</button>
        </div>
      </div>
    </div>
  );
}

// ---- Templates / Brands / Presets view ---------------------------------------

function TemplatesView({ toast }: { toast: ToastFn }) {
  const [templates, setTemplates] = useState<Template[]>([]);
  const [presets, setPresets] = useState<EffectPreset[]>([]);
  const [brands, setBrands] = useState<BrandKit[]>([]);
  const [applyId, setApplyId] = useState<string | null>(null);
  const [applyTitle, setApplyTitle] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [t, e, b] = await Promise.all([
        api<{ success: boolean; templates?: Template[] }>("/api/creative/templates"),
        api<{ success: boolean; presets?: EffectPreset[] }>("/api/creative/effect-presets"),
        api<{ success: boolean; brand_kits?: BrandKit[] }>("/api/creative/brand-kits"),
      ]);
      if (t.success) setTemplates(t.templates || []);
      if (e.success) setPresets(e.presets || []);
      if (b.success) setBrands(b.brand_kits || []);
    } catch (err: unknown) {
      toast((err as Error).message || "Failed to load templates", true);
    }
  }, [toast]);
  useEffect(() => { load(); }, [load]);

  const apply = async (id: string) => {
    setBusy(true);
    try {
      const res = await api<{ success: boolean }>(
        "/api/creative/templates/" + id + "/apply", { method: "POST", body: { title: applyTitle } });
      if (res.success) { toast("Template applied"); setApplyId(null); setApplyTitle(""); }
    } catch (err: unknown) {
      toast((err as Error).message || "Apply failed", true);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ maxWidth: 1200, width: "100%", margin: "0 auto" }}>
      <h1 style={{ fontSize: 22, fontWeight: 700, marginBottom: 20 }}>Templates &amp; Brand Kits</h1>

      <h2 style={{ fontSize: 16, fontWeight: 700, marginTop: 16 }}>Templates</h2>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(260px,1fr))", gap: 16 }}>
        {templates.map((tpl) => (
          <div key={tpl.id} style={{ background: "var(--bg-2)", border: "1px solid var(--border)",
            borderRadius: 12, padding: 16, display: "flex", flexDirection: "column", gap: 10 }}>
            <div style={{ height: 120, background: "var(--bg-3)", borderRadius: 8,
              display: "flex", alignItems: "center", justifyContent: "center",
              border: "1px solid var(--border)" }}>
              <span style={{ fontSize: 32 }}>{tpl.kind === "video" ? "🎞" : "🖼"}</span>
            </div>
            <h3 style={{ fontSize: 15, fontWeight: 600 }}>{tpl.title}</h3>
            <span style={{ fontSize: 10, textTransform: "uppercase", padding: "2px 8px", borderRadius: 4,
              background: "rgba(8,127,255,0.15)", color: "var(--cyan)" }}>{tpl.category} · {tpl.kind}</span>
            <button className="btn primary small"
              onClick={() => { setApplyId(tpl.id); setApplyTitle(""); }}>Apply</button>
          </div>
        ))}
        {templates.length === 0 && <p style={{ fontSize: 12, color: "var(--text-dim)" }}>No templates.</p>}
      </div>

      <h2 style={{ fontSize: 16, fontWeight: 700, marginTop: 24 }}>Effect Presets</h2>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(200px,1fr))", gap: 10, marginTop: 12 }}>
        {presets.map((p) => (
          <div key={p.id} style={{ background: "var(--bg-3)", border: "1px solid var(--border)",
            borderRadius: 8, padding: 10, fontSize: 12 }}>
            <div style={{ fontWeight: 600 }}>{p.name}</div>
            <div style={{ color: "var(--text-dim)" }}>{p.category} · {p.kind}</div>
          </div>
        ))}
      </div>

      <h2 style={{ fontSize: 16, fontWeight: 700, marginTop: 24 }}>Brand Kits</h2>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(200px,1fr))", gap: 10, marginTop: 12 }}>
        {brands.map((bk) => (
          <div key={bk.id} style={{ background: "var(--bg-3)", border: "1px solid var(--border)",
            borderRadius: 8, padding: 10, fontSize: 12 }}>
            <div style={{ fontWeight: 600 }}>{bk.name}</div>
            <div style={{ display: "flex", gap: 4, marginTop: 4 }}>
              {bk.colors.map((c) => <span key={c} style={{ width: 14, height: 14, borderRadius: 3,
                background: c, border: "1px solid var(--border)" }} title={c} />)}
            </div>
            {bk.fonts.length > 0 && <div style={{ color: "var(--text-dim)" }}>Fonts: {bk.fonts.join(", ")}</div>}
          </div>
        ))}
        {brands.length === 0 && <p style={{ fontSize: 12, color: "var(--text-dim)" }}>No brand kits saved.</p>}
      </div>

      {applyId && (
        <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.8)", zIndex: 1000,
          display: "flex", alignItems: "center", justifyContent: "center", padding: 16 }}>
          <div style={{ background: "var(--bg-2)", border: "1px solid var(--border)", borderRadius: 16,
            maxWidth: 360, width: "100%", padding: 20 }}>
            <h2 style={{ fontSize: 17, fontWeight: 700, marginBottom: 12 }}>Apply Template</h2>
            <input type="text" placeholder="project title (optional)" value={applyTitle}
              onChange={(e) => setApplyTitle(e.target.value)} style={{ width: "100%", marginBottom: 16 }} />
            <div style={{ display: "flex", gap: 10, justifyContent: "flex-end" }}>
              <button className="btn" onClick={() => setApplyId(null)}>Cancel</button>
              <button className="btn primary" disabled={busy} onClick={() => apply(applyId)}>
                {busy ? "Applying…" : "Apply"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ---- App (top-level wiring) --------------------------------------------------

const TABS = [
  { id: "projects", label: "Projects" },
  { id: "imageGen", label: "AI Image Generator" },
  { id: "videoGen", label: "AI Video Generator" },
  { id: "editor", label: "Multitrack Editor" },
  { id: "templates", label: "Templates & Brands" },
] as const;
type TabId = (typeof TABS)[number]["id"];

export function CreativeStudioView({ toast }: {
  toast: (msg: string, err?: boolean) => void;
}) {
  const [activeTab, setActiveTab] = useState<TabId>("projects");
  const [projects, setProjects] = useState<Project[]>([]);
  const [currentProject, setCurrentProject] = useState<ProjectDetail | null>(null);
  const [pxPerSecond, setPxPerSecond] = useState(40);
  const [loading, setLoading] = useState(false);

  const loadProjects = useCallback(async () => {
    try {
      const res = await api<{ success: boolean; projects: Project[] }>("/api/creative/projects");
      if (res.success) setProjects(res.projects);
    } catch (err: unknown) {
      toast((err as Error).message || "Failed to load projects", true);
    }
  }, [toast]);

  useEffect(() => { loadProjects(); }, [loadProjects]);

  const openProject = useCallback(async (id: string) => {
    setLoading(true);
    try {
      const res = await api<{ success: boolean; project: ProjectDetail }>("/api/creative/projects/" + id);
      if (res.success) { setCurrentProject(res.project); setActiveTab("editor"); }
    } catch (err: unknown) {
      toast((err as Error).message || "Failed to open project", true);
    } finally {
      setLoading(false);
    }
  }, [toast]);

  return (
    <div style={{ width: "100%", minHeight: "100%", display: "flex", flexDirection: "column",
      background: "var(--bg)", color: "var(--text)" }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between",
        padding: "14px 20px", borderBottom: "1px solid var(--border)", background: "var(--bg-2)" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <span style={{ fontSize: 18, fontWeight: 700, color: "var(--accent)", letterSpacing: 1 }}>
            Jalora Creative Studio
          </span>
          <span style={{ fontSize: 11, padding: "2px 8px", borderRadius: 6,
            background: "rgba(8,127,255,0.15)", color: "var(--cyan)",
            border: "1px solid rgba(8,127,255,0.3)" }}>Pro Suite</span>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          {TABS.map((t) => (
            <button key={t.id} onClick={() => setActiveTab(t.id)}
              style={{ padding: "8px 14px", borderRadius: 8, fontSize: 13, fontWeight: 600,
                cursor: "pointer",
                background: activeTab === t.id ? "var(--accent)" : "var(--bg-3)",
                color: activeTab === t.id ? "#fff" : "var(--text-dim)",
                border: "1px solid var(--border)" }}>
              {t.label}
            </button>
          ))}
        </div>
      </div>

      <div style={{ flex: 1, padding: 20, overflow: "auto" }}>
        {loading && <div style={{ padding: 16, color: "var(--cyan)" }}>Loading…</div>}
        {activeTab === "projects" && (
          <ProjectsView projects={projects} onOpen={openProject} onUpdated={loadProjects} toast={toast} />
        )}
        {activeTab === "imageGen" && <ImageGenView toast={toast} />}
        {activeTab === "videoGen" && <VideoGenView toast={toast} />}
        {activeTab === "editor" && currentProject && (
          <EditorView project={currentProject} setProject={setCurrentProject}
            pxPerSecond={pxPerSecond} setZoom={setPxPerSecond} toast={toast} />
        )}
        {activeTab === "editor" && !currentProject && (
          <div style={{ textAlign: "center", padding: 80, color: "var(--text-dim)" }}>
            <p style={{ fontSize: 18, marginBottom: 16 }}>No project open.</p>
            <button className="btn primary" onClick={() => setActiveTab("projects")}
              style={{ padding: "10px 20px" }}>Go to Projects</button>
          </div>
        )}
        {activeTab === "templates" && <TemplatesView toast={toast} />}
      </div>
    </div>
  );
}
