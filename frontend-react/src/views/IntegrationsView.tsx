import { useCallback, useEffect, useState } from "react";
import { api } from "../lib/api";
import { ProviderLogo } from "../components/ProviderLogos";
import type { IntegrationItem, ProviderCard, CustomIntegrationOut } from "../lib/types";

type Capabilities = {
  gmail_oauth?: boolean;
  email_provider?: string;
  twilio?: boolean;
  telnyx?: boolean;
  search_provider?: string;
  browser?: boolean;
};

type CapabilityScopeCard = {
  id: string;
  title: string;
  scope: string;
  limitations: string[];
  tests: string[];
  status: string;
  live_provider_verified: boolean;
  tools: Array<{
    name: string;
    permission_level: number;
    permission_name: string;
    approval_required_by_default: boolean;
    side_effect: string;
  }>;
};

type GmailStart = { integration_id: string; provider: string; authorization_url: string };
type ConnectResult = {
  status: string;
  provider: string;
  integration_id?: string;
  authorization_url?: string;
};
type TestResult = { ok: boolean; provider: string; status: string; detail: string };

export function IntegrationsView({
  t,
  onToast,
}: {
  t: (k: string) => string;
  onToast: (m: string, e?: boolean) => void;
}) {
  const [integrations, setIntegrations] = useState<IntegrationItem[]>([]);
  const [providerCards, setProviderCards] = useState<ProviderCard[]>([]);
  const [caps, setCaps] = useState<Capabilities>({});
  const [capabilityScopes, setCapabilityScopes] = useState<CapabilityScopeCard[]>([]);
  const [busy, setBusy] = useState(false);
  const [customIntegrations, setCustomIntegrations] = useState<CustomIntegrationOut[]>([]);
  const [showCustomForm, setShowCustomForm] = useState(false);
  const [customForm, setCustomForm] = useState({
    name: "", auth_url: "", token_url: "", client_id: "",
    client_secret: "", redirect_uri: "", scopes: "",
    token_auth_style: "body", userinfo_url: "", extra_auth_params: "",
  });

  const load = useCallback(async () => {
    try {
      const [items, health, registry, customs, scopeCatalog] = await Promise.all([
        api<IntegrationItem[]>("/api/integrations"),
        api<{ capabilities: Capabilities }>("/api/health"),
        api<{ providers: ProviderCard[] }>("/api/integrations/providers"),
        api<CustomIntegrationOut[]>("/api/integrations/custom"),
        api<{ capabilities: CapabilityScopeCard[] }>("/api/capabilities"),
      ]);
      setIntegrations(items);
      setCaps(health.capabilities || {});
      setProviderCards(registry.providers || []);
      setCustomIntegrations(customs || []);
      setCapabilityScopes(scopeCatalog.capabilities || []);
    } catch (err) {
      onToast((err as Error).message, true);
    }
  }, [onToast]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const status = params.get("gmail");
    if (!status) return;
    if (status === "connected") {
      const email = params.get("email");
      onToast(email ? `${t("gmailConnected")}: ${email}` : t("gmailConnected"));
    } else {
      onToast(t("gmailConnectError"), true);
    }
    window.history.replaceState({}, "", window.location.pathname);
  }, [onToast, t]);

  // Handle custom integration OAuth callback redirect
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const integration = params.get("integration");
    if (integration === "connected") {
      const name = params.get("name") || "";
      onToast(name ? `${t("integrationConnected")}: ${name}` : t("integrationConnected"));
      const url = new URL(window.location.href);
      url.searchParams.delete("integration");
      url.searchParams.delete("custom");
      url.searchParams.delete("name");
      url.searchParams.delete("provider");
      url.searchParams.delete("reason");
      window.history.replaceState({}, "", url.toString());
    } else if (integration === "error") {
      const reason = params.get("reason") || t("integrationError");
      onToast(reason, true);
      const url = new URL(window.location.href);
      url.searchParams.delete("integration");
      url.searchParams.delete("custom");
      url.searchParams.delete("name");
      url.searchParams.delete("provider");
      url.searchParams.delete("reason");
      window.history.replaceState({}, "", url.toString());
    }
  }, [onToast, t]);

  const gmail = integrations.find((i) => i.provider === "gmail");
  const mock = integrations.find((i) => i.provider === "email_mock");

  const connectGmail = async () => {
    setBusy(true);
    try {
      const start = await api<GmailStart>("/api/integrations/gmail/start", { method: "POST" });
      window.location.href = start.authorization_url;
    } catch (err) {
      onToast((err as Error).message, true);
    } finally {
      setBusy(false);
    }
  };

  const connectDemo = async () => {
    setBusy(true);
    try {
      await api("/api/integrations", {
        method: "POST",
        body: { provider: "email_mock", config: {} },
      });
      onToast(t("testSuccess"));
      load();
    } catch (err) {
      onToast((err as Error).message, true);
    } finally {
      setBusy(false);
    }
  };

  const disconnect = async (id: string) => {
    try {
      await api(`/api/integrations/${id}`, { method: "DELETE" });
      load();
    } catch (err) {
      onToast((err as Error).message, true);
    }
  };

  const runTest = async (id: string) => {
    try {
      const res = await api<TestResult>(`/api/integrations/${id}/test`, { method: "POST" });
      onToast(res.ok ? t("testSuccess") : res.detail || t("testFailed"), !res.ok);
    } catch (err) {
      onToast((err as Error).message, true);
    }
  };

  const connectProvider = async (card: ProviderCard) => {
    if (!card.configured) {
      onToast(t("addCredentialsHint"), true);
      return;
    }
    setBusy(true);
    try {
      const res = await api<ConnectResult>(
        `/api/integrations/${card.name}/connect`,
        { method: "POST" },
      );
      if (res.status === "redirect" && res.authorization_url) {
        window.location.href = res.authorization_url;
        return;
      }
      onToast(t("integrationConnected"));
      load();
    } catch (err) {
      onToast((err as Error).message, true);
    } finally {
      setBusy(false);
    }
  };

  const connectCustom = async (integration: CustomIntegrationOut) => {
    setBusy(true);
    try {
      const res = await api<ConnectResult>(
        `/api/integrations/custom/${integration.id}/connect`,
        { method: "POST" },
      );
      if (res.status === "redirect" && res.authorization_url) {
        window.location.href = res.authorization_url;
        return;
      }
      onToast(t("integrationConnected"));
      load();
    } catch (err) {
      onToast((err as Error).message, true);
    } finally {
      setBusy(false);
    }
  };

  const saveCustomIntegration = async () => {
    if (!customForm.name || !customForm.auth_url || !customForm.token_url ||
        !customForm.client_id || !customForm.client_secret) {
      onToast(t("customSaveError"), true);
      return;
    }
    setBusy(true);
    try {
      const scopes = customForm.scopes
        ? customForm.scopes.split(",").map((s) => s.trim()).filter(Boolean)
        : [];
      const extraParams: Record<string, string> = {};
      if (customForm.extra_auth_params) {
        customForm.extra_auth_params.split(",").forEach((pair) => {
          const [k, v] = pair.split("=").map((s) => s.trim());
          if (k && v) extraParams[k] = v;
        });
      }
      await api("/api/integrations/custom", {
        method: "POST",
        body: {
          name: customForm.name,
          auth_url: customForm.auth_url,
          token_url: customForm.token_url,
          client_id: customForm.client_id,
          client_secret: customForm.client_secret,
          redirect_uri: customForm.redirect_uri,
          scopes: scopes,
          token_auth_style: customForm.token_auth_style,
          userinfo_url: customForm.userinfo_url || undefined,
          extra_auth_params: Object.keys(extraParams).length > 0 ? extraParams : undefined,
        },
      });
      onToast(t("customSaved"));
      setShowCustomForm(false);
      setCustomForm({
        name: "", auth_url: "", token_url: "", client_id: "",
        client_secret: "", redirect_uri: "", scopes: "",
        token_auth_style: "body", userinfo_url: "", extra_auth_params: "",
      });
      load();
    } catch (err) {
      onToast(t("customSaveError"), true);
    } finally {
      setBusy(false);
    }
  };

  const disconnectCustom = async (integration: CustomIntegrationOut) => {
    try {
      await api(`/api/integrations/custom/${integration.id}`, { method: "DELETE" });
      load();
    } catch (err) {
      onToast((err as Error).message, true);
    }
  };

  const testCustom = async (integration: CustomIntegrationOut) => {
    try {
      const res = await api<TestResult>(
        `/api/integrations/custom/${integration.id}/test`,
        { method: "POST" },
      );
      onToast(res.ok ? t("testSuccess") : (res.detail || t("testFailed")), !res.ok);
    } catch (err) {
      onToast((err as Error).message, true);
    }
  };

  const providerLabel = (name: string) => {
    const key = `provider${name.charAt(0).toUpperCase()}${name.slice(1)}`;
    const val = t(key);
    // If the translator returns the raw key (no translation), use a readable fallback
    return val === key ? name.charAt(0).toUpperCase() + name.slice(1) : val;
  };

  const capLabel = (key: string) => {
    const labels: Record<string, string> = {
      gmail_oauth: t("gmailLabel"),
      email_provider: t("email"),
      twilio: t("providerTwilio"),
      telnyx: t("providerTelnyx"),
      voice: t("voiceTitle"),
      search_provider: t("providerSearch"),
      browser: t("capBrowser"),
      calendar_provider: t("providerCalendar"),
      drive_provider: t("providerDrive"),
      contacts: t("providerContacts"),
      maps_provider: t("providerMaps"),
      weather_provider: t("providerWeather"),
      translate_provider: t("providerTranslate"),
    };
    return labels[key] ?? key;
  };

  // Render every capability the backend reports — never a hardcoded list.
  const capRows = Object.entries(caps).map(([key, value]) => ({
    key,
    label: capLabel(key),
    active: value !== false && value !== "none" && value !== "" && value != null,
  }));

  return (
    <>
      <div className="view-header">
        <h2>{t("integrationsTitle")}</h2>
        <p>{t("integrationsHint")}</p>
      </div>

      <div className="panel">
        <h3>{t("gmailLabel")}</h3>
        {caps.gmail_oauth === false && (
          <div className="notice warn">{t("gmailNotConfigured")}</div>
        )}

        <div className="provider-row">
          <div>
            <div className="name">
              <ProviderLogo id="gmail" className="provider-logo" />
              {t("gmailLabel")}
            </div>
            <div className="model">
              {gmail
                ? gmail.status === "connected"
                  ? `${t("statusConnected")} · ${(gmail.config.email_address as string) || ""}`
                  : gmail.status === "error"
                    ? t("statusError")
                    : t("statusDisabled")
                : t("notConfigured")}
            </div>
          </div>
          <div className="row gap">
            <button
              className="btn small primary"
              onClick={connectGmail}
              disabled={busy || caps.gmail_oauth === false}
            >
              {gmail ? t("reconnectGmail") : t("connectGmail")}
            </button>
            {gmail && (
              <>
                <button className="btn small" onClick={() => runTest(gmail.id)}>
                  {t("testConnection")}
                </button>
                <button className="btn small danger" onClick={() => disconnect(gmail.id)}>
                  {t("disconnect")}
                </button>
              </>
            )}
          </div>
        </div>
        {gmail && (gmail.error || gmail.status === "error") && (
          <div className="notice warn">{gmail.error || t("integrationError")}</div>
        )}
        {gmail && gmail.status === "error" &&
          /403|access blocked|access_denied/i.test(gmail.error || "") && (
            <div className="notice warn">{t("gmail403Hint")}</div>
          )}
        <p className="muted small">{t("oauthHint")}</p>

        <h3>{t("mockEmailLabel")}</h3>
        <div className="provider-row">
          <div>
            <div className="name">
              <ProviderLogo id="email" className="provider-logo" />
              {t("mockEmailLabel")}
            </div>
            <div className="model">
              {mock
                ? mock.status === "connected"
                  ? t("statusConnected")
                  : mock.status === "error"
                    ? t("statusError")
                    : t("statusDisabled")
                : t("notConfigured")}
            </div>
          </div>
          <div className="row gap">
            {mock ? (
              <>
                <button className="btn small" onClick={() => runTest(mock.id)}>
                  {t("testConnection")}
                </button>
                <button className="btn small danger" onClick={() => disconnect(mock.id)}>
                  {t("disconnect")}
                </button>
              </>
            ) : (
              <button className="btn small primary" onClick={connectDemo} disabled={busy}>
                {t("connectAccount")}
              </button>
            )}
          </div>
        </div>
        <p className="muted small">{t("mockEmailHint")}</p>
        {mock && (mock.error || mock.status === "error") && (
          <div className="notice warn">{mock.error || t("integrationError")}</div>
        )}
      </div>

      <div className="panel">
        <h3>{t("assistantIntegrations")}</h3>
        <p className="muted small">{t("providersHint")}</p>
        {providerCards
          .filter((c) => c.name !== "gmail")
          .map((card) => {
            const row = integrations.find((i) => i.provider === card.name);
            // Prefer the live per-user Integration row; fall back to the
            // registry's configuration state when no row has been created.
            const stateLabel = row
              ? row.status === "connected"
                ? t("statusConnected")
                : row.status === "error"
                  ? t("statusError")
                  : row.status === "pending"
                    ? t("statusPending")
                    : t("statusDisabled")
              : card.connected
                ? card.keyless
                  ? t("statusKeyless")
                  : t("statusConnected")
                : card.configured
                  ? t("statusAvailable")
                  : t("statusUnconfigured");
            return (
              <div className="provider-row" key={card.name}>
                <div>
                  <div className="name">
                    <ProviderLogo id={card.logo ?? card.name} className="provider-logo" />
                    {providerLabel(card.name)}
                  </div>
                  <div className="model">
                    {card.description} · {stateLabel}
                    {row?.status === "error" && row.error ? ` — ${row.error}` : ""}
                  </div>
                </div>
                <div className="row gap">
                  {row ? (
                    <>
                      <button className="btn small" onClick={() => runTest(row.id)}>
                        {t("testConnection")}
                      </button>
                      <button className="btn small danger" onClick={() => disconnect(row.id)}>
                        {t("disconnect")}
                      </button>
                    </>
                  ) : card.connectable ? (
                    <button
                      className={`btn small${card.configured ? " primary" : ""}`}
                      onClick={() => connectProvider(card)}
                      disabled={busy || !card.configured}
                      title={!card.configured ? t("addCredentialsHint") : undefined}
                    >
                      {card.configured ? t("connectProvider") : t("addCredentials")}
                    </button>
                  ) : (
                    <span className={`tool-pill ${card.connected ? "p3" : ""}`}>
                      {stateLabel}
                    </span>
                  )}
                </div>
              </div>
            );
          })}
      </div>

      <div className="panel">
          <h3>{t("capabilityScopeTitle")}</h3>
          <p className="muted small">{t("capabilityScopeHint")}</p>
          <div className="notice warn">{t("capabilityOutcomeNotice")}</div>
          {capabilityScopes.map((card) => (
            <details className="capability-scope" key={card.id}>
              <summary>
                <strong>{card.title}</strong>
                <span className="tool-pill">{t("capabilityRegistered")}</span>
              </summary>
              <p>{card.scope}</p>
              <p className="muted small">
                {t("liveProviderNotVerified")}
              </p>
              <div className="row gap" aria-label={t("capabilityTools")}>
                {card.tools.map((tool) => (
                  <span className="tool-pill" key={tool.name} title={`${tool.side_effect}; permission ${tool.permission_name}`}>
                    {tool.name} · {tool.approval_required_by_default ? t("approvalRequired") : tool.permission_name}
                  </span>
                ))}
              </div>
              <h4>{t("capabilityLimitations")}</h4>
              <ul>
                {card.limitations.map((limitation) => <li key={limitation}>{limitation}</li>)}
              </ul>
              <p className="muted small">
                {t("capabilityTestEvidence")}: {card.tests.join(", ")}
              </p>
            </details>
          ))}
      </div>

      <div className="panel">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <h3>{t("addCustomIntegration")}</h3>
          <button
            className="btn small"
            onClick={() => setShowCustomForm(!showCustomForm)}
          >
            {showCustomForm ? "−" : "+"}
          </button>
        </div>
        <p className="muted small">{t("addCustomHint")}</p>

        {showCustomForm && (
          <div className="custom-integration-form">
            <div className="field-row">
              <label>{t("customName")}</label>
              <input
                type="text"
                placeholder={t("customName")}
                value={customForm.name}
                onChange={(e) => setCustomForm({ ...customForm, name: e.target.value })}
              />
            </div>
            <div className="field-row">
              <label>{t("customAuthUrl")}</label>
              <input
                type="url"
                placeholder="https://example.com/oauth/authorize"
                value={customForm.auth_url}
                onChange={(e) => setCustomForm({ ...customForm, auth_url: e.target.value })}
              />
            </div>
            <div className="field-row">
              <label>{t("customTokenUrl")}</label>
              <input
                type="url"
                placeholder="https://example.com/oauth/token"
                value={customForm.token_url}
                onChange={(e) => setCustomForm({ ...customForm, token_url: e.target.value })}
              />
            </div>
            <div className="field-row">
              <label>{t("customClientId")}</label>
              <input
                type="text"
                placeholder={t("customName")}
                value={customForm.client_id}
                onChange={(e) => setCustomForm({ ...customForm, client_id: e.target.value })}
              />
            </div>
            <div className="field-row">
              <label>{t("customClientSecret")}</label>
              <input
                type="password"
                placeholder="••••••••"
                value={customForm.client_secret}
                onChange={(e) => setCustomForm({ ...customForm, client_secret: e.target.value })}
              />
            </div>
            <div className="field-row">
              <label>{t("customRedirectUrl")}</label>
              <input
                type="text"
                placeholder="http://localhost:5173/api/integrations/custom/callback"
                value={customForm.redirect_uri}
                onChange={(e) => setCustomForm({ ...customForm, redirect_uri: e.target.value })}
              />
              <p className="muted small">{t("customRedirectHint")}</p>
            </div>
            <div className="field-row">
              <label>{t("customScopes")}</label>
              <input
                type="text"
                placeholder="read,write,profile"
                value={customForm.scopes}
                onChange={(e) => setCustomForm({ ...customForm, scopes: e.target.value })}
              />
            </div>
            <div className="field-row">
              <label>{t("customAuthStyle")}</label>
              <select
                value={customForm.token_auth_style}
                onChange={(e) => setCustomForm({ ...customForm, token_auth_style: e.target.value })}
              >
                <option value="body">Body (client_id/client_secret in POST body)</option>
                <option value="basic">Basic (HTTP Basic Auth header)</option>
              </select>
            </div>
            <button
              className="btn small primary"
              onClick={saveCustomIntegration}
              disabled={busy}
            >
              {t("customSave")}
            </button>
          </div>
        )}

        {customIntegrations.length > 0 && (
          <div style={{ marginTop: "12px" }}>
            {customIntegrations.map((ci) => (
              <div className="provider-row" key={ci.id}>
                <div>
                  <div className="name">
                    <ProviderLogo id="oauth" className="provider-logo" />
                    {ci.name}
                  </div>
                  <div className="model">
                    {ci.provider} · {ci.status === "connected"
                      ? t("statusConnected")
                      : ci.status === "error"
                        ? t("statusError")
                        : ci.status === "pending"
                          ? t("statusPending")
                          : t("statusDisabled")}
                    {ci.error ? ` — ${ci.error}` : ""}
                  </div>
                </div>
                <div className="row gap">
                  {ci.status === "connected" ? (
                    <>
                      <button className="btn small" onClick={() => testCustom(ci)}>
                        {t("testConnection")}
                      </button>
                      <button className="btn small" onClick={() => connectCustom(ci)}>
                        {t("reconnectGmail")}
                      </button>
                      <button className="btn small danger" onClick={() => disconnectCustom(ci)}>
                        {t("disconnect")}
                      </button>
                    </>
                  ) : ci.status === "pending" ? (
                    <button className="btn small" onClick={() => connectCustom(ci)}>
                      {t("testConnection")}
                    </button>
                  ) : (
                    <button className="btn small primary" onClick={() => connectCustom(ci)} disabled={busy}>
                      {t("connectProvider")}
                    </button>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="panel">
        <h3>{t("capabilities")}</h3>
        <div className="tool-list">
          {capRows.map((c) => {
            const iconId = c.key.replace("_", "").replace("_provider", "").replace("_oauth", "");
            return (
              <span
                key={c.key}
                className={`tool-pill ${c.active ? "p3" : ""}`}
                title={c.key}
              >
                <ProviderLogo id={iconId} className="tool-logo" />
                {c.label}
              </span>
            );
          })}
        </div>
      </div>
    </>
  );
}