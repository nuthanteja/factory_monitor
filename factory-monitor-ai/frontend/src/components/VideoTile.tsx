import { useRef } from "react";
import { useWhep } from "../hooks/useWhep";
import type { Camera } from "../lib/api";

interface VideoTileProps {
  camera: Camera;
}

export function VideoTile({ camera }: VideoTileProps): JSX.Element {
  const videoRef = useRef<HTMLVideoElement>(null);
  const status = useWhep(camera.whep_url, videoRef);

  return (
    <div
      data-testid="video-tile"
      data-camera-id={camera.id}
      style={{ position: "relative", background: "#000", aspectRatio: "16/9" }}
    >
      <video
        ref={videoRef}
        autoPlay
        muted
        playsInline
        style={{ width: "100%", height: "100%", display: "block" }}
        data-testid={`video-${camera.id}`}
      />

      {/* Fallback overlay — shown until the stream goes live */}
      {status !== "live" && (
        <div
          data-testid="video-fallback"
          style={{
            position: "absolute",
            inset: 0,
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
            background: "rgba(0,0,0,0.7)",
            color: "#fff",
            gap: "0.5rem",
          }}
        >
          <span data-testid="camera-name" style={{ fontWeight: 600 }}>
            {camera.name}
          </span>
          <span
            data-testid="whep-status"
            data-status={status}
            style={{ fontSize: "0.75rem", opacity: 0.75, textTransform: "uppercase" }}
          >
            {status === "failed" ? "Unavailable" : status}
          </span>
        </div>
      )}

      {/* Name badge — always visible */}
      <div
        data-testid="camera-name-badge"
        style={{
          position: "absolute",
          bottom: 0,
          left: 0,
          right: 0,
          padding: "0.25rem 0.5rem",
          background: "rgba(0,0,0,0.5)",
          color: "#fff",
          fontSize: "0.75rem",
        }}
      >
        {camera.name}
      </div>
    </div>
  );
}
