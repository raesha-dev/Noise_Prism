import type { AccuracyMode } from "./bharatnoise-api";

export type ExecutionProfile = {
  providerCalls: { min: number; max: number };
  label: string;
};

export function getExecutionProfile(mode: AccuracyMode): ExecutionProfile {
  return mode === "fast"
    ? { providerCalls: { min: 1, max: 1 }, label: "1 provider call" }
    : { providerCalls: { min: 3, max: 4 }, label: "3–4 provider calls · about 3× cost" };
}