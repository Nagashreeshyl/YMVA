"use client";

export type LiveEvent =
  | { type: "call"; call_id: string; version: number | null }
  | { type: "error"; content: string }
  | { type: "clear" }
  | { type: "state"; state: "listening" | "user_speaking" | "thinking" | "using_tool" | "agent_speaking" }
  | { type: "partial"; content: string; final: boolean }
  | {
      type: "user_turn" | "agent_turn" | "language_switch" | "status" | "tool_call";
      content: string;
      language: string | null;
      [key: string]: unknown;
    };

export interface BrowserCallHandlers {
  onEvent: (event: LiveEvent) => void;
  onLevel: (level: number) => void;
  onAgentSpeaking: (speaking: boolean) => void;
  onClose: (reason: string | null) => void;
}

function wsUrl(path: string): string {
  const { protocol, hostname, port, host } = window.location;
  const scheme = protocol === "https:" ? "wss" : "ws";
  // `next dev` doesn't proxy WebSockets, so talk to the API directly in development.
  const target = port === "3000" ? `${hostname}:8000` : host;
  return `${scheme}://${target}${path}`;
}

/** Microphone + speaker session with an agent over a WebSocket. Call stop() to hang up. */
export async function startBrowserCall(
  agentId: string,
  versionId: string | null,
  variables: Record<string, string>,
  handlers: BrowserCallHandlers,
): Promise<{ stop: () => void }> {
  const params = new URLSearchParams({ agent_id: agentId });
  if (versionId) params.set("version_id", versionId);
  for (const [k, v] of Object.entries(variables)) if (v) params.set(`var_${k}`, v);
  return startTalk(`/api/calls/web?${params}`, handlers);
}

/** The same session at any call WebSocket, e.g. the agent builder's interview. */
export async function startTalk(path: string, handlers: BrowserCallHandlers): Promise<{ stop: () => void }> {
  // Create and resume the audio context first, while we're still inside the click: browsers
  // keep audio created later (e.g. after the microphone permission prompt) muted.
  const context = new AudioContext({ latencyHint: "interactive" });
  const resumed = context.resume();
  let stream: MediaStream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
    });
  } catch (err) {
    void context.close();
    throw err;
  }
  await resumed;
  await context.audioWorklet.addModule("/audio-worklets.js");
  const mic = new AudioWorkletNode(context, "mic-capture");
  const player = new AudioWorkletNode(context, "pcm-player");
  context.createMediaStreamSource(stream).connect(mic);
  player.connect(context.destination);

  const ws = new WebSocket(wsUrl(path));
  ws.binaryType = "arraybuffer";
  let closed = false;

  mic.port.onmessage = (e) => {
    if (e.data.pcm && ws.readyState === WebSocket.OPEN) ws.send(e.data.pcm);
    if (typeof e.data.level === "number") handlers.onLevel(e.data.level);
  };
  player.port.onmessage = (e) => {
    if (typeof e.data.playing === "boolean") handlers.onAgentSpeaking(e.data.playing);
  };

  const cleanup = (reason: string | null) => {
    if (closed) return;
    closed = true;
    stream.getTracks().forEach((t) => t.stop());
    void context.close();
    handlers.onClose(reason);
  };

  ws.onmessage = (e) => {
    if (typeof e.data !== "string") {
      player.port.postMessage(e.data, [e.data]);
      return;
    }
    const event = JSON.parse(e.data) as LiveEvent;
    if (event.type === "clear") player.port.postMessage("clear");
    else handlers.onEvent(event);
  };
  ws.onclose = (e) => cleanup(e.code === 1008 ? "Not allowed. Sign in as an admin and try again." : null);
  ws.onerror = () => cleanup("Lost connection to the server.");

  return {
    stop: () => {
      if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) ws.close(1000);
      cleanup(null);
    },
  };
}
