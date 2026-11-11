/**
 * Demo-only live monitor transport. Captures the microphone with browser DSP disabled,
 * resamples to 8 kHz mono PCM16 and sends exactly one 1,024-byte frame every 64 ms over WS /live.
 * Browsers cannot set custom WebSocket headers, so the proxy must add `x-sample-rate: 8000`
 * and the telephony VAD preset on its upstream Gnani connection (see docs/proxy-contract.md).
 */
import { makeLiveSocketUrl, parseLiveMessage, type LiveMessage } from "./bharatnoise-api";
import {
  LIVE_CANDIDATES, LIVE_FRAME_BYTES, LIVE_FRAME_INTERVAL_MS, LIVE_SAMPLE_RATE, Pcm16Resampler, PcmFrameQueue,
} from "./pcm-framer";

export type LiveStats = { framesSent: number; bufferedFrames: number; droppedFrames: number; levelDb: number };

export type LiveHandlers = {
  onOpen: () => void;
  onMessage: (message: LiveMessage) => void;
  onStats: (stats: LiveStats) => void;
  onClose: (reason?: string) => void | undefined;
};

export type LiveSession = { stop: () => void };

export async function startLiveStream(baseUrl: string, handlers: LiveHandlers): Promise<LiveSession> {
  if (!navigator.mediaDevices?.getUserMedia) throw new Error("Microphone access is not available in this browser.");
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: { channelCount: 1, echoCancellation: false, noiseSuppression: false, autoGainControl: false },
  });
  const context = new AudioContext();
  const source = context.createMediaStreamSource(stream);
  const processor = context.createScriptProcessor(4096, 1, 1);
  const mute = context.createGain();
  mute.gain.value = 0;
  const resampler = new Pcm16Resampler(context.sampleRate, LIVE_SAMPLE_RATE);
  const queue = new PcmFrameQueue(32);
  const socket = new WebSocket(makeLiveSocketUrl(baseUrl));
  socket.binaryType = "arraybuffer";

  let framesSent = 0;
  let levelDb = -120;
  let stopped = false;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let tickStart = 0;
  let tick = 0;

  const report = () => handlers.onStats({ framesSent, bufferedFrames: queue.bufferedFrames, droppedFrames: queue.dropped, levelDb });

  const pace = () => {
    if (stopped) return;
    if (socket.readyState === WebSocket.OPEN) {
      const frame = queue.takeFrame();
      if (frame && frame.byteLength === LIVE_FRAME_BYTES) {
        socket.send(frame);
        framesSent += 1;
      }
    }
    if (tick % 8 === 0) report();
    tick += 1;
    const next = tickStart + tick * LIVE_FRAME_INTERVAL_MS;
    timer = setTimeout(pace, Math.max(0, next - performance.now()));
  };

  processor.onaudioprocess = (event) => {
    const input = event.inputBuffer.getChannelData(0);
    let sum = 0;
    for (let i = 0; i < input.length; i += 1) sum += (input[i] ?? 0) ** 2;
    levelDb = 10 * Math.log10(sum / input.length + 1e-12);
    queue.push(resampler.process(input));
  };

  const cleanup = (reason?: string) => {
    if (stopped) return;
    stopped = true;
    if (timer) clearTimeout(timer);
    processor.disconnect();
    source.disconnect();
    stream.getTracks().forEach((track) => track.stop());
    void context.close().catch(() => undefined);
    if (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING) socket.close(1000, "client stop");
    handlers.onClose(reason);
  };

  socket.addEventListener("open", () => {
    socket.send(JSON.stringify({
      type: "config",
      sample_rate: LIVE_SAMPLE_RATE,
      channels: 1,
      encoding: "pcm_s16le",
      frame_bytes: LIVE_FRAME_BYTES,
      frame_ms: LIVE_FRAME_INTERVAL_MS,
      vad_preset: "telephony",
      candidates: LIVE_CANDIDATES,
      demo: true,
    }));
    source.connect(processor);
    processor.connect(mute);
    mute.connect(context.destination);
    tickStart = performance.now();
    tick = 0;
    pace();
    handlers.onOpen();
  });
  socket.addEventListener("message", (event) => {
    try {
      handlers.onMessage(parseLiveMessage(event.data));
    } catch (error) {
      handlers.onMessage({
        type: "error",
        message: error instanceof Error ? error.message : "The live proxy response was invalid.",
      });
    }
  });
  socket.addEventListener("error", () => cleanup("The live audio proxy could not be reached."));
  socket.addEventListener("close", (event) => cleanup(event.code === 1000 ? undefined : `Live connection closed (${event.code}).`));

  return { stop: () => cleanup() };
}
