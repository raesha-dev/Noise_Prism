import type { AccuracyMode, TrackId } from "./bharatnoise-api";

export type ExecutionProfile = {
  providerCalls: { min: number; max: number };
  label: string;
};

export function getExecutionProfile(mode: AccuracyMode): ExecutionProfile {
  return mode === "fast"
    ? { providerCalls: { min: 1, max: 1 }, label: "1 provider call" }
    : { providerCalls: { min: 3, max: 4 }, label: "3–4 provider calls · about 3× cost" };
}

/**
 * Demo-only SNR router used when no service is connected. The real choice is made by the proxy.
 * Clean audio stays on C0 so enhancement cannot hurt it; noisier audio moves to stronger candidates.
 */
export function routeBySnr(snrDb: number | undefined, includeC3: boolean): TrackId {
  if (snrDb === undefined || snrDb >= 15) return "C0";
  if (snrDb >= 8) return "C1";
  if (snrDb < 3 && includeC3) return "C3";
  return "C2";
}
