import { useEffect, useRef, useState } from "react";
import { LiveVoiceCall, type LiveEvent } from "../lib/voiceLive";

interface Turn {
  who: "user" | "agent" | "tool";
  text: string;
}

/* Phase 1: start and drive a live in-app voice call with the assistant. */
export function LiveCallView({
  onToast,
}: {
  t?: (k: string) => string;
  onToast: (m: string, e?: boolean) => void;
}) {
  const callRef = useRef<LiveVoiceCall | null>(null);
  const [active, setActive] = useState(false);
  const [status, setStatus] = useState("");
  const [levels, setLevels] = useState(0);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [pending, setPending] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [ended, setEnded] = useState<string | null>(null);
  const [lastCallId, setLastCallId] = useState<string | null>(null);

  useEffect(() => () => callRef.current?.close("unmount"), []);

  const pushTurn = (who: Turn["who"], text: string) =>
    setTurns((prev) => [...prev, { who, text }]);

  const handleEvent = (event: LiveEvent) => {
    switch (event.type) {
      case "ready":
        if (typeof event.call_id === "string") setLastCallId(event.call_id);
        setStatus("Setting up microphone...");
        break;
      case "speech_started":
        setStatus("You're speaking");
        break;
      case "transcribing":
        setStatus("Processing your speech...");
        break;
      case "thinking":
        setStatus("Jalora is thinking...");
        break;
      case "listening":
        setStatus("Listening for your next turn");
        break;
      case "stt_empty":
        setStatus("I didn't catch that — please try again");
        break;
      case "user":
        pushTurn("user", String(event.text));
        setStatus("Jalora is thinking...");
        break;
      case "agent":
        pushTurn("agent", String(event.text));
        setStatus("Jalora is responding...");
        break;
      case "greeting":
        pushTurn("agent", String(event.text));
        setStatus("Jalora is speaking...");
        break;
      case "tool_result":
        pushTurn("tool", "tool: " + String(event.name ?? ""));
        break;
      case "provider_fallback":
        setStatus("switching provider (" + String(event.provider ?? "") + ")");
        break;
      case "needs_confirmation":
        setPending(String(event.question ?? "Should I go ahead?"));
        setStatus("Waiting for your approval");
        break;
      case "ended":
        if (typeof event.call_id === "string") setLastCallId(event.call_id);
        setEnded(String(event.summary ?? "Call ended."));
        setActive(false);
        setStatus("");
        break;
      default:
        break;
    }
  };

  const start = async (callId?: string | null) => {
    setEnded(null);
    setTurns([]);
    setPending(null);
    const call = new LiveVoiceCall("a natural two-way conversation", {
      onEvent: handleEvent,
      onMicLevel: (level) => setLevels(level),
      onPlaybackState: (state) => {
        setStatus(state === "speaking"
          ? "Jalora is speaking..."
          : "Listening for your next turn");
      },
      onClose: () => {
        setActive(false);
        setStatus("");
        if (callRef.current?.currentCallId) {
          setLastCallId(callRef.current.currentCallId);
        }
        callRef.current = null;
      },
      onError: (m) => onToast(m, true),
    }, null, callId);
    callRef.current = call;
    setStatus("connecting...");
    try {
      await call.connect();
      setActive(true);
    } catch (err) {
      call.close("failed");
      callRef.current = null;
      onToast(err instanceof Error ? err.message : "Could not start the call.", true);
    }
  };

  const hangup = () => {
    callRef.current?.hangup();
    callRef.current = null;
    setActive(false);
    setStatus("");
    setPending(null);
  };

  const confirmPending = (yes: boolean) => {
    callRef.current?.confirm(yes);
    setPending(null);
  };

  const sendDraft = () => {
    const text = draft.trim();
    if (!text) return;
    callRef.current?.sendText(text);
    setDraft("");
  };

  return (
    <div className="call-panel in_progress" style={{ marginBottom: 16 }}>
      <div className="call-head">
        <span className="call-phone">🎙 Live call</span>
        {status &&         <span className="call-purpose" aria-live="polite">{status}</span>}
        {active ? (
          <>
            <button className="btn small" onClick={() => callRef.current?.finishUtterance()}>
              Done talking
            </button>
            <button className="btn small danger" onClick={hangup}>
              Hang up
            </button>
          </>
        ) : (
          <>
            <button
              className="btn small primary"
              onClick={() => void start(lastCallId)}
            >
              {lastCallId ? "Reconnect voice session" : "Start live call"}
            </button>
            {lastCallId && (
              <button className="btn small" onClick={() => void start(null)}>
                New call
              </button>
            )}
          </>
        )}
        {active && (
          <span className="call-mock" title="microphone level">
            {"▁▂▃▄▅▆▇"[Math.min(6, Math.floor(levels * 7))] ?? "▁"}
          </span>
        )}
      </div>

      {pending && (
        <div className="error-bubble" style={{ marginTop: 8 }}>
          {pending}
          <div className="row gap" style={{ marginTop: 8 }}>
            <button className="btn small primary" onClick={() => confirmPending(true)}>
              Yes, go ahead
            </button>
            <button className="btn small" onClick={() => confirmPending(false)}>
              No
            </button>
          </div>
        </div>
      )}

      {ended && <div className="call-summary">{ended}</div>}

      {active && (
        <div className="row gap" style={{ marginTop: 8 }}>
          <input
            value={draft}
            placeholder="Or type a message..."
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") sendDraft();
            }}
            style={{ flex: 1 }}
          />
          <button className="btn small" onClick={sendDraft}>
            Send
          </button>
        </div>
      )}

      {turns.length > 0 && (
        <div className={`call-transcript${active ? " live" : ""}`}>
          {turns.map((turn, i) => (
            <div key={i} className={`turn ${turn.who}`}>
              <span className="turn-who">
                {turn.who === "agent" ? "◆" : turn.who === "user" ? "🧑" : "🔧"}
              </span>
              <span className="turn-text">{turn.text}</span>
            </div>
          ))}
          {active && <span className="turn-live" />}
        </div>
      )}
    </div>
  );
}

export default LiveCallView;
