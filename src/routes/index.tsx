import { createFileRoute } from "@tanstack/react-router";
import { Workstation } from "@/components/workstation/Workstation";

export const Route = createFileRoute("/")({
  head: () => ({
    meta: [
      { title: "BharatNoise | AI Audio Workstation" },
      {
        name: "description",
        content:
          "Audio workspace for noisy Indian telephony speech: compare C0–C3 enhancement candidates, speech analysis, and Gnani Prisma transcripts.",
      },
      { property: "og:title", content: "BharatNoise | AI Audio Workstation" },
      {
        property: "og:description",
        content:
          "Inspect audio candidates, speech analysis, transcripts, and evaluation in one workspace.",
      },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary_large_image" },
    ],
  }),
  component: Workstation,
});
