import { useCameras } from "../hooks/useCameras";
import { VideoTile } from "./VideoTile";

export function CameraWall(): JSX.Element {
  const { data: cameras, isLoading, isError, error } = useCameras();

  if (isLoading) {
    return (
      <p data-testid="cameras-loading" role="status">
        Loading cameras…
      </p>
    );
  }

  if (isError) {
    return (
      <p data-testid="cameras-error" role="alert">
        Failed to load cameras: {error?.message ?? "unknown error"}
      </p>
    );
  }

  if (!cameras || cameras.length === 0) {
    return (
      <p data-testid="cameras-empty" role="status">
        No cameras configured.
      </p>
    );
  }

  return (
    <section
      data-testid="camera-wall"
      style={{
        display: "grid",
        gridTemplateColumns: "repeat(auto-fill, minmax(320px, 1fr))",
        gap: "0.75rem",
        padding: "0.75rem",
      }}
    >
      {cameras.map((camera) => (
        <VideoTile key={camera.id} camera={camera} />
      ))}
    </section>
  );
}
