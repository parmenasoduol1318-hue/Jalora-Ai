/* Phase H (§46-48): browser voice helpers.
 *
 * - Speech input uses the Web Speech API where available (Chrome/Edge/Safari).
 * - Where it isn't (Firefox), we fall back to recording a clip with
 *   MediaRecorder and sending it to /api/voice/transcribe on the backend.
 * - Speech output uses the browser's speechSynthesis (no server round-trip).
 *
 * The Web Speech API is not in every lib.dom, so minimal structural typings are
 * declared here instead of relying on the (missing) global types.
 */

export interface SpeechRecognitionAlternativeLike {
  transcript: string;
}

export interface SpeechRecognitionResultLike {
  isFinal: boolean;
  length: number;
  [index: number]: SpeechRecognitionAlternativeLike;
}

export interface SpeechRecognitionResultListLike {
  length: number;
  [index: number]: SpeechRecognitionResultLike;
}

export interface SpeechRecognitionEventLike {
  resultIndex: number;
  results: SpeechRecognitionResultListLike;
}

export interface SpeechRecognitionErrorEventLike {
  error: string;
  message?: string;
}

export interface SpeechRecognitionLike {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  maxAlternatives: number;
  start(): void;
  stop(): void;
  abort(): void;
  onresult: ((event: SpeechRecognitionEventLike) => void) | null;
  onerror: ((event: SpeechRecognitionErrorEventLike) => void) | null;
  onend: (() => void) | null;
  onstart: (() => void) | null;
}

type RecognitionCtor = new () => SpeechRecognitionLike;

interface SpeechWindow {
  SpeechRecognition?: RecognitionCtor;
  webkitSpeechRecognition?: RecognitionCtor;
}

/** True when the browser can transcribe speech on-device. */
export function speechInputSupported(): boolean {
  const w = window as unknown as SpeechWindow;
  return Boolean(w.SpeechRecognition || w.webkitSpeechRecognition);
}

/** Create a fresh recognizer, or null when unsupported. */
export function createRecognizer(): SpeechRecognitionLike | null {
  if (typeof window === "undefined") return null;
  const w = window as unknown as SpeechWindow;
  const Ctor = w.SpeechRecognition || w.webkitSpeechRecognition;
  if (!Ctor) return null;
  const recognition = new Ctor();
  recognition.continuous = false;
  recognition.interimResults = true;
  recognition.maxAlternatives = 1;
  return recognition;
}

/** Flatten a recognizer event into (finalText, interimText). */
export function readRecognition(
  event: SpeechRecognitionEventLike,
): { finalText: string; interimText: string } {
  let finalText = "";
  let interimText = "";
  for (let i = event.resultIndex; i < event.results.length; i += 1) {
    const result = event.results[i];
    const alt = result[0];
    if (!alt) continue;
    if (result.isFinal) finalText += alt.transcript;
    else interimText += alt.transcript;
  }
  return { finalText, interimText };
}

/* ---- text to speech ------------------------------------------------------ */

export function ttsSupported(): boolean {
  return typeof window !== "undefined" && "speechSynthesis" in window;
}

export function listVoices(): SpeechSynthesisVoice[] {
  return ttsSupported() ? window.speechSynthesis.getVoices() : [];
}

/** Speak `text`; resolves when playback finishes (or immediately if muted). */
export function speak(text: string, voiceName = ""): Promise<void> {
  return new Promise((resolve) => {
    if (!ttsSupported() || !text.trim()) {
      resolve();
      return;
    }
    const synth = window.speechSynthesis;
    synth.cancel();
    const utterance = new SpeechSynthesisUtterance(text);
    if (voiceName) {
      const match = synth.getVoices().find((v) => v.name === voiceName);
      if (match) utterance.voice = match;
    }
    let settled = false;
    const finish = () => {
      if (settled) return;
      settled = true;
      resolve();
    };
    utterance.onend = finish;
    utterance.onerror = finish;
    synth.speak(utterance);
  });
}

export function stopSpeaking(): void {
  if (ttsSupported()) window.speechSynthesis.cancel();
}

/* ---- recording fallback -------------------------------------------------- */

export interface ClipRecorder {
  stop: () => Promise<Blob | null>;
  cancel: () => void;
}

/**
 * Start recording a clip with MediaRecorder. Returns a handle whose `stop()`
 * resolves with the recorded Blob (or null). Returns null when recording is
 * unavailable or the user denies the microphone.
 */
export async function recordUntilStopped(): Promise<ClipRecorder | null> {
  if (
    typeof navigator === "undefined" ||
    !navigator.mediaDevices?.getUserMedia ||
    typeof MediaRecorder === "undefined"
  ) {
    return null;
  }
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  const recorder = new MediaRecorder(stream);
  const chunks: BlobPart[] = [];
  recorder.ondataavailable = (event) => {
    if (event.data && event.data.size > 0) chunks.push(event.data);
  };
  recorder.start();

  const releaseTracks = () => stream.getTracks().forEach((track) => track.stop());
  let stopped = false;

  const finish = () =>
    new Promise<Blob | null>((resolve) => {
      recorder.onstop = () => {
        releaseTracks();
        resolve(
          chunks.length
            ? new Blob(chunks, { type: recorder.mimeType || "audio/webm" })
            : null,
        );
      };
      recorder.stop();
    });

  return {
    stop: () => {
      if (stopped) return Promise.resolve(null);
      stopped = true;
      return finish();
    },
    cancel: () => {
      if (stopped) return;
      stopped = true;
      recorder.onstop = releaseTracks;
      try {
        recorder.stop();
      } catch {
        releaseTracks();
      }
    },
  };
}
