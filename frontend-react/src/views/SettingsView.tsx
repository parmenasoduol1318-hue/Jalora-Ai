import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { ProviderLogo } from "../components/ProviderLogos";
import type { Locale } from "../lib/i18n";
import type { AppSettings, VoiceSettings, ModelInfo } from "../lib/types";
import { listVoices } from "../lib/voice";

type ProviderInfo = {
  model: string;
  configured: boolean;
  keyless: boolean;
  disabled: boolean;
  usable: boolean;
};
type ProvidersResponse = { providers: Record<string, ProviderInfo> };

/* Providers that run locally and do not need (or accept) an API key. */
const KEYLESS_PROVIDERS = new Set(["ollama", "lmstudio", "jan"]);

/* Dashboard / credit-balance pages for every provider — used by the
   "Open dashboard" link so users can see their key status / usage / credits. */
const PROVIDER_DASHBOARD_URLS: Record<string, string> = {
  openai: "https://platform.openai.com/api-keys",
  anthropic: "https://console.anthropic.com/settings/keys",
  google: "https://aistudio.google.com/apikey",
  deepseek: "https://platform.deepseek.com/api-keys",
  groq: "https://console.groq.com/keys",
  mistral: "https://console.mistral.ai/api-keys",
  openrouter: "https://openrouter.ai/keys",
  cohere: "https://dashboard.cohere.com/api-keys",
  cerebras: "https://platform.cerebras.ai/api-keys",
  perplexity: "https://www.perplexity.ai",
  poolside: "https://platform.poolside.ai/",
  nvidia: "https://build.nvidia.com/",
  copilot: "https://github.com/settings/tokens",
  codex: "https://platform.openai.com/api-keys",
  lmstudio: "http://localhost:1234",
  ollama: "http://localhost:11434",
  // Jan has no standard web dashboard — user configures manually.
};

/* Cloud/key-required providers whose API key the user can supply per-account. */
const KEY_REQUIRED_PROVIDERS = new Set([
  "openai", "anthropic", "google", "deepseek", "groq", "mistral",
  "openrouter", "cohere", "cerebras", "perplexity",
  "poolside",
  "nvidia",
  "copilot", "codex",
]);

export function SettingsView({
  t, locale, onLocale, onToast, theme, toggleTheme,
}: {
  t: (k: string) => string;
  locale: Locale;
  onLocale: (l: Locale) => void;
  onToast: (m: string, e?: boolean) => void;
  theme: "dark" | "light";
  toggleTheme: () => void;
}) {
  const [providers, setProviders] = useState<Record<string, ProviderInfo>>({});
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [healthData, setHealthData] = useState<Record<string, unknown> | null>(null);
  const [voices, setVoices] = useState<SpeechSynthesisVoice[]>(listVoices);
  const [providerKeys, setProviderKeys] = useState<Set<string>>(new Set());

  /* Which provider is currently being connected via the modal dialog. */
  const [connectProvider, setConnectProvider] = useState<string | null>(null);
  const [apiKeyInput, setApiKeyInput] = useState("");

  useEffect(() => {
    const synth = typeof window !== "undefined" && "speechSynthesis" in window
      ? window.speechSynthesis
      : null;
    if (!synth) return;
    const refresh = () => setVoices(synth.getVoices());
    refresh();
    synth.addEventListener?.("voiceschanged", refresh);
    return () => synth.removeEventListener?.("voiceschanged", refresh);
  }, []);

  const load = async () => {
    try {
      const [prov, prefs, health] = await Promise.all([
        api<ProvidersResponse>("/api/providers"),
        api<AppSettings>("/api/settings"),
        api<Record<string, unknown>>("/api/health"),
      ]);
      setProviders(prov.providers);
      setSettings(prefs);
      setProviderKeys(new Set(prefs.provider_keys ?? []));
      setHealthData(health);
    } catch (err) {
      onToast((err as Error).message, true);
    }
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react/hooks/exhaustive-deps
  }, []);

  const patchSettings = async (body: Record<string, unknown>, okMsg?: string) => {
    try {
      const next = await api<AppSettings>("/api/settings", { method: "PATCH", body });
      setSettings(next);
      window.dispatchEvent(new Event("jalora:settings-changed"));
      if (okMsg) onToast(okMsg);
      return true;
    } catch (err) {
      onToast((err as Error).message, true);
      return false;
    }
  };

  const savePolicies = async () => {
    if (!settings) return;
    const policies: Record<string, string> = {};
    document.querySelectorAll<HTMLSelectElement>("#policy-list select").forEach((sel) => {
      if (sel.value && sel.dataset.tool) policies[sel.dataset.tool] = sel.value;
    });
    await patchSettings({ tool_policies: policies }, t("policiesSaved"));
  };

  /* Phase H (§46-48): persist voice preferences server-side. */
  const setVoice = (changes: Partial<VoiceSettings>) => {
    if (!settings) return;
    void patchSettings({ voice: { ...settings.voice, ...changes } });
  };

  /* Disconnect/reconnect an AI provider for this account. Keys stay in the
     server environment; the router simply stops using the provider. */
  const toggleProvider = async (name: string, disable: boolean) => {
    if (!settings) return;
    const current = settings.disabled_providers ?? [];
    const next = disable
      ? Array.from(new Set([...current, name]))
      : current.filter((n) => n !== name);
    const ok = await patchSettings(
      { disabled_providers: next },
      disable ? t("providerDisconnected") : t("providerReconnected"),
    );
    if (ok) await load();
  };

  /* Save a per-user API key via the encrypted store. */
  const connectProviderKey = async (provider: string) => {
    const key = apiKeyInput.trim();
    if (!key) {
      onToast(t("apiKeyRequired"), true);
      return;
    }
    try {
      await api("/api/settings/provider-key", {
        method: "POST",
        body: { provider, api_key: key },
      });
      const newKeys = new Set(providerKeys);
      newKeys.add(provider);
      setProviderKeys(newKeys);
      setSettings((prev) => prev ? { ...prev, provider_keys: [...newKeys] } : prev);
      onToast(t("apiKeySaved"));
      setConnectProvider(null);
      setApiKeyInput("");
    } catch (err) {
      onToast((err as Error).message, true);
    }
  };

  /* Remove a per-user API key, reverting to env-var / disconnect behaviour. */
  const removeProviderKey = async (provider: string) => {
    try {
      await api(`/api/settings/provider-key/${provider}`, { method: "DELETE" });
      const newKeys = new Set(providerKeys);
      newKeys.delete(provider);
      setProviderKeys(newKeys);
      setSettings((prev) => prev ? { ...prev, provider_keys: [...newKeys] } : prev);
      onToast(t("apiKeyRemoved"));
      await load();
    } catch (err) {
      onToast((err as Error).message, true);
    }
  };

  /* Open a provider's dashboard / usage page in a new tab. */
  const openDashboard = (provider: string) => {
    const url = PROVIDER_DASHBOARD_URLS[provider];
    if (url) window.open(url, "_blank", "noopener,noreferrer");
  };

  /* ── LM Studio configuration (spec §lmstudio-models) ── */
  const [lmstudioModels, setLmstudioModels] = useState<ModelInfo[]>([]);
  const [lmstudioStatus, setLmstudioStatus] = useState<"idle" | "loading" | "ok" | "error">(
    settings?.lmstudio_base_url ? "loading" : "idle"
  );

  const testLmstudioConnection = async () => {
    if (!settings?.lmstudio_base_url) return;
    setLmstudioStatus("loading");
    try {
      const resp = await api<{ models: ModelInfo[]; available: boolean; error?: string }>(
        "/api/settings/lmstudio/models",
      );
      setLmstudioModels(resp.models || []);
      setLmstudioStatus(resp.available ? "ok" : "error");
      if (resp.error) onToast(resp.error, true);
    } catch (err) {
      setLmstudioStatus("error");
      onToast((err as Error).message, true);
    }
  };

  const reloadLmstudioModels = async () => {
    if (!settings?.lmstudio_base_url) return;
    await testLmstudioConnection();
  };

  // Auto-test connection on mount if LM Studio is configured
  useEffect(() => {
    if (settings?.lmstudio_base_url) {
      void testLmstudioConnection();
    }
    // eslint-disable-next-line react/hooks/exhaustive-deps
  }, [settings?.lmstudio_base_url]);

  /* Determine whether a provider shows "Connect" (needs a per-user key). */
  const needsApiKey = (name: string, info: ProviderInfo): boolean => {
    if (name === "google" || name === "groq") {
      return !providerKeys.has(name);
    }
    if (info.configured) return false;            // env key already present
    if (info.keyless) return false;               // local, no key needed
    if (providerKeys.has(name)) return false;     // user already supplied a key
    return KEY_REQUIRED_PROVIDERS.has(name);
  };

  return (
    <>
      <div className="view-header">
        <h2>{t("settingsTitle")}</h2>
      </div>
      <div className="panel">
        <h3>{t("providers")}</h3>
        <div className="provider-list">
          {Object.entries(providers).map(([name, info]) => {
            const hasKey = providerKeys.has(name);
            const hasProviderConfig = info.configured || hasKey;
            const usable = !info.disabled && (info.usable || hasKey);
            return (
            <div key={name} className="provider-row">
              <div>
                <div className="name">
                  <ProviderLogo id={name} className="provider-logo" />
                  {name}
                </div>
                <div className="model">{info.model}</div>
              </div>
              <span className={`badge ${usable ? "ok" : info.disabled ? "warn" : ""}`}>
                {hasKey && (name === "google" || name === "groq")
                  ? t("personalProviderKey")
                  : info.configured && (name === "google" || name === "groq")
                    ? t("sharedProviderKey")
                    : usable
                      ? t("configured")
                      : info.disabled
                        ? t("disabled")
                        : t("notConfigured")}
              </span>

              {/* Keyless local provider: open dashboard / usage page */}
              {info.keyless && KEYLESS_PROVIDERS.has(name) ? (
                <button className="btn small" onClick={() => openDashboard(name)}>
                  {t("openDashboard")}
                </button>
              ) : null}

              {/* User has stored a per-user key: allow removal */}
              {providerKeys.has(name) ? (
                <button className="btn small danger" onClick={() => removeProviderKey(name)}>
                  {t("removePersonalKey")}
                </button>
              ) : null}

              {/* Key-required, not configured: offer Connect dialog */}
              {needsApiKey(name, info) ? (
                <button
                  className="btn small primary"
                  onClick={() => { setConnectProvider(name); setApiKeyInput(""); }}
                >
                  {name === "google" || name === "groq"
                    ? t("connectPersonalKey")
                    : t("connectProvider")}
                </button>
              ) : null}

              {/* Configured (env key present): existing disconnect / reconnect */}
              {hasProviderConfig ? (
                info.disabled ? (
                  <button className="btn small" onClick={() => toggleProvider(name, false)}>
                    {t("reconnectProvider")}
                  </button>
                ) : (
                  <button className="btn small danger" onClick={() => toggleProvider(name, true)}>
                    {t("disconnectProvider")}
                  </button>
                )
              ) : null}
            </div>
          );
          })}
        </div>

        {/* LM Studio local provider configuration */}
        <h3>{t("lmstudioConfig")}</h3>
        <div className="pref-field">
          <label>{t("lmstudioBaseUrl")}</label>
          <input
            type="text"
            value={settings?.lmstudio_base_url ?? "http://localhost:1234/v1"}
            onChange={(e) => patchSettings(
              { lmstudio_base_url: e.target.value },
              undefined,
            ).then(() => setLmstudioStatus("idle"))}
            placeholder="http://localhost:1234/v1"
          />
        </div>
        <div className="pref-field">
          <label>{t("lmstudioModel")}</label>
          <select
            value={settings?.lmstudio_model ?? ""}
            onChange={(e) => patchSettings(
              { lmstudio_model: e.target.value },
              undefined,
            )}
            disabled={lmstudioModels.length === 0 || lmstudioStatus !== "ok"}
          >
            {lmstudioModels.length > 0 ? (
              lmstudioModels.map((m) => (
                <option key={m.id} value={m.id}>{m.name || m.id}</option>
              ))
            ) : (
              <option value="" disabled>
                {settings?.lmstudio_model || "No models loaded"}
              </option>
            )}
          </select>
        </div>
        <div className="pref-field">
          <label>&nbsp;</label>
          <div className="row gap">
            <button
              className={`btn small ${lmstudioStatus === "ok" ? "primary" : ""}`}
              onClick={reloadLmstudioModels}
              disabled={lmstudioStatus === "loading"}
            >
              {lmstudioStatus === "loading"
                ? "..."
                : lmstudioStatus === "ok"
                  ? t("lmstudioConnected")
                  : t("lmstudioTestConn")}
            </button>
            {lmstudioStatus === "error" ? (
              <span className="status-error">{t("lmstudioNotRunning")}</span>
            ) : null}
            {PROVIDER_DASHBOARD_URLS.lmstudio ? (
              <button className="btn small" onClick={() => openDashboard("lmstudio")}>
                {t("openDashboard")}
              </button>
            ) : null}
          </div>
        </div>

        <h3>{t("autonomy")}</h3>
        <div className="field">
          <select
            id="autonomy-select"
            value={settings ? String(settings.autonomy) : ""}
            onChange={(e) => patchSettings({ autonomy: Number(e.target.value) }, t("autonomyUpdated"))}
            disabled={!settings}
          >
            <option value="0">{t("auto0")}</option>
            <option value="1">{t("auto1")}</option>
            <option value="2">{t("auto2")}</option>
            <option value="3">{t("auto3")}</option>
            <option value="4">{t("auto4")}</option>
          </select>
          <p className="hint">{t("autonomyHint")}</p>
        </div>

        <h3>{t("toolPolicies")}</h3>
        <div id="policy-list" className="provider-list">
          {settings?.tools
            .filter((tool) => tool.permission >= 1 && tool.name !== "update_plan")
            .map((tool) => (
              <div key={tool.name} className="provider-row">
                <div>
                  <div className="name">{tool.name}</div>
                  <div className="model">{tool.description.slice(0, 90)}</div>
                </div>
                <select
                  data-tool={tool.name}
                  defaultValue={settings.tool_policies[tool.name] || ""}
                  onChange={savePolicies}
                >
                  <option value="">{t("polDefault")}</option>
                  <option value="automatic">{t("polAutomatic")}</option>
                  <option value="approval">{t("polApproval")}</option>
                  <option value="denied">{t("polDenied")}</option>
                </select>
              </div>
            ))}
        </div>

        <h3>{t("toolsTitle")}</h3>
        <div className="tool-list">
          {settings?.tools.map((tool) => (
            <span
              key={tool.name}
              className={`tool-pill ${tool.permission >= 3 ? "p3" : ""}`}
              title={tool.description}
            >
              {tool.name} · L{tool.permission}
            </span>
          ))}
        </div>

        <h3>{t("voiceTitle")}</h3>
        <div className="provider-list">
          <div className="provider-row">
            <div>
              <div className="name">{t("voiceEnable")}</div>
              <div className="model">{t("voiceHint")}</div>
            </div>
            <button
              className={`btn small ${settings?.voice.enabled ? "primary" : ""}`}
              onClick={() => setVoice({ enabled: !settings?.voice.enabled })}
            >
              {settings?.voice.enabled ? t("configured") : t("notConfigured")}
            </button>
          </div>
          <div className="provider-row">
            <div><div className="name">{t("voiceAutoSpeak")}</div></div>
            <button
              className={`btn small ${settings?.voice.auto_speak ? "primary" : ""}`}
              onClick={() => setVoice({ auto_speak: !settings?.voice.auto_speak })}
            >
              {settings?.voice.auto_speak ? t("configured") : t("notConfigured")}
            </button>
          </div>
          <div className="provider-row">
            <div><div className="name">{t("voiceContinuous")}</div></div>
            <button
              className={`btn small ${settings?.voice.continuous ? "primary" : ""}`}
              onClick={() => setVoice({ continuous: !settings?.voice.continuous })}
            >
              {settings?.voice.continuous ? t("configured") : t("notConfigured")}
            </button>
          </div>
          <div className="provider-row">
            <div><div className="name">{t("voicePick")}</div></div>
            <select
              value={settings?.voice.voice || ""}
              onChange={(e) => setVoice({ voice: e.target.value })}
            >
              <option value="">{t("voiceDefault")}</option>
              {voices.map((v) => (
                <option key={v.name} value={v.name}>{v.name}</option>
              ))}
            </select>
          </div>
        </div>

        <h3>{t("language")}</h3>
        <div className="row gap">
          <button
            className={`btn small ${locale === "en" ? "primary" : ""}`}
            onClick={() => onLocale("en")}
          >
            English
          </button>
          <button
            className={`btn small ${locale === "sw" ? "primary" : ""}`}
            onClick={() => onLocale("sw")}
          >
            Kiswahili
          </button>
        </div>

        {/* Phase K (§27, §72): preference fields */}
        <h3>{t("preferencesTitle")}</h3>
        <div className="pref-field">
          <label>{t("prefLength")}</label>
          <select
            value={settings ? settings.response_length : ""}
            onChange={(e) => patchSettings({ response_length: e.target.value }, "Preferences saved")}
          >
            <option value="balanced">{t("prefBalanced")}</option>
            <option value="concise">{t("prefConcise")}</option>
            <option value="detailed">{t("prefDetailed")}</option>
          </select>
        </div>
        <div className="pref-field">
          <label>{t("prefTone")}</label>
          <select
            value={settings ? settings.tone : ""}
            onChange={(e) => patchSettings({ tone: e.target.value }, "Preferences saved")}
          >
            <option value="professional">{t("prefProfessional")}</option>
            <option value="friendly">{t("prefFriendly")}</option>
            <option value="casual">{t("prefCasual")}</option>
            <option value="formal">{t("prefFormal")}</option>
          </select>
        </div>
        <div className="pref-field">
          <label>{t("prefTimezone")}</label>
          <input
            type="text"
            value={settings ? settings.timezone : ""}
            onChange={(e) => patchSettings({ timezone: e.target.value }, "Preferences saved")}
            placeholder="e.g. America/New_York"
          />
        </div>
        <div className="pref-field">
          <label>{t("prefProvider")}</label>
          <select
            value={settings ? settings.preferred_provider : ""}
            onChange={(e) => patchSettings({ preferred_provider: e.target.value }, "Preferences saved")}
          >
            <option value="auto">{t("prefAuto")}</option>
            <option value="openai">OpenAI</option>
            <option value="anthropic">Anthropic</option>
            <option value="groq">Groq</option>
            <option value="google">Google</option>
            <option value="deepseek">DeepSeek</option>
            <option value="mistral">Mistral</option>
            <option value="openrouter">OpenRouter</option>
            <option value="lmstudio">LMStudio</option>
            <option value="jan">Jan.ai</option>
            <option value="cohere">Cohere</option>
            <option value="cerebras">Cerebras</option>
            <option value="nvidia">NVIDIA</option>
            <option value="perplexity">Perplexity</option>
            <option value="ollama">Ollama</option>
          </select>
        </div>
      {/* Appearance — theme toggle (spec §settings-audit) */}
        <h3>{t("appearanceTitle")}</h3>
        <div className="provider-list">
          <div className="provider-row">
            <div>
              <div className="name">{t("appearanceTheme")}</div>
              <div className="model">{t("themeToggle")}</div>
            </div>
            <button
              className={`btn small ${theme === "dark" ? "primary" : ""}`}
              onClick={toggleTheme}
            >
              {theme === "dark" ? t("themeDark") : t("themeLight")}
            </button>
          </div>
        </div>

        {/* RAM & Resources — system health info from /api/health */}
        <h3>{t("ramResourcesTitle")}</h3>
        <div className="provider-list">
          {healthData ? (
            (() => {
              const ram = (healthData.ram_info as Record<string, unknown> | undefined) ?? {};
              const free = ram.free_gb as number | undefined;
              const total = ram.total_gb as number | undefined;
              return (
                <>
                  <div className="provider-row">
                    <div>
                      <div className="name">
                        {free !== undefined && total !== undefined
                          ? t("ramStatus").replace("{free}", String(free)).replace("{total}", String(total))
                          : t("ramIndicator")}
                      </div>
                      <div className="model">
                        {ram.low_ram ? t("ramWarning") : ""}
                      </div>
                    </div>
                    <span className={`badge ${ram.low_ram ? "warn" : "ok"}`}>
                      {ram.low_ram ? "low" : "ok"}
                    </span>
                  </div>
                  {ram.tier !== undefined ? (
                    <div className="provider-row">
                      <div>
                        <div className="name">{t("ramTier").replace("{tier}", String(ram.tier))}</div>
                        <div className="model">{t("ramThresholdHint")}</div>
                      </div>
                    </div>
                  ) : null}
                </>
              );
            })()
          ) : (
            <div className="provider-row">
              <div><div className="name">{t("ramIndicator")}</div></div>
            </div>
          )}
        </div>

        {/* Notifications — spec §settings-audit */}
        <h3>{t("notificationsSettings")}</h3>
        <div className="pref-field">
          <label>{t("notifDesktop")}</label>
          <select
            value={settings?.notifications_enabled ? "on" : "off"}
            disabled={!settings}
            onChange={async (e) => {
              const enabled = e.target.value === "on";
              if (enabled) {
                if (!("Notification" in window)) {
                  onToast(t("notificationUnsupported"), true);
                  return;
                }
                try {
                  const permission = await window.Notification.requestPermission();
                  if (permission !== "granted") {
                    onToast(t("notificationPermissionDenied"), true);
                    return;
                  }
                } catch (err) {
                  onToast(
                    `${t("notificationPermissionDenied")} ${(err as Error).message}`,
                    true,
                  );
                  return;
                }
              }
              await patchSettings(
                { notifications_enabled: enabled },
                t("preferencesSaved"),
              );
            }}
          >
            <option value="on">{t("enabled")}</option>
            <option value="off">{t("disabled")}</option>
          </select>
        </div>
        <div className="pref-field">
          <label>{t("notifSound")}</label>
          <select
            value={settings?.notification_sound ? "on" : "off"}
            disabled={!settings}
            onChange={(e) => patchSettings({ notification_sound: e.target.value === "on" }, t("preferencesSaved"))}
          >
            <option value="on">{t("enabled")}</option>
            <option value="off">{t("disabled")}</option>
          </select>
        </div>
        <div className="pref-field">
          <label>{t("notifSchedule")}</label>
          <select
            value={settings?.schedule_alerts ? "on" : "off"}
            disabled={!settings}
            onChange={(e) => patchSettings({ schedule_alerts: e.target.value === "on" }, t("preferencesSaved"))}
          >
            <option value="on">{t("enabled")}</option>
            <option value="off">{t("disabled")}</option>
          </select>
        </div>
        <div className="pref-field">
          <label>{t("notifApproval")}</label>
          <select
            value={settings?.approval_alerts ? "on" : "off"}
            disabled={!settings}
            onChange={(e) => patchSettings({ approval_alerts: e.target.value === "on" }, t("preferencesSaved"))}
          >
            <option value="on">{t("enabled")}</option>
            <option value="off">{t("disabled")}</option>
          </select>
        </div>

        {/* Privacy — spec §settings-audit */}
        <h3>{t("privacyTitle")}</h3>
        <div className="provider-list">
          <div className="provider-row">
            <div>
              <div className="name">{t("privacyDataExport")}</div>
              <div className="model">{t("privacyKeyStorage")}</div>
            </div>
            <button className="btn small" onClick={() => onToast(t("somethingWrong"))}>
              {t("exportData")}
            </button>
          </div>
          <div className="provider-row">
            <div>
              <div className="name">{t("prefMemory")}</div>
              <div className="model">{t("prefMemoryPersist")}</div>
            </div>
            <button className="btn small" onClick={() => onToast(t("somethingWrong"))}>
              {t("clearAction")}
            </button>
          </div>
        </div>
      </div>

      {/* Connect dialog — per-user API key entry */}
      {connectProvider && (
        <div className="modal-backdrop" onClick={() => setConnectProvider(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h3>{t("connectToProvider").replace("{provider}", connectProvider)}</h3>
            <p>{t("apiKeyRequired")}</p>
            {(connectProvider === "google" || connectProvider === "groq")
              && providers[connectProvider]?.configured ? (
                <p className="hint">{t("providerKeyFallback")}</p>
              ) : null}
            {PROVIDER_DASHBOARD_URLS[connectProvider] ? (
              <p>
                <a
                  href={PROVIDER_DASHBOARD_URLS[connectProvider]}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  {t("getApiKey")}
                </a>
              </p>
            ) : null}
            <input
              type="password"
              value={apiKeyInput}
              onChange={(e) => setApiKeyInput(e.target.value)}
              placeholder={t("apiKeyPlaceholder")}
              className="api-key-input"
            />
            <p className="hint">{t("keyStored")}</p>
            <div className="row gap">
              <button className="btn" onClick={() => setConnectProvider(null)}>
                {t("cancel")}
              </button>
              <button
                className="btn primary"
                onClick={() => connectProviderKey(connectProvider)}
              >
                {t("save")}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
