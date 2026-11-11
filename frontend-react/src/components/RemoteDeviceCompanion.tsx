import { useCallback, useEffect, useState } from "react";

type PairRequest = {
  id: string;
  name: string;
  platform: string;
  scopes: string[];
  status: string;
  expires_at: string;
  requested_by?: string;
};

type RemoteCommand = {
  id: string;
  action: "browser.open_url";
  url: string;
  status: string;
  created_at: string;
};

type DeviceCredentials = {
  device_token: string;
  device: { id: string; name: string; scopes: string[] };
};

async function deviceApi<T>(
  path: string,
  token: string,
  method = "GET",
  body?: unknown,
): Promise<T> {
  const response = await fetch(path, {
    method,
    headers: {
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = (await response.json().catch(() => ({}))) as Record<string, unknown>;
  if (!response.ok) {
    const error = (data.error || {}) as { details?: string };
    throw new Error(error.details || String(data.message || response.statusText));
  }
  return data as T;
}

export function RemoteDeviceCompanion({
  onExit,
}: {
  onExit: () => void;
}) {
  const [pairLink] = useState(() => {
    const fragment = new URLSearchParams(window.location.hash.slice(1));
    return {
      requestId: fragment.get("pair_request_id") || "",
      code: fragment.get("pair_code") || "",
    };
  });
  const pairCode = pairLink.code;
  const pairRequestId = pairLink.requestId;
  const [pairRequest, setPairRequest] = useState<PairRequest | null>(null);
  const [deviceToken, setDeviceToken] = useState(
    () => pairLink.code ? "" : sessionStorage.getItem("jalora_remote_device_token") || "",
  );
  const [deviceName, setDeviceName] = useState(
    () => sessionStorage.getItem("jalora_remote_device_name") || "",
  );
  const [commands, setCommands] = useState<RemoteCommand[]>([]);
  const [busyCommand, setBusyCommand] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    if (new URLSearchParams(window.location.hash.slice(1)).has("pair_code")) {
      window.history.replaceState({}, "", window.location.pathname);
    }
  }, []);

  useEffect(() => {
    if (!pairRequestId || deviceToken) return;
    let cancelled = false;
    deviceApi<PairRequest>(
      `/api/devices/pair-requests/${encodeURIComponent(pairRequestId)}/target`,
      "",
      "POST",
      { pairing_code: pairCode },
    )
      .then((request) => {
        if (!cancelled) setPairRequest(request);
      })
      .catch((cause: unknown) => {
        if (!cancelled) setError((cause as Error).message);
      });
    return () => { cancelled = true; };
  }, [deviceToken, pairRequestId, pairCode]);

  const refreshCommands = useCallback(async () => {
    if (!deviceToken) return;
    try {
      const current = await deviceApi<RemoteCommand[]>(
        "/api/devices/remote-commands/target",
        deviceToken,
      );
      setCommands(current);
      setError("");
    } catch (cause) {
      setError((cause as Error).message);
    }
  }, [deviceToken]);

  useEffect(() => {
    if (!deviceToken) return;
    void refreshCommands();
    const timer = window.setInterval(() => void refreshCommands(), 3000);
    return () => window.clearInterval(timer);
  }, [deviceToken, refreshCommands]);

  const approvePairing = async () => {
    if (!pairRequestId || !pairCode || !pairRequest || pairRequest.status !== "pending") return;
    setError("");
    try {
      const result = await deviceApi<DeviceCredentials>(
        `/api/devices/pair-requests/${encodeURIComponent(pairRequestId)}/approve`,
        "",
        "POST",
        { confirm: true, pairing_code: pairCode },
      );
      sessionStorage.setItem("jalora_remote_device_token", result.device_token);
      sessionStorage.setItem("jalora_remote_device_name", result.device.name);
      setDeviceToken(result.device_token);
      setDeviceName(result.device.name);
      setPairRequest(null);
      setError("");
    } catch (cause) {
      setError((cause as Error).message);
    }
  };

  const rejectPairing = async () => {
    if (!pairRequestId || !pairCode) return;
    setError("");
    try {
      await deviceApi(
        `/api/devices/pair-requests/${encodeURIComponent(pairRequestId)}/reject`,
        "",
        "POST",
        { pairing_code: pairCode },
      );
      setPairRequest((current) => current ? { ...current, status: "rejected" } : current);
    } catch (cause) {
      setError((cause as Error).message);
    }
  };

  const respondToCommand = async (
    command: RemoteCommand,
    approve: boolean,
  ) => {
    setBusyCommand(command.id);
    setError("");
    let destination: Window | null = null;
    try {
      if (approve) {
        destination = window.open("about:blank", "_blank");
        if (!destination) {
          throw new Error("Allow pop-ups for Jalora to open the link after approval.");
        }
        destination.opener = null;
      }
      const decision = await deviceApi<{ status: string; url?: string }>(
        `/api/devices/remote-commands/${command.id}/decision`,
        deviceToken,
        "POST",
        { approve },
      );
      if (!approve) {
        setCommands((current) => current.filter((item) => item.id !== command.id));
        return;
      }
      if (!decision.url) throw new Error("The approved destination was not returned.");
      destination!.location.replace(decision.url);
      await deviceApi(
        `/api/devices/remote-commands/${command.id}/opened`,
        deviceToken,
        "POST",
        { opened: true },
      );
      setCommands((current) => current.filter((item) => item.id !== command.id));
    } catch (cause) {
      destination?.close();
      setError((cause as Error).message);
      if (approve) {
        try {
          await deviceApi(
            `/api/devices/remote-commands/${command.id}/opened`,
            deviceToken,
            "POST",
            { opened: false },
          );
        } catch {
          // Keep the original action error visible; the command can be retried if still pending.
        }
      }
    } finally {
      setBusyCommand("");
      void refreshCommands();
    }
  };

  const disconnect = () => {
    sessionStorage.removeItem("jalora_remote_device_token");
    sessionStorage.removeItem("jalora_remote_device_name");
    setDeviceToken("");
    setDeviceName("");
    setCommands([]);
  };

  return (
    <main className="content">
      <section className="panel device-access-panel">
        <h2>Jalora remote device companion</h2>
        <p className="muted small">
          This web companion can open an approved public link in a browser only.
          It cannot read the device screen or interact with other apps. Keep it
          open on the target device to receive requests.
        </p>
        {error && <div className="notice warn" role="alert">{error}</div>}

        {!deviceToken && pairRequest && (
          <div className="column gap">
            <h3>Approve device pairing?</h3>
            <p>
              A Jalora user requested pairing for <strong>{pairRequest.name}</strong>
              {" "}({pairRequest.platform}).
            </p>
            {pairRequest.requested_by && (
              <p>Requesting Jalora account: <strong>{pairRequest.requested_by}</strong></p>
            )}
            <p>Permissions requested: {(pairRequest.scopes || []).join(", ")}</p>
            <p className="muted small">
              Approve only if you recognize this request. Opening each web link
              still requires your approval on this device.
            </p>
            <div className="row gap">
              <button
                className="btn small primary"
                disabled={busyCommand !== "" || pairRequest.status !== "pending"}
                onClick={() => void approvePairing()}
              >
                Approve pairing
              </button>
              <button
                className="btn small danger"
                disabled={busyCommand !== "" || pairRequest.status !== "pending"}
                onClick={() => void rejectPairing()}
              >
                Reject
              </button>
            </div>
            {pairRequest.status !== "pending" && (
              <p>Pairing request {pairRequest.status}.</p>
            )}
          </div>
        )}

        {!deviceToken && !pairRequest && !error && (
          <p>Open the one-time pairing link from Jalora on this device.</p>
        )}

        {deviceToken && (
          <div className="column gap">
            <div className="row gap">
              <strong>Connected: {deviceName}</strong>
              <button className="btn small danger" onClick={disconnect}>
                Disconnect this browser
              </button>
            </div>
            <p className="muted small">
              Pending web-link requests will appear here. Each link needs your
              approval before the browser opens it.
            </p>
            {commands.length === 0 ? (
              <p className="muted small">No pending remote actions.</p>
            ) : commands.map((command) => (
              <section className="panel device-message" key={command.id}>
                <div className="name">Open web link</div>
                <p>{command.url}</p>
                <div className="row gap">
                  <button
                    className="btn small primary"
                    disabled={Boolean(busyCommand)}
                    onClick={() => void respondToCommand(command, true)}
                  >
                    Approve &amp; open link
                  </button>
                  <button
                    className="btn small danger"
                    disabled={Boolean(busyCommand)}
                    onClick={() => void respondToCommand(command, false)}
                  >
                    Reject
                  </button>
                </div>
              </section>
            ))}
          </div>
        )}
        <button className="btn small" onClick={onExit}>Back to Jalora</button>
      </section>
    </main>
  );
}
