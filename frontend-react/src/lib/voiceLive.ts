/* Phase 1: live in-app voice call client.
 *
 * Streams microphone audio up a WebSocket as raw PCM16 @ 16 kHz mono and plays
 * synthesised PCM16 frames (server-side Piper/OpenAI TTS) back as they arrive.
 * All language understanding, tools and TTS happen server-side; this module is
 * only capture/transport/playback so it stays transport-agnostic.
 *
 * The server speaks a small JSON protocol plus binary audio:
 *   out:  ready, user, agent, tool_calls, tool_result, needs_confirmation,
 *         provider_fallback, audio, audio_end, ended, error
 *   in :  { type: "start" | "end" | "text" | "confirm" | "hangup", ... }
 */
import { voiceLiveUrl } from "./api";

export interface LiveEvent {
  type: string;
  [key: string]: unknown;
}

export interface LiveCallHandlers {
  onEvent?: (event: LiveEvent) => void;
  onMicLevel?: (level: number) => void;
  onClose?: (reason: string) => void;
  onError?: (message: string) => void;
}

function float32ToInt16(input: Float32Array): Int16Array {
  const out = new Int16Array(input.length);
  for (let i = 0; i < input.length; i += 1) {
    const s = Math.max(-1, Math.min(1, input[i]));
    out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  return out;
}

function downsample(input: Float32Array, from: number, to: number): Float32Array {
  if (to === from) return input;
  const ratio = from / to;
  const length = Math.max(1, Math.round(input.length / ratio));
  const out = new Float32Array(length);
  let i = 0;
  for (let o = 0; o < length; o += 1) {
    const next = Math.round((o + 1) * ratio);
    let sum = 0;
    let count = 0;
    for (; i < next && i < input.length; i += 1) {
      sum += input[i];
      count += 1;
    }
    out[o] = count ? sum / count : 0;
  }
  return out;
}

function base64ToInt16(b64: string): Int16Array {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i += 1) bytes[i] = bin.charCodeAt(i);
  const count = Math.floor(bytes.length / 2);
  return new Int16Array(bytes.buffer, bytes.byteOffset, count);
}

interface AudioWindow {
  webkitAudioContext?: typeof AudioContext;
}

export class LiveVoiceCall {
  private ws: WebSocket | null = null;
  private handlers: LiveCallHandlers;
  private micCtx: AudioContext | null = null;
  private playbackCtx: AudioContext | null = null;
  private stream: MediaStream | null = null;
  private source: MediaStreamAudioSourceNode | null = null;
  private processor: ScriptProcessorNode | null = null;
  private sink: GainNode | null = null;
  private playTime = 0;
  private closed = false;
  private purpose: string;
  private conversationId: string | null;
  private callId: string | null;
  private pendingApprovalId: string | null = null;
  private sampleRate = 16000;
  private muted = false;

  constructor(
    purpose: string,
    handlers: LiveCallHandlers = {},
    conversationId?: string | null,
    callId?: string | null,
  ) {
    this.purpose = purpose;
    this.handlers = handlers;
    this.conversationId = conversationId || null;
    this.callId = callId || null;
  }

  get isClosed(): boolean {
    return this.closed;
  }

  get currentCallId(): string | null {
    return this.callId;
  }

  async connect(): Promise<void> {
    const url = voiceLiveUrl(this.conversationId, this.callId);
    const ws = new WebSocket(url);
    ws.binaryType = "arraybuffer";
    this.ws = ws;
    await new Promise<void>((resolve, reject) => {
      ws.onopen = () => resolve();
      ws.onerror = () => reject(new Error("Could not reach the voice server."));
    });
    ws.onmessage = (event) => this.handleMessage(event);
    ws.onclose = () => this.handleClose("socket closed");
    await this.startMic();
    this.send({ type: "start", purpose: this.purpose });
  }

  /** Finish the current push-to-talk utterance (VAD also auto-finishes). */
  finishUtterance(): void {
    this.send({ type: "end" });
  }

  /** Send typed text as if the user had spoken it. */
  sendText(text: string): void {
    if (text.trim()) this.send({ type: "text", text });
  }

  /** Approve or decline a pending tool confirmation. */
  confirm(yes: boolean): void {
    if (!this.pendingApprovalId) return;
    this.send({
      type: "confirm",
      yes,
      approval_id: this.pendingApprovalId,
    });
  }

  setMuted(muted: boolean): void {
    this.muted = muted;
    if (this.stream) {
      this.stream.getAudioTracks().forEach((t) => (t.enabled = !muted));
    }
  }

  hangup(): void {
    this.send({ type: "hangup" });
    this.close("hangup");
  }

  close(reason = "closed"): void {
    if (this.closed) return;
    this.closed = true;
    if (this.processor) {
      this.processor.onaudioprocess = null;
      try {
        this.processor.disconnect();
      } catch {
        /* already detached */
      }
    }
    if (this.source) {
      try {
        this.source.disconnect();
      } catch {
        /* already detached */
      }
    }
    if (this.sink) {
      try {
        this.sink.disconnect();
      } catch {
        /* already detached */
      }
    }
    this.stream?.getTracks().forEach((t) => t.stop());
    void this.micCtx?.close().catch(() => undefined);
    void this.playbackCtx?.close().catch(() => undefined);
    this.micCtx = this.playbackCtx = null;
    this.stream = null;
    if (this.ws && this.ws.readyState <= WebSocket.OPEN) {
      try {
        this.ws.close(1000, reason);
      } catch {
        /* already closing */
      }
    }
    this.handlers.onClose?.(reason);
  }

  private send(payload: Record<string, unknown>): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(payload));
    }
  }

  private async startMic(): Promise<void> {
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: true,
        noiseSuppression: true,
        channelCount: 1,
      },
    });
    this.stream = stream;
    const Ctor =
      window.AudioContext || (window as unknown as AudioWindow).webkitAudioContext;
    const ctx = new Ctor();
    this.micCtx = ctx;
    this.playbackCtx = ctx;
    this.source = ctx.createMediaStreamSource(stream);
    this.processor = ctx.createScriptProcessor(4096, 1, 1);
    this.sink = ctx.createGain();
    this.sink.gain.value = 0; // keep the graph alive without echoing the mic
    this.processor.onaudioprocess = (event) => {
      if (this.muted || this.ws?.readyState !== WebSocket.OPEN) return;
      const channel = event.inputBuffer.getChannelData(0);
      const level = Math.min(1, Math.abs(channel[1] ?? 0));
      this.handlers.onMicLevel?.(level);
      const pcm = float32ToInt16(downsample(channel, ctx.sampleRate, this.sampleRate));
      this.ws.send(pcm.buffer);
    };
    this.source.connect(this.processor);
    this.processor.connect(this.sink);
    this.sink.connect(ctx.destination);
  }

  private handleMessage(event: MessageEvent): void {
    if (typeof event.data !== "string") return;
    let payload: LiveEvent;
    try {
      payload = JSON.parse(event.data) as LiveEvent;
    } catch {
      return;
    }
    if (payload.type === "ready" && typeof payload.sample_rate === "number") {
      this.sampleRate = payload.sample_rate;
    }
    if (payload.type === "ready") {
      if (typeof payload.call_id === "string") this.callId = payload.call_id;
      if (typeof payload.conversation_id === "string") {
        this.conversationId = payload.conversation_id;
      }
    }
    if (payload.type === "needs_confirmation") {
      this.pendingApprovalId =
        typeof payload.approval_id === "string" ? payload.approval_id : null;
    } else if (payload.type === "approval_state") {
      this.pendingApprovalId = null;
    } else if (
      payload.type === "task_state"
      && payload.status !== "paused_approval"
    ) {
      this.pendingApprovalId = null;
    }
    if (payload.type === "audio" && typeof payload.data === "string") {
      this.enqueueAudio(payload.data, Number(payload.sample_rate) || 22050);
      return;
    }
    if (payload.type === "error") {
      this.handlers.onError?.(String(payload.detail ?? payload.code ?? "voice error"));
    }
    this.handlers.onEvent?.(payload);
  }

  private enqueueAudio(b64: string, rate: number): void {
    const ctx = this.playbackCtx;
    if (!ctx) return;
    const samples = base64ToInt16(b64);
    if (!samples.length) return;
    const floats = new Float32Array(samples.length);
    for (let i = 0; i < samples.length; i += 1) floats[i] = samples[i] / 32768;
    const buffer = ctx.createBuffer(1, floats.length, rate);
    buffer.copyToChannel(floats, 0);
    const node = ctx.createBufferSource();
    node.buffer = buffer;
    node.connect(ctx.destination);
    const now = ctx.currentTime;
    if (this.playTime < now) this.playTime = now + 0.05;
    node.start(this.playTime);
    this.playTime += buffer.duration;
  }

  private handleClose(reason: string): void {
    if (!this.closed) {
      this.closed = true;
      this.handlers.onClose?.(reason);
    }
  }
}
