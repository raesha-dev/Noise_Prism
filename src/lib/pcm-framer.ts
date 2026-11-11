/**
 * Live-monitor framing: converts browser microphone audio (any rate, float32) into
 * 8 kHz mono PCM16 little-endian, emitted as exactly 1,024-byte frames (512 samples = 64 ms).
 */
export const LIVE_SAMPLE_RATE = 8000;
export const LIVE_FRAME_BYTES = 1024;
export const LIVE_FRAME_SAMPLES = LIVE_FRAME_BYTES / 2;
export const LIVE_FRAME_INTERVAL_MS = (LIVE_FRAME_SAMPLES / LIVE_SAMPLE_RATE) * 1000; // 64
/** Live mode only runs the cheap candidates; DeepFilterNet (C3) is excluded. */
export const LIVE_CANDIDATES = ["C0", "C1", "C2"] as const;

/** Streaming down-sampler with a box anti-alias filter; keeps fractional position across chunks. */
export class Pcm16Resampler {
  private readonly ratio: number;
  private position = 0;
  private carry = new Float32Array(0);

  constructor(inputRate: number, outputRate = LIVE_SAMPLE_RATE) {
    if (inputRate < outputRate) throw new Error("Input sample rate must be at least the output rate.");
    this.ratio = inputRate / outputRate;
  }

  process(input: Float32Array): Int16Array {
    const data = new Float32Array(this.carry.length + input.length);
    data.set(this.carry);
    data.set(input, this.carry.length);
    const out: number[] = [];
    const half = this.ratio / 2;
    while (this.position + half < data.length) {
      const start = Math.max(0, Math.floor(this.position - half));
      const end = Math.min(data.length, Math.ceil(this.position + half));
      let sum = 0;
      for (let i = start; i < end; i += 1) sum += data[i] ?? 0;
      const value = end > start ? sum / (end - start) : 0;
      out.push(Math.max(-32768, Math.min(32767, Math.round(value * 32767))));
      this.position += this.ratio;
    }
    const keepFrom = Math.max(0, Math.floor(this.position - half));
    this.carry = data.slice(keepFrom);
    this.position -= keepFrom;
    return Int16Array.from(out);
  }
}

/** Accumulates PCM16 samples and hands out exact 1,024-byte little-endian frames. */
export class PcmFrameQueue {
  private samples: number[] = [];
  readonly maxBufferedFrames: number;
  dropped = 0;

  constructor(maxBufferedFrames = 32) {
    this.maxBufferedFrames = maxBufferedFrames;
  }

  push(pcm: Int16Array) {
    for (const value of pcm) this.samples.push(value);
    const limit = this.maxBufferedFrames * LIVE_FRAME_SAMPLES;
    if (this.samples.length > limit) {
      const excess = this.samples.length - limit;
      const dropSamples = Math.ceil(excess / LIVE_FRAME_SAMPLES) * LIVE_FRAME_SAMPLES;
      this.samples.splice(0, dropSamples);
      this.dropped += dropSamples / LIVE_FRAME_SAMPLES;
    }
  }

  get bufferedFrames() {
    return Math.floor(this.samples.length / LIVE_FRAME_SAMPLES);
  }

  takeFrame(): ArrayBuffer | null {
    if (this.samples.length < LIVE_FRAME_SAMPLES) return null;
    const chunk = this.samples.splice(0, LIVE_FRAME_SAMPLES);
    const buffer = new ArrayBuffer(LIVE_FRAME_BYTES);
    const view = new DataView(buffer);
    chunk.forEach((value, index) => view.setInt16(index * 2, value, true));
    return buffer;
  }
}
