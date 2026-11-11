import { spawn, type ChildProcess } from "node:child_process";
import { createConnection, createServer } from "node:net";
import { mkdtemp, rm, access } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it, vi } from "vitest";
import { checkApiConnection, fetchEvaluation, processAudioFile } from "@/lib/bharatnoise-api";
import { startLiveStream, type LiveSession } from "@/lib/live-stream";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const PYTHON_ENV = path.join(
  ROOT,
  ".venv",
  process.platform === "win32" ? "Scripts/python.exe" : "bin/python",
);

type AudioEvent = { inputBuffer: { getChannelData: (channel: number) => Float32Array } };

class FakeAudioNode {
  connect(): void {}
  disconnect(): void {}
}

class FakeAudioProcessor extends FakeAudioNode {
  onaudioprocess: ((event: AudioEvent) => void) | null = null;
}

class FakeAudioContext {
  readonly sampleRate = 48_000;
  readonly destination = new FakeAudioNode();

  createMediaStreamSource(): FakeAudioNode {
    return new FakeAudioNode();
  }

  createScriptProcessor(): FakeAudioProcessor {
    activeProcessor = new FakeAudioProcessor();
    return activeProcessor;
  }

  createGain(): FakeAudioNode & { gain: { value: number } } {
    return Object.assign(new FakeAudioNode(), { gain: { value: 1 } });
  }

  async close(): Promise<void> {}
}

let activeProcessor: FakeAudioProcessor | null = null;

async function freePort(): Promise<number> {
  const server = createServer();
  await new Promise<void>((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  const address = server.address();
  if (!address || typeof address === "string") throw new Error("Could not allocate a local port.");
  const port = address.port;
  await new Promise<void>((resolve, reject) =>
    server.close((error) => (error ? reject(error) : resolve())),
  );
  return port;
}

async function pythonExecutable(): Promise<string> {
  try {
    await access(PYTHON_ENV);
    return PYTHON_ENV;
  } catch {
    return process.env["PYTHON"] ?? (process.platform === "win32" ? "python" : "python3");
  }
}

function startProcess(command: string, args: string[], env: NodeJS.ProcessEnv): ChildProcess {
  const child = spawn(command, args, { cwd: ROOT, env, stdio: ["ignore", "pipe", "pipe"] });
  child.stdout?.on("data", () => {});
  child.stderr?.on("data", () => {});
  return child;
}

async function waitForPort(port: number, child: ChildProcess, timeoutMs = 20_000): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (child.exitCode !== null)
      throw new Error(`Local service exited with code ${child.exitCode}.`);
    try {
      await new Promise<void>((resolve, reject) => {
        const socket = createConnection({ host: "127.0.0.1", port });
        socket.once("error", reject);
        socket.once("connect", () => {
          socket.destroy();
          resolve();
        });
      });
      return;
    } catch {
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
  }
  throw new Error("Local service did not become ready.");
}

async function waitForBackend(baseUrl: string, child: ChildProcess): Promise<void> {
  const deadline = Date.now() + 20_000;
  while (Date.now() < deadline) {
    if (child.exitCode !== null) throw new Error(`Backend exited with code ${child.exitCode}.`);
    try {
      const response = await fetch(`${baseUrl}/api/health`, { signal: AbortSignal.timeout(1_000) });
      if (response.ok) return;
    } catch {
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
  }
  throw new Error("Backend did not become ready.");
}

async function stopProcess(child: ChildProcess): Promise<void> {
  if (child.exitCode !== null) return;
  child.kill("SIGTERM");
  let exited = false;
  await new Promise<void>((resolve) => {
    const timer = setTimeout(resolve, 5_000);
    child.once("exit", () => {
      exited = true;
      clearTimeout(timer);
      resolve();
    });
  });
  if (exited || child.exitCode !== null) return;
  if (child.exitCode === null) {
    child.kill("SIGKILL");
    await new Promise<void>((resolve) => child.once("exit", () => resolve()));
  }
}

function syntheticWav(): Buffer {
  const rate = 8000;
  const count = rate * 2;
  const bytes = Buffer.alloc(44 + count * 2);
  bytes.write("RIFF", 0);
  bytes.writeUInt32LE(bytes.length - 8, 4);
  bytes.write("WAVE", 8);
  bytes.write("fmt ", 12);
  bytes.writeUInt32LE(16, 16);
  bytes.writeUInt16LE(1, 20);
  bytes.writeUInt16LE(1, 22);
  bytes.writeUInt32LE(rate, 24);
  bytes.writeUInt32LE(rate * 2, 28);
  bytes.writeUInt16LE(2, 32);
  bytes.writeUInt16LE(16, 34);
  bytes.write("data", 36);
  bytes.writeUInt32LE(count * 2, 40);
  for (let index = 0; index < count; index += 1) {
    const sample = Math.round(8192 * Math.sin((2 * Math.PI * 440 * index) / rate));
    bytes.writeInt16LE(sample, 44 + index * 2);
  }
  return bytes;
}

function waitFor<T>(promise: Promise<T>, timeoutMs: number, label: string): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`${label} timed out.`)), timeoutMs);
    promise.then(
      (value) => {
        clearTimeout(timer);
        resolve(value);
      },
      (error: unknown) => {
        clearTimeout(timer);
        reject(error);
      },
    );
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
  activeProcessor = null;
});

describe("frontend end-to-end mock stack", () => {
  it("uses the API adapter and live stream against the real local services", async () => {
    const backendPort = await freePort();
    let mockPort = await freePort();
    while (mockPort === backendPort) mockPort = await freePort();
    const dataDir = await mkdtemp(path.join(os.tmpdir(), "bharatnoise-frontend-e2e-"));
    const env: NodeJS.ProcessEnv = {
      ...process.env,
      GNANI_API_KEY: "mock-key",
      GNANI_BASE_URL: `http://127.0.0.1:${mockPort}`,
      GNANI_WS_URL: `ws://127.0.0.1:${mockPort}/stt/v3/stream`,
      BN_DATA_DIR: dataDir,
      BN_DAILY_CALL_CAP: "300",
      BN_LIVE_STOP_GRACE_S: "1",
    };
    const python = await pythonExecutable();
    const mock = startProcess(
      python,
      ["-m", "bharatnoise_api.mock_gnani", "--port", String(mockPort)],
      env,
    );
    let backend: ChildProcess | null = null;
    let session: LiveSession | null = null;
    let audioTimer: ReturnType<typeof setInterval> | null = null;
    try {
      await waitForPort(mockPort, mock);
      backend = startProcess(
        python,
        [
          "-m",
          "uvicorn",
          "bharatnoise_api.server:app",
          "--workers",
          "1",
          "--host",
          "127.0.0.1",
          "--port",
          String(backendPort),
        ],
        env,
      );
      await waitForPort(backendPort, backend);
      const baseUrl = `http://127.0.0.1:${backendPort}`;
      await waitForBackend(baseUrl, backend);

      const health = await checkApiConnection(baseUrl);
      expect(health.gnaniMode).toBe("mock");
      const file = new File([syntheticWav()], "synthetic-tone.wav", { type: "audio/wav" });
      const result = await processAudioFile(baseUrl, file, {
        mode: "fast",
        candidates: ["C0", "C1", "C2"],
        asrModel: "prisma-v2.5",
        compareModels: false,
        timeoutMs: 90_000,
      });
      expect(result.gnaniMode).toBe("mock");
      expect(result.transcription).toMatch(/^\[MOCK\]/);
      expect(result.scoreSuppressedReason).toBe("mock");

      const evaluation = await fetchEvaluation(baseUrl);
      expect(evaluation.available).toBe(false);
      expect(evaluation.reason).toBe("No benchmark results have been generated yet.");

      let resolveConnected!: () => void;
      let resolveTranscript!: (text: string) => void;
      let rejectLive!: (error: Error) => void;
      let resolveClosed!: () => void;
      const connected = new Promise<void>((resolve) => {
        resolveConnected = resolve;
      });
      const transcript = new Promise<string>((resolve) => {
        resolveTranscript = resolve;
      });
      const liveFailure = new Promise<never>((_, reject) => {
        rejectLive = reject;
      });
      const liveResult = Promise.race([transcript, liveFailure]);
      const closed = new Promise<void>((resolve) => {
        resolveClosed = resolve;
      });
      let framesSent = 0;
      let connectedModel: unknown;
      const stream = {
        getTracks: () => [{ stop: () => {} }],
      };
      vi.stubGlobal("navigator", {
        mediaDevices: { getUserMedia: async () => stream },
      });
      vi.stubGlobal("AudioContext", FakeAudioContext);
      session = await startLiveStream(baseUrl, {
        onOpen: () => {},
        onMessage: (message) => {
          if (message.type === "connected") {
            connectedModel = message.config?.model;
            resolveConnected();
          }
          if (message.type === "transcript" && message.text.startsWith("[MOCK]"))
            resolveTranscript(message.text);
          if (message.type === "error") rejectLive(new Error("The live proxy returned an error."));
        },
        onStats: (stats) => {
          framesSent = stats.framesSent;
        },
        onClose: () => resolveClosed(),
      });
      await waitFor(connected, 5_000, "Live connected message");
      expect(connectedModel).toBe("mock-prisma-v2.5");

      const input = Float32Array.from(
        { length: 3072 },
        (_, index) => 0.25 * Math.sin((2 * Math.PI * 440 * index) / 48_000),
      );
      audioTimer = setInterval(() => {
        activeProcessor?.onaudioprocess?.({ inputBuffer: { getChannelData: () => input } });
      }, 64);
      await new Promise((resolve) => setTimeout(resolve, 3_000));
      if (audioTimer) clearInterval(audioTimer);
      audioTimer = null;
      session.stop();

      const mockTranscript = await waitFor(liveResult, 8_000, "Mock transcript");
      expect(mockTranscript.startsWith("[MOCK]")).toBe(true);
      expect(framesSent).toBeGreaterThan(0);
      await waitFor(closed, 8_000, "Live socket close");
      session = null;
    } finally {
      if (audioTimer) clearInterval(audioTimer);
      session?.stop();
      if (backend) await stopProcess(backend);
      await stopProcess(mock);
      await rm(dataDir, { recursive: true, force: true });
    }
  }, 120_000);
});
