import { useCallback, useEffect, useState } from "react";
import { api } from "../lib/api";

type Device = {
  id: string;
  name: string;
  platform: string;
  status: string;
  connectivity: "online" | "offline";
  scopes: string[];
  last_seen_at: string | null;
};

type PairRequest = {
  id: string;
  name: string;
  platform: string;
  scopes: string[];
  status: string;
  expires_at: string;
  pairing_url: string;
};

type RemoteCommand = {
  id: string;
  device_id: string;
  action: string;
  url: string;
  status: string;
  protocol_status?: string;
  delivery_count?: number;
  error_code?: string | null;
  receipt?: {
    status?: string;
    evidence?: Record<string, unknown>;
  } | null;
  created_at: string;
};

type DeviceMessage = {
  id: string;
  device_id: string;
  source_app: string;
  sender: string;
  content: string;
  draft_reply: string;
  action_status: string;
  received_at: string;
};

type DevicePlatform = "android" | "ios" | "windows" | "macos" | "linux";
type PermissionOption = { id: string; label: string };

const platformLabels: Record<DevicePlatform, string> = {
  android: "Android",
  ios: "iOS",
  windows: "Windows",
  macos: "macOS",
  linux: "Linux",
};

function isDevicePlatform(value: string): value is DevicePlatform {
  return ["android", "ios", "windows", "macos", "linux"].includes(value);
}

function currentBrowserPlatform(): DevicePlatform {
  const agent = navigator.userAgent.toLowerCase();
  if (/android/.test(agent)) return "android";
  if (/iphone|ipad|ipod/.test(agent)) return "ios";
  if (/win/.test(agent)) return "windows";
  return "linux";
}

export function DeviceAccessPanel({
  onToast,
}: {
  onToast: (message: string, error?: boolean) => void;
}) {
  const defaultPlatform = currentBrowserPlatform();
  const [devices, setDevices] = useState<Device[]>([]);
  const [permissionOptions, setPermissionOptions] = useState<PermissionOption[]>([]);
  const [messages, setMessages] = useState<DeviceMessage[]>([]);
  const [deviceName, setDeviceName] = useState(
    `Current ${platformLabels[defaultPlatform]} device`,
  );
  const [platform, setPlatform] = useState(defaultPlatform);
  const [scopes, setScopes] = useState<string[]>(["remote.browser.open_url"]);
  const [consentConfirmed, setConsentConfirmed] = useState(false);
  const [pairRequest, setPairRequest] = useState<PairRequest | null>(null);
  const [pairRequestId, setPairRequestId] = useState("");
  const [pairCurrentBrowser, setPairCurrentBrowser] = useState(false);
  const [remoteCommands, setRemoteCommands] = useState<RemoteCommand[]>([]);
  const [selectedRemoteDevice, setSelectedRemoteDevice] = useState("");
  const [remoteUrl, setRemoteUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [summaries, setSummaries] = useState<Record<string, string>>({});

  const load = useCallback(async () => {
    try {
      const [deviceRows, messageRows, commandRows, permissions] = await Promise.all([
        api<Device[]>("/api/devices"),
        api<DeviceMessage[]>("/api/devices/inbox"),
        api<RemoteCommand[]>("/api/devices/remote-command-history"),
        api<{ scopes: PermissionOption[] }>("/api/devices/scopes"),
      ]);
      setDevices(deviceRows);
      setPermissionOptions(permissions.scopes);
      setMessages(messageRows);
      setRemoteCommands(commandRows);
      setSelectedRemoteDevice((current) =>
        current || deviceRows.find((device) =>
          device.status === "active" && device.scopes.includes("remote.browser.open_url"),
        )?.id || "",
      );
      setDrafts((existing) => {
        const next = { ...existing };
        for (const message of messageRows) {
          if (!(message.id in next)) next[message.id] = message.draft_reply;
        }
        return next;
      });
    } catch (error) {
      onToast((error as Error).message, true);
    }
  }, [onToast]);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), 15000);
    return () => window.clearInterval(timer);
  }, [load]);

  const toggleScope = (scope: string) => {
    setScopes((current) =>
      current.includes(scope)
        ? current.filter((value) => value !== scope)
        : [...current, scope],
    );
  };

  const pairDevice = async (currentBrowser: boolean) => {
    setBusy(true);
    try {
      const result = await api<PairRequest>("/api/devices/pair-requests", {
        method: "POST",
        body: {
          name: deviceName,
          platform,
          scopes,
          consent_confirmed: consentConfirmed,
        },
      });
      setPairRequest(result);
      setPairRequestId(result.id);
      setPairCurrentBrowser(currentBrowser);
      setDeviceName(currentBrowser ? `Current ${platformLabels[platform]} device` : "");
      setConsentConfirmed(false);
      onToast(currentBrowser
        ? "Pair request created. Open the approval page in a new tab on this device."
        : "Pair request created. Open its link on the device you want to pair.");
    } catch (error) {
      onToast((error as Error).message, true);
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    if (!pairRequestId || pairRequest?.status !== "pending") return;
    let cancelled = false;
    const poll = async () => {
      try {
        const status = await api<PairRequest>(
          `/api/devices/pair-requests/${encodeURIComponent(pairRequestId)}`,
        );
        if (cancelled) return;
        setPairRequest((current) => current ? { ...current, ...status } : current);
        if (status.status === "approved") await load();
      } catch (error) {
        if (!cancelled) onToast((error as Error).message, true);
      }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 3000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [pairRequestId, pairRequest?.status, load, onToast]);

  const sendRemoteUrlRequest = async () => {
    const target = devices.find((device) => device.id === selectedRemoteDevice);
    const url = remoteUrl.trim();
    if (!target || !url) {
      onToast("Choose an approved device and enter a web link.", true);
      return;
    }
    if (!window.confirm(
      `Send "${url}" to ${target.name}? The target device must approve before the link opens.`,
    )) return;
    setBusy(true);
    try {
      const result = await api<{ status?: string }>(`/api/devices/${target.id}/remote-commands`, {
        method: "POST",
        body: { url, confirm: true },
      });
      setRemoteUrl("");
      if (result.status === "paused_approval") {
        onToast("Web link request is awaiting shared approval. Review it in Tasks.");
      } else {
        onToast(`Web link request sent to ${target.name}.`);
        await load();
      }
    } catch (error) {
      onToast((error as Error).message, true);
    } finally {
      setBusy(false);
    }
  };

  const cancelRemoteCommand = async (command: RemoteCommand) => {
    if (!window.confirm(
      command.protocol_status === "running"
        ? "The device may already be performing this action. Request cancellation and mark the outcome uncertain?"
        : "Cancel this remote request before the device executes it?",
    )) return;
    try {
      const result = await api<{
        status: string;
        cancellation_confirmed: boolean;
      }>(`/api/devices/remote-command-history/${command.id}/cancel`, {
        method: "POST",
      });
      onToast(result.cancellation_confirmed
        ? "Remote request cancelled before execution."
        : "Cancellation cannot be confirmed after execution started; the result is marked uncertain.");
      await load();
    } catch (error) {
      onToast((error as Error).message, true);
    }
  };

  const revokeDevice = async (device: Device) => {
    if (!window.confirm(`Revoke access for ${device.name}?`)) return;
    try {
      await api(`/api/devices/${device.id}`, { method: "DELETE" });
      await load();
    } catch (error) {
      onToast((error as Error).message, true);
    }
  };

  const generateText = async (message: DeviceMessage, mode: "summary" | "draft") => {
    setBusy(true);
    try {
      const result = await api<{ reply: string }>(
        `/api/devices/inbox/${message.id}/assist`,
        {
          method: "POST",
          body: { mode },
        },
      );
      if (mode === "draft") {
        setDrafts((current) => ({ ...current, [message.id]: result.reply }));
      } else {
        setSummaries((current) => ({ ...current, [message.id]: result.reply }));
      }
    } catch (error) {
      onToast((error as Error).message, true);
    } finally {
      setBusy(false);
    }
  };

  const saveDraft = async (message: DeviceMessage) => {
    try {
      const result = await api<{ status?: string }>(`/api/devices/inbox/${message.id}`, {
        method: "PATCH",
        body: { draft_reply: drafts[message.id] ?? "" },
      });
      if (result.status === "paused_approval") {
        onToast("Draft save is awaiting shared approval. Review it in Tasks.");
        return;
      }
      onToast("Draft saved.");
      await load();
    } catch (error) {
      onToast((error as Error).message, true);
    }
  };

  const approveReply = async (message: DeviceMessage) => {
    const reply = (drafts[message.id] ?? "").trim();
    if (!reply) {
      onToast("Write or generate a reply before approving it.", true);
      return;
    }
    if (!window.confirm("Queue this edited reply for sending on the device?")) return;
    try {
      const result = await api<{ status?: string }>(`/api/devices/inbox/${message.id}/approve`, {
        method: "POST",
        body: { reply, confirm_send: true },
      });
      if (result.status === "paused_approval") {
        onToast("Reply is awaiting shared approval. Review it in Tasks.");
      } else {
        onToast("Reply queued. The companion will send it when the app allows.");
        await load();
      }
    } catch (error) {
      onToast((error as Error).message, true);
    }
  };

  const deleteMessage = async (message: DeviceMessage) => {
    try {
      const result = await api<{ status?: string }>(
        `/api/devices/inbox/${message.id}`, { method: "DELETE" },
      );
      if (result.status === "paused_approval") {
        onToast("Message deletion is awaiting shared approval. Review it in Tasks.");
        return;
      }
      onToast("Device message deleted.");
      await load();
    } catch (error) {
      onToast((error as Error).message, true);
    }
  };

  return (
    <div className="panel device-access-panel">
      <h3>Another device — remote companion pairing</h3>
      <p className="muted small">
        Choose whether to pair the browser/device you are using now or a different
        device. The target approves the short-lived request. This is separate from
        Windows app control above: this browser companion can open approved public
        web links only; it cannot read screens, delete files, or control other apps.
      </p>

      <div className="form-grid">
        <label>
          Device name
          <input
            value={deviceName}
            maxLength={120}
            onChange={(event) => setDeviceName(event.target.value)}
            placeholder="My Android phone"
          />
        </label>
        <label>
          Platform
          <select
            value={platform}
            onChange={(event) => {
              const nextPlatform = event.target.value;
              if (isDevicePlatform(nextPlatform)) setPlatform(nextPlatform);
            }}
          >
            <option value="android">Android</option>
            <option value="ios">iOS</option>
            <option value="windows">Windows</option>
            <option value="macos">macOS</option>
            <option value="linux">Linux</option>
          </select>
        </label>
      </div>
      <div className="column gap">
        {permissionOptions.map((permission) => (
          <label className="row gap" key={permission.id}>
            <input
              type="checkbox"
              checked={scopes.includes(permission.id)}
              onChange={() => toggleScope(permission.id)}
            />
            {permission.label}
          </label>
        ))}
      </div>
      <label className="row gap">
        <input
          type="checkbox"
          checked={consentConfirmed}
          onChange={(event) => setConsentConfirmed(event.target.checked)}
        />
        I authorize this device to use the selected permissions.
      </label>
      <div className="row gap">
        <button
          className="btn small primary"
          disabled={busy || !deviceName.trim() || !consentConfirmed || scopes.length === 0}
          onClick={() => void pairDevice(true)}
        >
          Pair this browser/device
        </button>
        <button
          className="btn small"
          disabled={busy || !deviceName.trim() || !consentConfirmed || scopes.length === 0}
          onClick={() => void pairDevice(false)}
        >
          Pair a different device
        </button>
      </div>

      {pairRequest && (
        <div className="notice warn">
          <strong>
            Pairing {pairRequest.status} · expires {new Date(pairRequest.expires_at).toLocaleTimeString()}
          </strong>
          <p>
            {pairCurrentBrowser
              ? "Open the approval page in another tab in this browser, then approve the request there:"
              : `Open this link on ${pairRequest.name} to review and approve the request:`}
          </p>
          <a
            href={pairRequest.pairing_url}
            target="_blank"
            rel="noopener noreferrer"
            className="btn small"
          >
            {pairCurrentBrowser ? "Open approval on this device" : "Open pairing link"}
          </a>
          <input
            readOnly
            value={pairRequest.pairing_url}
            aria-label="Pairing link"
            onFocus={(event) => event.target.select()}
          />
          <button
            className="btn small"
            onClick={() => {
              void navigator.clipboard.writeText(pairRequest.pairing_url).then(
                () => onToast("Pairing link copied. Open it on the target device."),
                (error: unknown) => onToast((error as Error).message, true),
              );
            }}
          >
            Copy pairing link
          </button>
        </div>
      )}

      <h4>Paired remote devices</h4>
      {devices.length === 0 ? (
        <p className="muted small">No devices paired.</p>
      ) : (
        devices.map((device) => (
          <div className="provider-row" key={device.id}>
            <div>
              <div className="name">
                {device.name} · {device.platform} · {device.connectivity}
              </div>
              <div className="model">
                {device.status === "revoked" ? "revoked" : "paired"} · {(device.scopes || []).join(", ")}
                {device.last_seen_at
                  ? ` · Last active ${new Date(device.last_seen_at).toLocaleString()}`
                  : ""}
              </div>
            </div>
            {device.status !== "revoked" && (
              <button
                className="btn small danger"
                onClick={() => void revokeDevice(device)}
              >
                Revoke
              </button>
            )}
          </div>
        ))
      )}

      <h4>Open a web link on a paired device</h4>
      <p className="muted small">
        The target companion page must be open. It will display the request and
        wait for a local approval before opening the external link.
      </p>
      <div className="form-grid">
        <label>
          Target device
          <select
            value={selectedRemoteDevice}
            onChange={(event) => setSelectedRemoteDevice(event.target.value)}
          >
            <option value="">Choose a device</option>
            {devices.filter((device) =>
              device.status === "active" &&
              device.scopes.includes("remote.browser.open_url"),
            ).map((device) => (
              <option key={device.id} value={device.id}>{device.name}</option>
            ))}
          </select>
        </label>
        <label>
          Web link
          <input
            type="url"
            value={remoteUrl}
            maxLength={2000}
            onChange={(event) => setRemoteUrl(event.target.value)}
            placeholder="https://example.com"
          />
        </label>
      </div>
      <button
        className="btn small primary"
        disabled={busy || !selectedRemoteDevice || !remoteUrl.trim()}
        onClick={() => void sendRemoteUrlRequest()}
      >
        Request on target device
      </button>
      {remoteCommands.length > 0 && (
        <div className="column gap">
          <h4>Recent remote requests</h4>
          {remoteCommands.slice(0, 10).map((command) => (
            <div className="provider-row" key={command.id}>
              <div>
                <div className="name">
                  {devices.find((device) => device.id === command.device_id)?.name || "Device"}
                  {" · "}{command.url}
                </div>
                <div className="model">
                  {command.protocol_status || command.status}
                  {command.delivery_count ? ` · deliveries ${command.delivery_count}` : ""}
                  {command.error_code ? ` · ${command.error_code}` : ""}
                  {command.receipt?.evidence?.browser_open_receipt === true
                    ? " · browser handoff reported"
                    : ""}
                </div>
              </div>
              {["queued", "delivered", "running"].includes(command.protocol_status || "") && (
                <button
                  className="btn small danger"
                  disabled={busy}
                  onClick={() => void cancelRemoteCommand(command)}
                >
                  {command.protocol_status === "running"
                    ? "Request cancellation"
                    : "Cancel request"}
                </button>
              )}
            </div>
          ))}
        </div>
      )}

      <h4>Device messages</h4>
      {messages.length === 0 ? (
        <p className="muted small">No messages have been received from a companion.</p>
      ) : (
        messages.map((message) => {
          const sourceDevice = devices.find((device) => device.id === message.device_id);
          const canDraft = sourceDevice?.status === "active" &&
            sourceDevice.scopes.includes("messages.draft");
          const canSend = sourceDevice?.status === "active" &&
            sourceDevice.scopes.includes("messages.send");
          return (
          <section className="panel device-message" key={message.id}>
            <div className="name">{message.sender || "Unknown sender"} · {message.source_app}</div>
            <p>{message.content}</p>
            {summaries[message.id] && (
              <p className="device-message-summary">
                <strong>Summary:</strong> {summaries[message.id]}
              </p>
            )}
            <div className="row gap">
              <button
                className="btn small"
                disabled={busy}
                onClick={() => void generateText(message, "summary")}
              >
                Summarize
              </button>
              <button
                className="btn small"
                disabled={busy || message.action_status !== "none" || !canDraft}
                onClick={() => void generateText(message, "draft")}
              >
                Draft reply
              </button>
            </div>
            {message.action_status === "none" ? (
              <>
                {(canDraft || canSend) && (
                  <textarea
                    value={drafts[message.id] ?? ""}
                    maxLength={4000}
                    onChange={(event) =>
                      setDrafts((current) => ({ ...current, [message.id]: event.target.value }))
                    }
                    placeholder="Edit the reply before approving..."
                    rows={3}
                  />
                )}
                <div className="row gap">
                  {(canDraft || canSend) && (
                    <button className="btn small" onClick={() => void saveDraft(message)}>
                      Save draft
                    </button>
                  )}
                  {canSend && (
                    <button
                      className="btn small primary"
                      onClick={() => void approveReply(message)}
                    >
                      Approve &amp; queue send
                    </button>
                  )}
                  <button
                    className="btn small danger"
                    onClick={() => void deleteMessage(message)}
                  >
                    Delete message
                  </button>
                </div>
              </>
            ) : (
              <p className="muted small">Reply status: {message.action_status}</p>
            )}
          </section>
          );
        })
      )}
    </div>
  );
}
