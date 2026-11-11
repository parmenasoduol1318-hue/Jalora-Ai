import { ApiError, getToken } from "./api";

/** A single parsed SSE event (the JSON object the backend streams). */
export type AgentEvent = Record<string, unknown>;

/**
 * POST to an SSE endpoint and invoke `onEvent` for every `data:` frame.
 *
 * Shared by the chat view and the coding workspace so both speak the exact same
 * wire format (`event: <type>\ndata: <json>\n\n`). Throws {@link ApiError} for
 * non-2xx responses and lets `AbortError` propagate so callers can distinguish
 * a user stop from a real failure.
 */
export async function streamAgent(
  path: string,
  body: unknown,
  onEvent: (event: AgentEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const resp = await fetch(path, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${getToken()}`,
    },
    body: JSON.stringify(body),
    signal,
  });
  if (!resp.ok) {
    const data = (await resp.json().catch(() => ({}))) as {
      error?: { code?: string; details?: string };
    };
    throw new ApiError(
      data.error?.code || "ERROR",
      data.error?.details || "Request failed",
      resp.status,
    );
  }
  if (!resp.body) {
    throw new ApiError("EMPTY_RESPONSE", "Server returned no response body", resp.status);
  }
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  const dispatch = (block: string) => {
    const dataLine = block.split("\n").find((line) => line.startsWith("data:"));
    if (!dataLine) return;
    try {
      onEvent(JSON.parse(dataLine.slice(5).trim()) as AgentEvent);
    } catch {
      onEvent({
        type: "error",
        error: { code: "MALFORMED_SSE", details: "Received malformed server event." },
      });
    }
  };
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx: number;
    while ((idx = buffer.indexOf("\n\n")) >= 0) {
      const block = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      dispatch(block);
    }
  }
  buffer += decoder.decode();
  if (buffer.trim()) dispatch(buffer);
}
