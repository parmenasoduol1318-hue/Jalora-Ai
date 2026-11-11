import { DeviceAccessPanel } from "../components/DeviceAccessPanel";
import { LocalAppControlPanel } from "../components/LocalAppControlPanel";

export function DevicesView({
  t,
  onToast,
}: {
  t: (key: string) => string;
  onToast: (message: string, error?: boolean) => void;
}) {
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

      <LocalAppControlPanel onToast={onToast} />
      <DeviceAccessPanel onToast={onToast} />
    </>
  );
}
