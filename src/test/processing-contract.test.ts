import { describe, expect, it } from "vitest";

import { getExecutionProfile } from "@/lib/processing-contract";

describe("audio accuracy-mode call budgets", () => {
  it("keeps Fast mode to one provider call", () => {
    expect(getExecutionProfile("fast").providerCalls).toEqual({ min: 1, max: 1 });
  });

  it("labels Max accuracy as three to four calls and about three times the cost", () => {
    expect(getExecutionProfile("max")).toEqual({
      providerCalls: { min: 3, max: 4 },
      label: "3–4 provider calls · about 3× cost",
    });
  });
});