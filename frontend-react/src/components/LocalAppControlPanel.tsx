import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import {
  createRecognizer,
  readRecognition,
  type SpeechRecognitionLike,
} from "../lib/voice";

type LocalApp = {
  id: string;
  name: string;
  executable_path: string;
  enabled: boolean;
  running: boolean;
};

type LocalStatus = {
  available: boolean;
  platform: string;
  message: string;
};

type PendingAction = {
  appId: string;
  appName: string;
  action: "launch" | "close" | "click" | "type" | "key";
  x?: number;
  y?: number;
  text?: string;
  key?: string;
};

type AppControl = {
  name: string;
  control_type: string;
  automation_id: string;
  enabled: boolean;
  visible: boolean;
  bounds: { left: number; top: number; right: number; bottom: number };
};

type AppInspection = {
  mode: "accessibility" | "screenshot_ocr";
  app: string;
  controls?: AppControl[];
  text?: string;
  truncated: boolean;
};

export function LocalAppControlPanel({
  onToast,
}: {
  onToast: (message: string, error?: boolean) => void;
}) {
  const [status, setStatus] = useState<LocalStatus | null>(null);
  const [apps, setApps] = useState<LocalApp[]>([]);
  const [name, setName] = useState("");
  const [executablePath, setExecutablePath] = useState("");
  const [consentConfirmed, setConsentConfirmed] = useState(false);
  const [pendingAction, setPendingAction] = useState<PendingAction | null>(null);
  const [selectedAppId, setSelectedAppId] = useState("");
  const [clickX, setClickX] = useState("");
  const [clickY, setClickY] = useState("");
  const [textToType, setTextToType] = useState("");
  const [keyToPress, setKeyToPress] = useState("Enter");
  const [recognizedCommand, setRecognizedCommand] = useState("");
  const [listening, setListening] = useState(false);
  const [busy, setBusy] = useState(false);
  const [inspection, setInspection] = useState<AppInspection | null>(null);
  const recognizerRef = useRef<SpeechRecognitionLike | null>(null);

  const load = useCallback(async () => {
    try {
      const localStatus = await api<LocalStatus>("/api/devices/local/status");
      setStatus(localStatus);
      if (localStatus.available) {
        setApps(await api<LocalApp[]>("/api/devices/local/apps"));
      } else {
        setApps([]);
      }
    } catch (error) {
      onToast((error as Error).message, true);
    }
  }, [onToast]);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), 10000);
    return () => {
      window.clearInterval(timer);
      recognizerRef.current?.abort();
    };
  }, [load]);

  const registerApp = async () => {
    setBusy(true);
    try {
      await api("/api/devices/local/apps", {
        method: "POST",
        body: {
          name,
          executable_path: executablePath,
          consent_confirmed: consentConfirmed,
        },
      });
      setName("");
      setExecutablePath("");
      setConsentConfirmed(false);
      await load();
      onToast("App added to Jalora's approved app list.");
    } catch (error) {
      onToast((error as Error).message, true);
    } finally {
      setBusy(false);
    }
  };

  const performAction = async (action: PendingAction) => {
    setBusy(true);
    try {
      const result = action.action === "click" || action.action === "type" ||
          action.action === "key"
        ? await api<{ status: string }>(
          `/api/devices/local/apps/${action.appId}/interact`,
          {
            method: "POST",
            body: {
              action: action.action,
              confirm: true,
              x: action.x,
              y: action.y,
              text: action.text,
              key: action.key,
            },
          },
        )
        : await api<{ status: string }>(
          `/api/devices/local/apps/${action.appId}/${action.action}`,
          { method: "POST", body: { confirm: true } },
        );
      setPendingAction(null);
      if (result.status === "paused_approval") {
        onToast("Local app action is awaiting shared approval. Review it in Tasks.");
        return;
      }
      if (action.action === "launch") setSelectedAppId(action.appId);
      if (action.action === "close") {
        const closed = result.status === "closed" || result.status === "already_exited";
        onToast(
          closed
            ? `${action.appName} closed.`
            : `Asked ${action.appName} to close gracefully, but Jalora could not verify that it exited.`,
          !closed,
        );
      } else {
        onToast(`${action.appName}: ${result.status}.`);
      }
      await load();
    } catch (error) {
      onToast((error as Error).message, true);
    } finally {
      setBusy(false);
    }
  };

  const removeApp = async (app: LocalApp) => {
    if (!window.confirm(`Remove Jalora's approval for ${app.name}?`)) return;
    try {
      await api(`/api/devices/local/apps/${app.id}`, { method: "DELETE" });
      await load();
    } catch (error) {
      onToast((error as Error).message, true);
    }
  };

  const inspectApp = async (app: LocalApp, mode: AppInspection["mode"]) => {
    const method = mode === "accessibility"
      ? "read its accessibility controls"
      : "capture visible text for OCR";
    if (!window.confirm(
      `Allow Jalora to ${method} in ${app.name}? This may expose sensitive information currently visible in that app.`,
    )) return;
    setBusy(true);
    try {
      const result = await api<AppInspection & { status?: string }>(
        `/api/devices/local/apps/${app.id}/inspect`,
        { method: "POST", body: { mode, confirm: true } },
      );
      if (result.status === "paused_approval") {
        onToast("App inspection is awaiting shared approval. Review it in Tasks.");
        return;
      }
      setInspection(result);
    } catch (error) {
      onToast((error as Error).message, true);
    } finally {
      setBusy(false);
    }
  };

  const clickAccessibleControl = async (control: AppControl) => {
    const app = apps.find((candidate) => candidate.id === selectedAppId);
    if (!app) return;
    const label = control.name || control.automation_id;
    if (!window.confirm(
      `Click "${label}" in ${app.name}? This may submit data or trigger an irreversible action.`,
    )) return;
    setBusy(true);
    try {
      const result = await api<{ verified?: boolean; status?: string }>(
        `/api/devices/local/apps/${app.id}/click-control`,
        {
          method: "POST",
          body: {
            name: control.name,
            automation_id: control.automation_id || null,
            confirm: true,
          },
        },
      );
      if (result.status === "paused_approval") {
        onToast("App control action is awaiting shared approval. Review it in Tasks.");
        return;
      }
      onToast(result.verified
        ? `Action completed; the accessibility tree changed in ${app.name}.`
        : `Action was sent, but Jalora could not verify a visible change in ${app.name}. Do not repeat it blindly.`,
        !result.verified);
      setInspection(null);
    } catch (error) {
      onToast((error as Error).message, true);
    } finally {
      setBusy(false);
    }
  };

  const queueInteraction = (
    action: "click" | "type" | "key",
    details: { x?: number; y?: number; text?: string; key?: string } = {},
  ) => {
    const app = apps.find((candidate) => candidate.id === selectedAppId);
    if (!app || !app.running) {
      onToast("Open an approved app through Jalora, then select it to operate.", true);
      return;
    }
    setPendingAction({ appId: app.id, appName: app.name, action, ...details });
  };

  const startVoice = () => {
    if (listening) {
      recognizerRef.current?.stop();
      return;
    }
    const recognizer = createRecognizer();
    if (!recognizer) {
      onToast("Voice recognition is unavailable in this browser.", true);
      return;
    }
    recognizer.lang = "en-KE";
    recognizer.onstart = () => setListening(true);
    recognizer.onerror = (event) => {
      setListening(false);
      onToast(`Voice recognition failed: ${event.error}.`, true);
    };
    recognizer.onend = () => {
      setListening(false);
      recognizerRef.current = null;
    };
    recognizer.onresult = (event) => {
      const { finalText } = readRecognition(event);
      const command = finalText.trim();
      if (!command) return;
      setRecognizedCommand(command);
      const normalized = command
        .replace(/^[,.\s]*(jalora|hey jalora)[,.\s]*/i, "")
        .trim();
      const match = normalized.match(/^(open|launch|close|quit)\s+(.+?)\.?$/i);
      if (match) {
        const [, verb, spokenName] = match;
        const app = apps.find(
          (candidate) =>
            candidate.name.toLocaleLowerCase() === spokenName.trim().toLocaleLowerCase(),
        );
        if (!app) {
          onToast(`"${spokenName.trim()}" is not in your approved app list.`, true);
          recognizer.stop();
          return;
        }
        setPendingAction({
          appId: app.id,
          appName: app.name,
          action: /^(close|quit)$/i.test(verb) ? "close" : "launch",
        });
        recognizer.stop();
        return;
      }
      const clickMatch = normalized.match(/^click\s+(\d+)[,\s]+(\d+)$/i);
      if (clickMatch) {
        queueInteraction("click", {
          x: Number(clickMatch[1]),
          y: Number(clickMatch[2]),
        });
        recognizer.stop();
        return;
      }
      const typeMatch = normalized.match(/^type\s+([\s\S]+)$/i);
      if (typeMatch) {
        queueInteraction("type", { text: typeMatch[1] });
        recognizer.stop();
        return;
      }
      const keyMatch = normalized.match(
        /^press\s+(enter|tab|esc|backspace|space|up|down|left|right)$/i,
      );
      if (keyMatch) {
        const keyMap: Record<string, string> = {
          enter: "Enter",
          tab: "Tab",
          esc: "Esc",
          backspace: "Backspace",
          space: "Space",
          up: "Up",
          down: "Down",
          left: "Left",
          right: "Right",
        };
        queueInteraction("key", { key: keyMap[keyMatch[1].toLowerCase()] });
        recognizer.stop();
        return;
      }
      onToast(
        'Use "open <approved app>", "close <app>", "click x y", "type <text>", or "press <key>".',
        true,
      );
      recognizer.stop();
    };
    recognizerRef.current = recognizer;
    try {
      recognizer.start();
    } catch (error) {
      recognizerRef.current = null;
      setListening(false);
      onToast((error as Error).message, true);
    }
  };

  return (
    <div className="panel device-access-panel">
      <h3>This Jalora computer — Windows app control</h3>
      <p className="muted small">
        This section controls only approved desktop apps on the Windows computer
        running Jalora&apos;s backend. It is not necessarily the device where this
        browser tab is open, and it is separate from the remote paired devices below.
        Add trusted .exe files yourself. Every voice command is push-to-talk and
        requires a separate confirmation; no microphone stays open. For an app Jalora
        launched, you can request a click in its client area, type text, or press a
        limited key. Each operation is separately confirmed and works only while that
        same app is the foreground window. Accessibility inspection and OCR are
        read-only and require separate confirmation. Every input action still requires
        review.
      </p>

      {status && !status.available && (
        <div className="device-control-notice">{status.message}</div>
      )}
      {status?.available && (
        <>
          <div className="device-app-form">
            <label>
              Display name
              <input
                value={name}
                maxLength={120}
                onChange={(event) => setName(event.target.value)}
                placeholder="Notepad"
              />
            </label>
            <label>
              Full path to trusted Windows .exe
              <input
                value={executablePath}
                maxLength={1024}
                onChange={(event) => setExecutablePath(event.target.value)}
                placeholder="C:\Windows\System32\notepad.exe"
              />
            </label>
            <label className="device-consent">
              <input
                type="checkbox"
                checked={consentConfirmed}
                onChange={(event) => setConsentConfirmed(event.target.checked)}
              />
              I trust this program and authorize Jalora to launch it on this PC.
            </label>
            <button
              className="btn small primary"
              disabled={busy || !name.trim() || !executablePath.trim() || !consentConfirmed}
              onClick={() => void registerApp()}
            >
              Approve app
            </button>
          </div>

          <div className="device-voice-controls">
            <button className="btn small" disabled={busy} onClick={startVoice}>
              {listening ? "Stop listening" : "Push to talk"}
            </button>
            <span className="muted small">
              Say “open Notepad”, “close Notepad”, “click 300 200”, “type hello”, or “press Enter”.
              Review and confirm every action.
            </span>
          </div>
          {recognizedCommand && (
            <p className="device-message-summary">Heard: {recognizedCommand}</p>
          )}

          {pendingAction && (
            <div className="device-control-confirm">
              <span>
                Confirm {pendingAction.action} for <strong>{pendingAction.appName}</strong>
                {pendingAction.action === "click" &&
                  ` at ${pendingAction.x}, ${pendingAction.y}`}
                {pendingAction.action === "type" && `: “${pendingAction.text}”`}
                {pendingAction.action === "key" && `: ${pendingAction.key}`}?
              </span>
              {(pendingAction.action === "click" ||
                pendingAction.action === "type" ||
                pendingAction.action === "key") && (
                <span className="device-action-warning">
                  This input could send a message or trigger an irreversible action in the app.
                  Verify the foreground window and content before confirming.
                </span>
              )}
              <button
                className="btn small primary"
                disabled={busy}
                onClick={() => void performAction(pendingAction)}
              >
                Confirm
              </button>
              <button
                className="btn small"
                disabled={busy}
                onClick={() => setPendingAction(null)}
              >
                Cancel
              </button>
            </div>
          )}

          <h4>Approved apps</h4>
          {apps.length === 0 ? (
            <p className="muted small">No desktop apps approved yet.</p>
          ) : (
            apps.map((app) => (
              <div className="provider-row" key={app.id}>
                <div>
                  <div className="name">{app.name} · {app.running ? "running" : "stopped"}</div>
                  <div className="model">{app.executable_path}</div>
                </div>
                <div className="device-actions">
                  <button
                    className="btn small primary"
                    disabled={busy || !app.enabled || app.running}
                    onClick={() =>
                      setPendingAction({ appId: app.id, appName: app.name, action: "launch" })
                    }
                  >
                    Open
                  </button>
                  <button
                    className={`btn small${selectedAppId === app.id ? " primary" : ""}`}
                    disabled={!app.running}
                    onClick={() => setSelectedAppId(app.id)}
                  >
                    {selectedAppId === app.id ? "Selected" : "Operate"}
                  </button>
                  <button
                    className="btn small"
                    disabled={busy || !app.running}
                    onClick={() => void inspectApp(app, "accessibility")}
                  >
                    Inspect controls
                  </button>
                  <button
                    className="btn small"
                    disabled={busy || !app.running}
                    onClick={() =>
                      setPendingAction({ appId: app.id, appName: app.name, action: "close" })
                    }
                  >
                    Close
                  </button>
                  <button
                    className="btn small danger"
                    disabled={busy || app.running}
                    onClick={() => void removeApp(app)}
                  >
                    Remove
                  </button>
                </div>
              </div>
            ))
          )}
          {selectedAppId && apps.some(
            (app) => app.id === selectedAppId && app.running && app.enabled,
          ) && (
            <div className="device-app-form">
              <p className="muted small">
                Selected app: {apps.find((app) => app.id === selectedAppId)?.name}.
                Focus its window on the desktop before confirming an input.
                Click coordinates are pixels from the top-left of the app's content area.
              </p>
              <div className="device-input-row">
                <button
                  className="btn small"
                  disabled={busy}
                  onClick={() => {
                    const app = apps.find((candidate) => candidate.id === selectedAppId);
                    if (app) void inspectApp(app, "screenshot_ocr");
                  }}
                >
                  Screenshot + OCR
                </button>
                <span className="muted small">
                  OCR needs Tesseract installed on this PC.
                </span>
              </div>
              {inspection && inspection.app === apps.find(
                (app) => app.id === selectedAppId,
              )?.name && (
                <div className="device-control-notice">
                  <div className="row gap">
                    <strong>
                      {inspection.mode === "accessibility" ? "Accessibility tree" : "OCR text"}
                    </strong>
                    <button className="btn small" onClick={() => setInspection(null)}>
                      Clear
                    </button>
                  </div>
                  {inspection.controls?.map((control, index) => (
                    <div className="provider-row" key={`${control.automation_id}-${control.name}-${index}`}>
                      <div>
                        <div className="name">
                          {control.name || "(unnamed control)"} · {control.control_type}
                        </div>
                        <div className="model">
                          {control.automation_id ? `ID: ${control.automation_id} · ` : ""}
                          {control.enabled ? "enabled" : "disabled"} · {control.visible ? "visible" : "hidden"}
                        </div>
                      </div>
                      <button
                        className="btn small"
                        disabled={busy || !control.enabled || !control.visible || (!control.name && !control.automation_id)}
                        onClick={() => void clickAccessibleControl(control)}
                      >
                        Confirm click
                      </button>
                    </div>
                  ))}
                  {inspection.text && <pre className="email-body">{inspection.text}</pre>}
                  {inspection.truncated && <div className="muted small">Result was truncated.</div>}
                </div>
              )}
              <div className="device-input-row">
                <input
                  type="number"
                  min={0}
                  max={10000}
                  aria-label="Click x coordinate"
                  placeholder="X"
                  value={clickX}
                  onChange={(event) => setClickX(event.target.value)}
                />
                <input
                  type="number"
                  min={0}
                  max={10000}
                  aria-label="Click y coordinate"
                  placeholder="Y"
                  value={clickY}
                  onChange={(event) => setClickY(event.target.value)}
                />
                <button
                  className="btn small"
                  disabled={!clickX || !clickY}
                  onClick={() => queueInteraction("click", {
                    x: Number(clickX),
                    y: Number(clickY),
                  })}
                >
                  Click
                </button>
              </div>
              <textarea
                value={textToType}
                maxLength={2000}
                onChange={(event) => setTextToType(event.target.value)}
                placeholder="Text to type into the focused app"
                rows={3}
              />
              <button
                className="btn small"
                disabled={!textToType}
                onClick={() => queueInteraction("type", { text: textToType })}
              >
                Type text
              </button>
              <div className="device-input-row">
                <select value={keyToPress} onChange={(event) => setKeyToPress(event.target.value)}>
                  {["Enter", "Tab", "Esc", "Backspace", "Space", "Up", "Down", "Left", "Right"]
                    .map((key) => <option key={key} value={key}>{key}</option>)}
                </select>
                <button
                  className="btn small"
                  onClick={() => queueInteraction("key", { key: keyToPress })}
                >
                  Press key
                </button>
              </div>
            </div>
          )}
        </>
      )}
      <p className="muted small">
        For email, WhatsApp, and calls, use connected providers and their existing
        review/approval steps. This Windows app-control increment does not read or send
        messages through other apps.
      </p>
    </div>
  );
}
