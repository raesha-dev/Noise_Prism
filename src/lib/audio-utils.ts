/** Browser-side audio helpers: waveform peaks, rough speech statistics, sample clips, WAV encoding. */

export type SignalStats = {
  durationSec: number;
  snrDb: number;
  noiseFloorDb: number;
  speechEnergyDb: number;
  speechRatio: number;
  vadBins: boolean[];
  vadSegments: [number, number][];
  spectrum: number[];
};

export function computePeaks(samples: Float32Array, bins = 320): number[] {
  if (samples.length === 0) return Array.from({ length: bins }, () => 0);
  const size = samples.length / bins;
  const peaks: number[] = [];
  let max = 0;
  for (let bin = 0; bin < bins; bin += 1) {
    const start = Math.floor(bin * size);
    const end = Math.max(start + 1, Math.floor((bin + 1) * size));
    let peak = 0;
    for (let i = start; i < end && i < samples.length; i += 1)
      peak = Math.max(peak, Math.abs(samples[i] ?? 0));
    peaks.push(peak);
    max = Math.max(max, peak);
  }
  return max > 0 ? peaks.map((value) => value / max) : peaks;
}

const toDb = (power: number) => 10 * Math.log10(power + 1e-12);

function fftMagnitudes(frame: Float32Array): Float32Array {
  const n = frame.length;
  const re = Float32Array.from(
    frame,
    (value, i) => value * (0.5 - 0.5 * Math.cos((2 * Math.PI * i) / (n - 1))),
  );
  const im = new Float32Array(n);
  for (let i = 1, j = 0; i < n; i += 1) {
    let bit = n >> 1;
    for (; j & bit; bit >>= 1) j ^= bit;
    j ^= bit;
    if (i < j) {
      [re[i], re[j]] = [re[j] ?? 0, re[i] ?? 0];
    }
  }
  for (let len = 2; len <= n; len <<= 1) {
    const angle = (-2 * Math.PI) / len;
    for (let i = 0; i < n; i += len) {
      for (let k = 0; k < len / 2; k += 1) {
        const wr = Math.cos(angle * k),
          wi = Math.sin(angle * k);
        const a = i + k,
          b = i + k + len / 2;
        const tr = (re[b] ?? 0) * wr - (im[b] ?? 0) * wi;
        const ti = (re[b] ?? 0) * wi + (im[b] ?? 0) * wr;
        re[b] = (re[a] ?? 0) - tr;
        im[b] = (im[a] ?? 0) - ti;
        re[a] = (re[a] ?? 0) + tr;
        im[a] = (im[a] ?? 0) + ti;
      }
    }
  }
  return Float32Array.from({ length: n / 2 }, (_, i) => Math.hypot(re[i] ?? 0, im[i] ?? 0));
}

/**
 * Rough energy-based statistics computed in the browser. These are estimates for orientation,
 * not the service's VAD/SNR measurement.
 */
export function analyzeSignal(
  samples: Float32Array,
  sampleRate: number,
  vadBinCount = 48,
  bands = 32,
): SignalStats {
  const frameSize = Math.max(1, Math.round(sampleRate * 0.02));
  const powers: number[] = [];
  for (let start = 0; start + frameSize <= samples.length; start += frameSize) {
    let sum = 0;
    for (let i = start; i < start + frameSize; i += 1) sum += (samples[i] ?? 0) ** 2;
    powers.push(sum / frameSize);
  }
  const durationSec = samples.length / sampleRate;
  if (powers.length === 0) {
    return {
      durationSec,
      snrDb: 0,
      noiseFloorDb: -120,
      speechEnergyDb: -120,
      speechRatio: 0,
      vadBins: Array(vadBinCount).fill(false),
      vadSegments: [],
      spectrum: Array(bands).fill(0),
    };
  }
  const sortedDb = powers.map(toDb).sort((a, b) => a - b);
  const floorDb = sortedDb[Math.floor(sortedDb.length * 0.15)] ?? -120;
  const threshold = floorDb + 6;
  const speechFlags = powers.map((power) => toDb(power) > threshold);
  const speechPowers = powers.filter((_, i) => speechFlags[i]);
  const noisePowers = powers.filter((_, i) => !speechFlags[i]);
  const mean = (values: number[]) =>
    values.length ? values.reduce((a, b) => a + b, 0) / values.length : 1e-12;
  const speechEnergyDb = toDb(mean(speechPowers));
  const noiseFloorDb = toDb(mean(noisePowers.length ? noisePowers : [Math.min(...powers)]));

  const vadBins = Array.from({ length: vadBinCount }, (_, bin) => {
    const start = Math.floor((bin / vadBinCount) * speechFlags.length);
    const end = Math.max(start + 1, Math.floor(((bin + 1) / vadBinCount) * speechFlags.length));
    const slice = speechFlags.slice(start, end);
    return slice.filter(Boolean).length / Math.max(1, slice.length) >= 0.5;
  });

  const vadSegments: [number, number][] = [];
  let open: number | null = null;
  speechFlags.forEach((flag, i) => {
    const t = (i * frameSize) / sampleRate;
    if (flag && open === null) open = t;
    if (!flag && open !== null) {
      vadSegments.push([open, t]);
      open = null;
    }
  });
  if (open !== null) vadSegments.push([open, durationSec]);

  const fftSize = 256;
  const spectrumSum = new Float32Array(fftSize / 2);
  const frameCount = Math.min(80, Math.floor(samples.length / fftSize));
  for (let f = 0; f < frameCount; f += 1) {
    const start = Math.floor((f / Math.max(1, frameCount)) * (samples.length - fftSize));
    const mags = fftMagnitudes(samples.slice(start, start + fftSize));
    mags.forEach((value, i) => (spectrumSum[i] = (spectrumSum[i] ?? 0) + value));
  }
  const nyquist = sampleRate / 2;
  const maxBin = Math.floor((Math.min(4000, nyquist) / nyquist) * (fftSize / 2));
  const bandDb = Array.from({ length: bands }, (_, band) => {
    const start = Math.floor((band / bands) * maxBin);
    const end = Math.max(start + 1, Math.floor(((band + 1) / bands) * maxBin));
    let sum = 0;
    for (let i = start; i < end; i += 1) sum += spectrumSum[i] ?? 0;
    return 20 * Math.log10(sum / (end - start) + 1e-9);
  });
  const top = Math.max(...bandDb);
  const spectrum = bandDb.map((value) => Math.max(0, Math.min(1, 1 + (value - top) / 60)));

  return {
    durationSec,
    snrDb: speechPowers.length && noisePowers.length ? speechEnergyDb - noiseFloorDb : 0,
    noiseFloorDb,
    speechEnergyDb,
    speechRatio: speechPowers.length / powers.length,
    vadBins,
    vadSegments,
    spectrum,
  };
}

/** Reads sample rate and channel count from a RIFF/WAVE header, or null for other formats. */
export function readWavHeader(bytes: ArrayBuffer): { sampleRate: number; channels: number } | null {
  if (bytes.byteLength < 44) return null;
  const view = new DataView(bytes);
  const tag = (offset: number) =>
    String.fromCharCode(
      view.getUint8(offset),
      view.getUint8(offset + 1),
      view.getUint8(offset + 2),
      view.getUint8(offset + 3),
    );
  if (tag(0) !== "RIFF" || tag(8) !== "WAVE") return null;
  let offset = 12;
  while (offset + 8 <= bytes.byteLength) {
    const size = view.getUint32(offset + 4, true);
    if (tag(offset) === "fmt " && offset + 16 <= bytes.byteLength)
      return {
        channels: view.getUint16(offset + 10, true),
        sampleRate: view.getUint32(offset + 12, true),
      };
    offset += 8 + size + (size % 2);
  }
  return null;
}

export async function decodeToMono(file: Blob): Promise<{
  samples: Float32Array;
  sampleRate: number;
  sourceRate?: number | undefined;
  channels: number;
  duration: number;
}> {
  const AudioCtx =
    window.AudioContext ??
    (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
  const context = new AudioCtx();
  try {
    const bytes = await file.arrayBuffer();
    const header = readWavHeader(bytes);
    const buffer = await context.decodeAudioData(bytes);
    const mono = new Float32Array(buffer.length);
    for (let c = 0; c < buffer.numberOfChannels; c += 1) {
      const data = buffer.getChannelData(c);
      for (let i = 0; i < data.length; i += 1)
        mono[i] = (mono[i] ?? 0) + (data[i] ?? 0) / buffer.numberOfChannels;
    }
    // decodeAudioData resamples to the device rate; report the file's own rate when the header tells us.
    return {
      samples: mono,
      sampleRate: buffer.sampleRate,
      sourceRate: header?.sampleRate,
      channels: header?.channels ?? buffer.numberOfChannels,
      duration: buffer.duration,
    };
  } finally {
    void context.close().catch(() => undefined);
  }
}

export function encodeWav(samples: Float32Array, sampleRate: number): Blob {
  return new Blob([encodeWavBuffer(samples, sampleRate)], { type: "audio/wav" });
}

export function makeWavFile(samples: Float32Array, sampleRate: number, sourceName: string): File {
  const stem = sourceName.replace(/\.[^/.]+$/, "") || "audio";
  return new File([encodeWav(samples, sampleRate)], `${stem}.wav`, { type: "audio/wav" });
}

export function encodeWavBuffer(samples: Float32Array, sampleRate: number): ArrayBuffer {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);
  const write = (offset: number, text: string) =>
    [...text].forEach((ch, i) => view.setUint8(offset + i, ch.charCodeAt(0)));
  write(0, "RIFF");
  view.setUint32(4, 36 + samples.length * 2, true);
  write(8, "WAVE");
  write(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  write(36, "data");
  view.setUint32(40, samples.length * 2, true);
  samples.forEach((value, i) =>
    view.setInt16(44 + i * 2, Math.max(-32768, Math.min(32767, Math.round(value * 32767))), true),
  );
  return buffer;
}

export type SampleKind = "call" | "street" | "quiet";
export const SAMPLE_CLIPS: Record<SampleKind, { label: string; fileName: string }> = {
  call: { label: "Noisy telephony call (synthetic)", fileName: "synthetic_call_traffic.wav" },
  street: { label: "Street babble (synthetic)", fileName: "synthetic_street_babble.wav" },
  quiet: { label: "Clean reference (synthetic)", fileName: "synthetic_clean_reference.wav" },
};

/** Synthetic speech-like 8 kHz clip for trying the workstation. Not real speech. */
export function makeSampleClip(kind: SampleKind, durationSec = 14.7): File {
  const rate = 8000;
  const length = Math.round(rate * durationSec);
  const out = new Float32Array(length);
  let seed = kind === "call" ? 11 : kind === "street" ? 29 : 47;
  const rand = () => {
    seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
  let phase = 0;
  let brown = 0;
  for (let i = 0; i < length; i += 1) {
    const t = i / rate;
    const phraseOn = Math.sin(2 * Math.PI * 0.21 * t + 0.6) > -0.35 ? 1 : 0;
    const syllable = Math.max(0, Math.sin(2 * Math.PI * 4.1 * t)) ** 1.5;
    const f0 = 150 + 45 * Math.sin(2 * Math.PI * 0.7 * t) + 20 * Math.sin(2 * Math.PI * 2.3 * t);
    phase += (2 * Math.PI * f0) / rate;
    const formant = 0.9 + 0.6 * Math.sin(2 * Math.PI * 1.7 * t);
    let voice = 0;
    for (let h = 1; h <= 14; h += 1) {
      const freq = f0 * h;
      if (freq > 3400) break;
      const env =
        Math.exp(-(((freq - 500 * formant) / 350) ** 2)) +
        0.6 * Math.exp(-(((freq - 1500 * formant) / 500) ** 2)) +
        0.12;
      voice += (env * Math.sin(phase * h)) / h;
    }
    const speech = 0.32 * voice * syllable * phraseOn;
    const white = rand() * 2 - 1;
    brown = (brown + 0.02 * white) / 1.02;
    let noise = 0;
    if (kind === "call")
      noise =
        1.6 * brown +
        0.05 * white +
        0.035 * Math.sin(2 * Math.PI * 50 * t) +
        (Math.sin(2 * Math.PI * 0.09 * t) > 0.97 ? 0.08 * Math.sin(2 * Math.PI * 420 * t) : 0);
    if (kind === "street")
      noise = 0.09 * white * (0.6 + 0.4 * Math.sin(2 * Math.PI * 3.3 * t + 1)) + 0.8 * brown;
    if (kind === "quiet") noise = 0.006 * white;
    out[i] = Math.max(-1, Math.min(1, speech + noise));
  }
  return new File([encodeWav(out, rate)], SAMPLE_CLIPS[kind].fileName, { type: "audio/wav" });
}
