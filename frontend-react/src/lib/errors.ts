import { ApiError } from "./api";

/* Friendly, translatable notifications for failures. Backend error codes are
   mapped to i18n keys so failed tasks never show raw system messages. */
const FRIENDLY_CODE: Record<string, string> = {
  NO_TOKEN: "errAuthRequired",
  UNAUTHORIZED: "errAuthRequired",
  TOKEN_INVALID: "errAuthRequired",
  TOKEN_EXPIRED: "errAuthExpired",
  USER_INACTIVE: "errAuth",
  INVALID_CREDENTIALS: "errAuth",
  MODEL_AUTH_FAILED: "errAuth",
  MODEL_RATE_LIMITED: "errRateLimit",
  MODEL_UNREACHABLE: "errUnreachable",
  MODEL_EMPTY_RESPONSE: "errEmpty",
  // MODEL_HTTP_ERROR and MODEL_ERROR are intentionally NOT in FRIENDLY_CODE so
  // that friendlyError() falls through to the server-provided `details` field,
  // which is already user-friendly (e.g. "The AI provider couldn't process the
  // request. Please try again.").  This gives users a more specific message than
  // the generic "Something went wrong" translation fallback.
  MODEL_NOT_CONFIGURED: "errNotConfigured",
  // INTERNAL_ERROR is also NOT mapped — the server crafts a clean, non-technical
  // details string (the exception type stays only in the server-side debug log).
  RESUME_FAILED: "errGeneric",
  TASK_TIMEOUT: "errTimeout",
  // Local-provider-specific codes (Ollama / LM Studio / Jan)
  OLLAMA_OUT_OF_MEMORY: "errOom",
  OLLAMA_MODEL_MISSING: "errModelMissing",
  OLLAMA_OFFLINE: "errLocalOffline",
  OLLAMA_ERROR: "errLocalGeneric",
  LMSTUDIO_OUT_OF_MEMORY: "errOom",
  LMSTUDIO_MODEL_MISSING: "errModelMissing",
  LMSTUDIO_OFFLINE: "errLmstudioOffline",
  LMSTUDIO_ERROR: "errLmstudioError",
  JAN_OUT_OF_MEMORY: "errOom",
  JAN_MODEL_MISSING: "errModelMissing",
  JAN_OFFLINE: "errLocalOffline",
  JAN_ERROR: "errLocalGeneric",
  // LoRA-specific codes (local fine-tuned Jalora adapter)
  LORA_OUT_OF_MEMORY: "errOom",
  LORA_MODEL_MISSING: "errModelMissing",
  LORA_OFFLINE: "errLocalOffline",
  LORA_ERROR: "errLocalGeneric",
  LORA_NOT_ENABLED: "errNotConfigured",
};

const RAW_TECHNICAL = /(?:^|\s)(?:[A-Za-z_]*Error|[A-Za-z_]*Exception)\b|Traceback|File "[^"]*", line \d+/;

/** Turn an error code/detail pair into a short, human, localized message. */
export function friendlyError(
  code: string | undefined,
  details: string | undefined,
  t: (k: string) => string,
): string {
  if (code && FRIENDLY_CODE[code]) return t(FRIENDLY_CODE[code]);
  const msg = (details || "").trim();
  if (msg && msg.length <= 200 && !RAW_TECHNICAL.test(msg)) return msg;
  return t("errGeneric");
}

/** Convenience wrapper for caught `ApiError`s / generic errors. */
export function errorMessage(err: unknown, t: (k: string) => string): string {
  if (err instanceof ApiError) return friendlyError(err.code, err.message, t);
  const msg = err instanceof Error ? err.message : String(err);
  return friendlyError(undefined, msg, t);
}
