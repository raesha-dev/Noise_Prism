import { useRef, type ChangeEvent, type MouseEvent } from "react";
import {
  Activity,
  AudioLines,
  Clock3,
  CloudUpload,
  Headphones,
  LoaderCircle,
  Pause,
  Play,
  SkipBack,
  SkipForward,
  Volume2,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import type { TrackId } from "@/lib/bharatnoise-api";
import { SAMPLE_CLIPS, type SampleKind } from "@/lib/audio-utils";
import { WaveformPath } from "./shared";
import { formatTime, trackDefs } from "./workstation-utils";

export type TrackView = {
  id: TrackId;
  peaks: number[] | null;
  status: "real" | "service" | "pending" | "skipped" | "unavailable";
  hasAudio: boolean;
  reason?: string | undefined;
};

type Props = {
  clip: {
    name: string;
    source: "upload" | "recording" | "sample";
    sampleRate?: number | undefined;
    channels?: number | undefined;
  } | null;
  analyzing: boolean;
  pipelineBusy: boolean;
  tracks: TrackView[];
  selected: TrackId;
  onSelect: (id: TrackId) => void;
  checked: TrackId[];
  onToggleCheck: (id: TrackId, checked: boolean) => void;
  onFile: (file: File) => void;
  recording: boolean;
  recordSeconds: number;
  onRecord: () => void;
  onSample: (kind: SampleKind) => void;
  playing: boolean;
  currentTime: number;
  duration: number;
  onPlayToggle: () => void;
  onSeek: (seconds: number) => void;
  volume: number;
  onVolume: (value: number) => void;
  rate: number;
  onRate: (value: number) => void;
  playbackNote?: string | undefined;
};

const statusLabel: Record<TrackView["status"], string> = {
  real: "From your clip",
  service: "From service",
  pending: "Awaiting service result",
  skipped: "Not transcribed this run",
  unavailable: "Unavailable",
};

export function AudioStudio(props: Props) {
  const uploadRef = useRef<HTMLInputElement | null>(null);
  const { duration, currentTime } = props;
  const ticks = Array.from({ length: 7 }, (_, i) => ((i + 1) * duration) / 7);
  const progress = duration ? Math.max(0, Math.min(100, (currentTime / duration) * 100)) : 0;

  function onFileChange(event: ChangeEvent<HTMLInputElement>) {
    const file = event.currentTarget.files?.[0];
    if (file) props.onFile(file);
    event.currentTarget.value = "";
  }

  function seekFromClick(event: MouseEvent<HTMLButtonElement>, id: TrackId) {
    props.onSelect(id);
    if (!props.clip || !duration) return;
    const rect = event.currentTarget.getBoundingClientRect();
    props.onSeek(((event.clientX - rect.left) / rect.width) * duration);
  }

  return (
    <section className="panel studio-panel" aria-labelledby="studio-heading">
      <div className="panel-heading studio-heading">
        <div className="panel-title">
          <AudioLines />
          <div>
            <h1 id="studio-heading">Audio Studio</h1>
            <span>Inspect and compare candidate waveforms</span>
          </div>
        </div>
        <div className="studio-actions">
          <input
            ref={uploadRef}
            className="visually-hidden"
            type="file"
            accept="audio/*,.wav,.mp3,.ogg,.m4a,.flac,.webm"
            onChange={onFileChange}
            aria-label="Choose audio file"
            tabIndex={-1}
          />
          <Button className="upload-button" onClick={() => uploadRef.current?.click()}>
            <CloudUpload /> Add audio
          </Button>
          <Button
            variant="outline"
            className={`record-button ${props.recording ? "is-recording" : ""}`}
            onClick={props.onRecord}
            aria-pressed={props.recording}
          >
            <span className="record-dot" />
            {props.recording ? `Stop · ${formatTime(props.recordSeconds)}` : "Record"}
          </Button>
          <select
            className="sample-select"
            aria-label="Load a synthetic sample clip"
            value=""
            onChange={(event) => {
              const value = event.currentTarget.value as SampleKind | "";
              if (value) props.onSample(value);
            }}
          >
            <option value="">Samples</option>
            {(Object.keys(SAMPLE_CLIPS) as SampleKind[]).map((kind) => (
              <option key={kind} value={kind}>
                {SAMPLE_CLIPS[kind].label}
              </option>
            ))}
          </select>
        </div>
      </div>

      <div
        className="audio-meta"
        onDragOver={(event) => event.preventDefault()}
        onDrop={(event) => {
          event.preventDefault();
          const file = event.dataTransfer.files?.[0];
          if (file) props.onFile(file);
        }}
      >
        <span className="file-chip">
          <Headphones size={15} aria-hidden="true" />
          {props.clip?.name ?? "No clip loaded · drop audio here"}
          <span className="sample-tag">
            {props.clip ? props.clip.source.toUpperCase() : "NO CLIP"}
          </span>
        </span>
        <span className="meta-chip">
          <Activity />
          {!props.clip?.sampleRate
            ? "8 kHz target"
            : props.clip.sampleRate === 8000
              ? "8 kHz native"
              : `${(props.clip.sampleRate / 1000).toFixed(props.clip.sampleRate % 1000 ? 1 : 0)} kHz → 8 kHz`}
        </span>
        <span className="meta-chip">
          {props.clip?.channels && props.clip.channels > 1
            ? `${props.clip.channels} ch → mono`
            : "Mono"}
        </span>
        <span className="meta-chip">
          <Clock3 />
          {props.clip ? `${duration.toFixed(1)} s` : "—"}
        </span>
        {props.analyzing && (
          <span className="meta-chip">
            <LoaderCircle className="spin" />
            Analysing
          </span>
        )}
      </div>

      <div className="timeline-scale" aria-hidden="true">
        <span />
        {ticks.map((tick, i) => (
          <span key={i}>{props.clip ? formatTime(tick) : ""}</span>
        ))}
      </div>
      <div className="waveform-list">
        {props.tracks.map((track) => {
          const def = trackDefs.find((item) => item.id === track.id)!;
          const isSelected = props.selected === track.id;
          return (
            <div
              key={track.id}
              className={`wave-row ${isSelected ? "selected" : ""} status-${track.status}`}
            >
              <label
                className="track-toggle"
                title={`Include ${track.id} in transcript comparison`}
              >
                <input
                  type="checkbox"
                  checked={props.checked.includes(track.id)}
                  onChange={(event) => props.onToggleCheck(track.id, event.currentTarget.checked)}
                  aria-label={`Include ${track.id} in transcript comparison`}
                />
              </label>
              <button
                className="track-label"
                onClick={() => props.onSelect(track.id)}
                aria-pressed={isSelected}
                aria-label={`Select ${track.id} ${def.name}`}
              >
                <AudioLines size={15} />
                <strong>{track.id}</strong>
                <span>
                  <b>{def.name}</b>
                  <small>{def.detail}</small>
                </span>
              </button>
              <div className="waveform-cell">
                {track.peaks ? (
                  <button
                    className={`waveform wave-${def.color}`}
                    onClick={(event) => seekFromClick(event, track.id)}
                    aria-label={`Select ${track.id} and seek`}
                  >
                    <WaveformPath peaks={track.peaks} />
                    {props.clip && <span className="playhead" style={{ left: `${progress}%` }} />}
                  </button>
                ) : (
                  <button
                    className="waveform waveform-pending"
                    onClick={() => props.onSelect(track.id)}
                    aria-label={`Select ${track.id}`}
                  >
                    <span>
                      {props.clip ? (
                        <span title={track.reason}>{statusLabel[track.status]}</span>
                      ) : (
                        "Add a clip to view its waveform"
                      )}
                    </span>
                  </button>
                )}
                {track.peaks && <span className="track-status">{statusLabel[track.status]}</span>}
              </div>
            </div>
          );
        })}
      </div>

      <div className="transport">
        <div className="transport-left">
          <Button
            variant="secondary"
            size="icon"
            aria-label="Skip to beginning"
            onClick={() => props.onSeek(0)}
          >
            <SkipBack />
          </Button>
          <Button
            size="icon"
            className="play-button"
            aria-label={props.playing ? "Pause audio" : "Play audio"}
            onClick={props.onPlayToggle}
          >
            <span className="play-symbol">{props.playing ? <Pause /> : <Play />}</span>
          </Button>
          <Button
            variant="secondary"
            size="icon"
            aria-label="Skip forward 5 seconds"
            onClick={() => props.onSeek(Math.min(duration, currentTime + 5))}
          >
            <SkipForward />
          </Button>
          <span className="time-display">
            {formatTime(currentTime)} <i>/</i> {formatTime(duration)}
          </span>
        </div>
        <input
          className="seek-slider"
          aria-label="Audio playback position"
          type="range"
          min="0"
          max={duration || 0}
          step="0.01"
          value={Math.min(currentTime, duration || 0)}
          onChange={(event) => props.onSeek(Number(event.currentTarget.value))}
          disabled={!props.clip}
        />
        <div className="transport-right">
          <Volume2 size={16} aria-hidden="true" />
          <input
            aria-label="Audio volume"
            className="volume-slider"
            type="range"
            min="0"
            max="1"
            step="0.01"
            value={props.volume}
            onChange={(event) => props.onVolume(Number(event.currentTarget.value))}
          />
          <select
            aria-label="Playback speed"
            value={String(props.rate)}
            onChange={(event) => props.onRate(Number(event.currentTarget.value))}
          >
            {[0.75, 1, 1.25, 1.5, 2].map((rate) => (
              <option key={rate} value={String(rate)}>
                {rate}×
              </option>
            ))}
          </select>
        </div>
      </div>
      <p className="playback-note" aria-live="polite">
        {props.playbackNote ??
          (props.clip ? (
            <>Space plays or pauses · click a waveform to seek</>
          ) : (
            <>Add, record, or pick a synthetic sample clip to view its waveform.</>
          ))}
      </p>
      <details className="processing-guide" open={props.pipelineBusy || undefined}>
        <summary>What happens to uploaded audio?</summary>
        {props.pipelineBusy && (
          <p className="playback-note" role="status">
            Processing request in progress. This upload endpoint returns only the final response, so
            exact backend sub-step progress is not streamed; the sequence below explains what the
            backend is doing.
          </p>
        )}
        <ol>
          <li>
            <strong>Prepare in your browser:</strong> the file is decoded, downmixed to mono,
            converted to WAV, and shown as a waveform. This step does not request a transcript.
          </li>
          <li>
            <strong>When you press Run:</strong> the backend validates and resamples to 8 kHz,
            estimates speech regions and noise, then builds only the requested audio candidates. C0
            remains unchanged. C2B applies experimental spectral filtering, lowers detected
            non-speech, and levels detected speech; C2C adds some original audio back, which can
            also restore noise.
          </li>
          <li>
            <strong>Transcribe:</strong> Fast mode sends only its routed candidate. Max requests the
            selected candidates and compares their returned transcripts. Gnani requests run
            serially; upload processing is not a continuous live stream. Use Live Monitor for
            streaming audio.
          </li>
        </ol>
        <p>
          Voice activity detection cannot determine microphone distance or remove noise that
          overlaps speech perfectly. C2B/C2C parameters are unfitted experiments, so cleaner
          waveforms do not guarantee better recognition.
        </p>
      </details>
    </section>
  );
}
