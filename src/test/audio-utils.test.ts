import { describe, expect, it } from "vitest";

import { encodeWavBuffer, makeWavFile, readWavHeader } from "@/lib/audio-utils";

describe("audio conversion", () => {
  it("creates a named mono PCM WAV for backend upload", async () => {
    const file = makeWavFile(new Float32Array([0, 0.5, -0.5]), 16_000, "call.m4a");

    expect(file.name).toBe("call.wav");
    expect(file.type).toBe("audio/wav");
    expect(file.size).toBe(50);
    expect(readWavHeader(encodeWavBuffer(new Float32Array([0, 0.5, -0.5]), 16_000))).toEqual({
      sampleRate: 16_000,
      channels: 1,
    });
  });
});
