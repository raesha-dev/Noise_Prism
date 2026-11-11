import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { EvaluationReport } from "@/lib/bharatnoise-api";
import { EvaluationPanel } from "@/components/workstation/EvaluationPanel";
import { TranscriptPanel } from "@/components/workstation/TranscriptPanel";

describe("mock-mode metric suppression", () => {
  it("shows the explicitly marked mock transcript without WER or CER", () => {
    render(
      <TranscriptPanel
        transcripts={[{ id: "C0", text: "[MOCK] test placeholder", source: "service" }]}
        selected="C0"
        baseline="[MOCK] test placeholder"
        reference="reference fixture"
        onReference={vi.fn()}
        modelLabel="mock-Prisma v2.5"
        mockMode
      />,
    );

    expect(screen.getByText("[MOCK] test placeholder")).toBeInTheDocument();
    expect(screen.queryByText(/WER|CER/)).not.toBeInTheDocument();
  });

  it("hides imported and service evaluation metrics while mock mode is active", () => {
    const report: EvaluationReport = {
      available: true,
      dataset: "fixture",
      sections: {
        comparison: [{ id: "fixture", pipeline: "C0", wer: null, cer: null, latencyMs: null }],
      },
    };
    render(
      <EvaluationPanel
        report={report}
        onReport={vi.fn()}
        reportSource="imported"
        apiUrl="http://127.0.0.1:8000"
        lastResult={null}
        mockMode
      />,
    );

    expect(
      screen.getByText("WER and CER are hidden while the configured service is in mock mode."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.queryByText("WER by row")).not.toBeInTheDocument();
  });
});
