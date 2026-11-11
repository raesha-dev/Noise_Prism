import { describe, expect, it } from "vitest";
import { LIVE_CANDIDATES, LIVE_FRAME_BYTES, LIVE_FRAME_INTERVAL_MS, LIVE_SAMPLE_RATE, Pcm16Resampler, PcmFrameQueue } from "@/lib/pcm-framer";

describe("live frame contract", () => {
  it("uses exactly 1,024-byte frames at 64 ms cadence and 8 kHz", () => {
    expect(LIVE_FRAME_BYTES).toBe(1024);
    expect(LIVE_FRAME_INTERVAL_MS).toBe(64);
    expect(LIVE_SAMPLE_RATE).toBe(8000);
  });
  it("only streams the cheap candidates (no DeepFilterNet C3)", () => {
    expect([...LIVE_CANDIDATES]).toEqual(["C0", "C1", "C2"]);
  });
  it("turns 128 ms of 48 kHz audio into two exact 1,024-byte frames", () => {
    const resampler = new Pcm16Resampler(48000);
    const queue = new PcmFrameQueue();
    for (let i = 0; i < 6; i += 1) queue.push(resampler.process(new Float32Array(1024).fill(0.5)));
    const first = queue.takeFrame();
    const second = queue.takeFrame();
    expect(first?.byteLength).toBe(1024);
    expect(second?.byteLength).toBe(1024);
    expect(queue.takeFrame()).toBeNull();
  });
  it("writes little-endian PCM16 and clamps full-scale input", () => {
    const queue = new PcmFrameQueue();
    queue.push(new Pcm16Resampler(8000).process(new Float32Array(512).fill(2)));
    const view = new DataView(queue.takeFrame()!);
    expect(view.getInt16(0, true)).toBe(32767);
  });
});
