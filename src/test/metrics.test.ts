import { describe, expect, it } from "vitest";
import { characterErrorRate, diffWords, wordErrorRate } from "@/lib/metrics";
import { analyzeSignal, computePeaks } from "@/lib/audio-utils";
import { routeBySnr } from "@/lib/processing-contract";

describe("transcript scoring", () => {
  it("counts one substituted word out of four as 25% WER, ignoring case and punctuation", () => {
    expect(wordErrorRate("Haan, mujhe plan bhejo!", "haan mujhe plane bhejo")).toBeCloseTo(0.25);
  });
  it("computes CER on characters without spaces", () => {
    expect(characterErrorRate("abcd", "abed")).toBeCloseTo(0.25);
  });
  it("highlights words that differ from C0", () => {
    expect(diffWords("voh plan varna", "woh plan warna").map((t) => t.kind)).toEqual(["changed", "same", "changed"]);
  });
});

describe("browser estimates", () => {
  it("finds speech in a tone burst and reports a high SNR over a quiet floor", () => {
    const rate = 8000;
    const samples = new Float32Array(rate * 2);
    for (let i = 0; i < samples.length; i += 1) samples[i] = i > rate * 0.5 && i < rate * 1.5 ? 0.5 * Math.sin((2 * Math.PI * 400 * i) / rate) : 0.001 * Math.sin(i * 1.3);
    const stats = analyzeSignal(samples, rate);
    expect(stats.snrDb).toBeGreaterThan(30);
    expect(stats.vadBins[24]).toBe(true);
    expect(stats.vadBins[2]).toBe(false);
  });
  it("normalizes peaks to the loudest point", () => {
    expect(Math.max(...computePeaks(Float32Array.from([0.1, -0.4, 0.2, 0.05]), 2))).toBe(1);
  });
});

describe("demo SNR router", () => {
  it("keeps clean audio on C0 and routes noisier audio to stronger candidates", () => {
    expect(routeBySnr(20, true)).toBe("C0");
    expect(routeBySnr(10, true)).toBe("C1");
    expect(routeBySnr(5, true)).toBe("C2");
    expect(routeBySnr(1, true)).toBe("C3");
    expect(routeBySnr(1, false)).toBe("C2");
  });
});

import { encodeWavBuffer, readWavHeader } from "@/lib/audio-utils";
describe("WAV header", () => {
  it("reports the file's own 8 kHz mono format", async () => {
    const bytes = encodeWavBuffer(new Float32Array(800), 8000);
    expect(readWavHeader(bytes)).toEqual({ sampleRate: 8000, channels: 1 });
  });
});
