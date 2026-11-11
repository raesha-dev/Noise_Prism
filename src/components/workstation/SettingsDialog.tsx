import { useEffect, useState } from "react";
import { Activity, CheckCircle2, LoaderCircle, TriangleAlert } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import {
  DEFAULT_API_BASE_URL,
  checkApiConnection,
  normalizeBaseUrl,
  type HealthInfo,
} from "@/lib/bharatnoise-api";

type Props = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  apiUrl: string;
  onSave: (url: string) => void;
  health: HealthInfo | null;
  onHealth: (health: HealthInfo | null) => void;
};

export function SettingsDialog({ open, onOpenChange, apiUrl, onSave, health, onHealth }: Props) {
  const [draft, setDraft] = useState(apiUrl);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (open) {
      setDraft(apiUrl);
      setError("");
    }
  }, [open, apiUrl]);

  function validated(): string | null {
    try {
      setError("");
      return normalizeBaseUrl(draft);
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : "That address is not valid.");
      return null;
    }
  }

  async function test() {
    const url = validated();
    if (!url) return;
    setChecking(true);
    try {
      const info = await checkApiConnection(url);
      onHealth(info);
      toast.success("Backend is reachable.");
    } catch (problem) {
      onHealth(null);
      setError(problem instanceof Error ? problem.message : "Could not reach the audio service.");
    } finally {
      setChecking(false);
    }
  }

  function save() {
    const url = validated();
    if (url === null) return;
    onSave(url);
    onOpenChange(false);
    toast.success("Backend address saved.");
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="settings-dialog">
        <div className="settings-heading">
          <span className="eyebrow">AUDIO SERVICE</span>
          <DialogTitle className="settings-title">Connect your proxy</DialogTitle>
          <DialogDescription className="settings-desc">
            The FastAPI backend holds your Gnani credentials and forwards telephony audio to Prisma.
            This page never sees the Gnani key.
          </DialogDescription>
        </div>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            save();
          }}
        >
          <label className="settings-label" htmlFor="proxy-url">
            Proxy base address
          </label>
          <input
            id="proxy-url"
            className="settings-input"
            inputMode="url"
            autoComplete="url"
            placeholder={DEFAULT_API_BASE_URL}
            value={draft}
            onChange={(event) => setDraft(event.currentTarget.value)}
            aria-invalid={Boolean(error)}
            aria-describedby={error ? "proxy-error" : undefined}
          />
          {error && (
            <p id="proxy-error" className="settings-error" role="alert">
              <TriangleAlert size={14} />
              {error}
            </p>
          )}
          {health && !error && (
            <p className="settings-ok" role="status">
              <CheckCircle2 size={14} />
              Status {health.status}
              {health.version ? ` · v${health.version}` : ""}
              {health.gnaniMode === "unconfigured"
                ? " · Gnani key missing"
                : health.gnaniMode === "mock"
                  ? " · mock Gnani configured"
                  : " · live Gnani configured"}
            </p>
          )}
          <div className="settings-hint">
            <span>Routes this page calls</span>
            <code>GET /health</code>
            <code>POST /process</code>
            <code>GET /evaluation</code>
            <code>WS /live</code>
          </div>
          <div className="settings-hint">
            <span>POST /process</span>
            <small>
              Multipart: file, mode (fast|max), sample_rate=8000, channels=1,
              sample_format=pcm_s16le, asr_provider=gnani_prisma, asr_model, candidates (e.g.
              C0,C1,C2), selection (snr_router|consensus), compare_models. Returns
              selected_candidate, candidates.{"{"}C0…C3{"}"}.{"{"}transcript, audio_url, guard_score
              {"}"}, analysis, evaluation, pipeline.
            </small>
          </div>
          <div className="settings-hint">
            <span>WS /live</span>
            <small>
              First a JSON config message, then exactly 1,024-byte PCM16 frames every 64 ms.
              Browsers cannot send custom socket headers, so the proxy sets x-sample-rate: 8000 and
              the telephony VAD preset upstream. Allow this site's origin in CORS.
            </small>
          </div>
          <div className="settings-actions">
            {apiUrl !== DEFAULT_API_BASE_URL && (
              <Button
                type="button"
                variant="ghost"
                onClick={() => {
                  onSave(DEFAULT_API_BASE_URL);
                  onOpenChange(false);
                  toast("Local backend address restored.");
                }}
              >
                Use local backend
              </Button>
            )}
            <Button
              type="button"
              variant="outline"
              onClick={() => void test()}
              disabled={checking || !draft.trim()}
            >
              {checking ? <LoaderCircle className="spin" /> : <Activity />}Test connection
            </Button>
            <Button type="submit">Save address</Button>
          </div>
        </form>
        <p className="privacy-note">
          The address is stored only in this browser. Audio is uploaded only when you run the
          pipeline or start live streaming.
        </p>
      </DialogContent>
    </Dialog>
  );
}
