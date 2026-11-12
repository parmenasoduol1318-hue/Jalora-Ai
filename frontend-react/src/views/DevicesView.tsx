import { useEffect, useState } from "react";
import { DeviceAccessPanel } from "../components/DeviceAccessPanel";
import { LocalAppControlPanel } from "../components/LocalAppControlPanel";
import { api } from "../lib/api";

type DeviceOperation = {
  operation: string;
  label: string;
  status: string;
  reason: string;
  approval_required?: boolean;
};

type DeviceCapabilityInventory = {
  devices: Array<{
    id: string;
    name: string;
    platform: string;
    status: string;
    companion_capabilities: DeviceOperation[];
    unsupported_operations: DeviceOperation[];
  }>;
  local_host: {
    name: string;
    platform: string;
    status: string;
    reason: string;
    applications: Array<{
      id: string;
      name: string;
      running: boolean;
      capabilities: string[];
    }>;
  };
  cloud_integrations: Array<{ provider: string; area: string; access: string }>;
  cloud_browser: { status: string; operations: string[]; note: string };
  limitations: string[];
};

export function DevicesView({
  t,
  onToast,
}: {
  t: (key: string) => string;
  onToast: (message: string, error?: boolean) => void;
}) {
  const [inventory, setInventory] = useState<DeviceCapabilityInventory | null>(null);

  useEffect(() => {
    let active = true;
    const load = async () => {
      try {
        const result = await api<DeviceCapabilityInventory>("/api/devices/capabilities");
        if (active) setInventory(result);
      } catch (error) {
        if (active) onToast((error as Error).message, true);
      }
    };
    void load();
    const timer = window.setInterval(() => void load(), 15000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [onToast]);

  return (
    <>
      <div className="view-header">
        <h2>{t("devicesAccess")}</h2>
        <p>Review this Jalora computer and separately paired browser devices.</p>
      </div>

      <section className="device-connection-guide" aria-labelledby="device-connection-title">
        <h3 id="device-connection-title">Know which device Jalora can access</h3>
        <div className="device-connection-guide-grid">
          <div className="device-connection-guide-card">
            <strong>This Jalora computer</strong>
            <p>
              Windows app controls run on the computer hosting Jalora&apos;s backend,
              not necessarily the device displaying this page.
            </p>
          </div>
          <div className="device-connection-guide-card">
            <strong>Paired browser device</strong>
            <p>
              The current web companion can open public links only after approval
              on the target. It cannot inspect a screen, inventory installed apps,
              or control other applications.
            </p>
          </div>
        </div>
      </section>

      <section className="panel" aria-labelledby="device-capabilities-title">
        <h3 id="device-capabilities-title">Available device capabilities</h3>
        {!inventory ? (
          <p className="muted small">Loading this account&apos;s device and integration inventory…</p>
        ) : (
          <>
            <div className="device-connection-guide-grid">
              <div className="device-connection-guide-card">
                <strong>
                  {inventory.local_host.name} ({inventory.local_host.platform})
                </strong>
                <p>{inventory.local_host.reason}</p>
                <p className="muted small">
                  Status: {inventory.local_host.status}. Approved apps:{" "}
                  {inventory.local_host.applications.length
                    ? inventory.local_host.applications.map((app) =>
                      `${app.name}${app.running ? " (running)" : ""}`).join(", ")
                    : "none registered"}
                </p>
              </div>
              <div className="device-connection-guide-card">
                <strong>Cloud browser and research</strong>
                <p>{inventory.cloud_browser.note}</p>
                <p className="muted small">
                  Status: {inventory.cloud_browser.status}. Operations:{" "}
                  {inventory.cloud_browser.operations.length
                    ? inventory.cloud_browser.operations.join(", ")
                    : "none registered"}
                </p>
              </div>
            </div>

            <h4>Paired devices</h4>
            {inventory.devices.length === 0 ? (
              <p className="muted small">No paired devices are registered for this account.</p>
            ) : inventory.devices.map((device) => (
              <details key={device.id} className="device-capability-device">
                <summary>
                  {device.name} ({device.platform}) — {device.status}
                </summary>
                <ul>
                  {device.companion_capabilities.map((operation) => (
                    <li key={operation.operation}>
                      {operation.label}: {operation.status}
                      {operation.approval_required ? " (approval required)" : ""}
                      {" — "}{operation.reason}
                    </li>
                  ))}
                  {device.unsupported_operations.map((operation) => (
                    <li key={operation.operation}>
                      {operation.label}: unsupported — {operation.reason}
                    </li>
                  ))}
                </ul>
              </details>
            ))}

            <h4>Connected cloud integrations</h4>
            {inventory.cloud_integrations.length
              ? <ul>{inventory.cloud_integrations.map((integration) => (
                <li key={integration.provider}>
                  {integration.provider} ({integration.area}): {integration.access}
                </li>
              ))}</ul>
              : <p className="muted small">No connected cloud integrations are reported.</p>}

            <ul className="muted small">
              {inventory.limitations.map((limitation) => <li key={limitation}>{limitation}</li>)}
            </ul>
          </>
        )}
      </section>

      <LocalAppControlPanel onToast={onToast} />
      <DeviceAccessPanel onToast={onToast} />
    </>
  );
}
