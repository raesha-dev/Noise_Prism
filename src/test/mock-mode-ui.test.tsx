import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { EvaluationReport } from "@/lib/bharatnoise-api";
import { EvaluationPanel } from "@/components/workstation/EvaluationPanel";
import { TranscriptPanel } from "@/components/workstation/TranscriptPanel";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("mock-mode metric suppression", () => {
  it("shows the explicitly marked mock transcript without WER or CER", () => {
    render(
      <TranscriptPanel
        transcripts={[{ id: "C0", text: "[MOCK] test placeholder", source: "service" }]}
        selected="C0"
        baseline="[MOCK] test placeholder"
        reference="reference fixture"
        onReference={vi.fn()}
        mode="fast"
        modelLabel="mock-Prisma v2.5"
        requestedLanguageCode="hi-IN"
        outputFormat="verbatim"
        detectedLanguageCode="kn-IN"
        mockMode
        apiUrl="http://127.0.0.1:8000"
      />,
    );

    expect(screen.getByText("[MOCK] test placeholder")).toBeInTheDocument();
    expect(screen.getByText(/Requested hi-IN, but Prisma detected kn-IN/)).toBeInTheDocument();
    expect(screen.queryByText(/WER|CER/)).not.toBeInTheDocument();
  });

  it("distinguishes an empty service transcript from an unrequested candidate", () => {
    render(
      <TranscriptPanel
        transcripts={[
          {
            id: "C0",
            text: "",
            source: "service",
            reason:
              "Gnani returned an empty transcript. Check the language setting and compare the original audio.",
          },
        ]}
        selected="C0"
        reference=""
        onReference={vi.fn()}
        mode="max"
        modelLabel="Prisma v3.0"
        requestedLanguageCode="indic-auto"
        outputFormat="transcribe"
        mockMode={false}
        apiUrl="http://127.0.0.1:8000"
      />,
    );

    expect(screen.getByText(/Gnani returned an empty transcript/)).toBeInTheDocument();
    expect(
      screen.queryByText(/No transcript was returned for this candidate/),
    ).not.toBeInTheDocument();
  });

  it("sends the complete multilingual transcript to Gnani and exposes returned audio", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(new Blob(["wav-data"], { type: "audio/wav" }), {
        status: 200,
        headers: { "Content-Type": "audio/wav" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:gnani-tts");
    const playAudio = vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue();
    const transcript = "नमस्ते, வணக்கம், hello";
    render(
      <TranscriptPanel
        transcripts={[{ id: "C0", text: transcript, source: "service" }]}
        selected="C0"
        baseline={transcript}
        reference=""
        onReference={vi.fn()}
        mode="fast"
        modelLabel="Prisma v3.0"
        requestedLanguageCode="indic-auto"
        detectedLanguageCode="hi-IN"
        outputFormat="verbatim"
        mockMode={false}
        apiUrl="http://127.0.0.1:8000"
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Read C0 transcript aloud" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:8000/api/tts",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ text: transcript }),
      }),
    );
    expect(await screen.findByLabelText("Gnani speech · C0")).toHaveAttribute(
      "src",
      "blob:gnani-tts",
    );
    await waitFor(() => expect(playAudio).toHaveBeenCalledOnce());
    expect(screen.getByText("Playing Gnani speech for C0.")).toBeInTheDocument();
  });

  it("selects the candidate with the lowest measured WER against a supplied reference", () => {
    const onSelect = vi.fn();
    render(
      <TranscriptPanel
        transcripts={[
          { id: "C0", text: "wrong words here", source: "service" },
          { id: "C1", text: "hello world", source: "service" },
          { id: "C2", text: "hello there", source: "service" },
        ]}
        selected="C0"
        baseline="wrong words here"
        reference="hello world"
        onReference={vi.fn()}
        onSelect={onSelect}
        mode="max"
        modelLabel="Prisma v3.0"
        requestedLanguageCode="en-IN"
        outputFormat="verbatim"
        mockMode={false}
        apiUrl="http://127.0.0.1:8000"
      />,
    );

    expect(onSelect).toHaveBeenLastCalledWith("C1");
    expect(screen.getByText(/lowest WER/)).toHaveTextContent(
      /Selected\s+C1\s*:\s*lowest WER\s*\(\s*0\.00%\s*\)/,
    );
    fireEvent.click(screen.getByRole("button", { name: "Select C2 transcript" }));
    expect(onSelect).toHaveBeenLastCalledWith("C2");
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
