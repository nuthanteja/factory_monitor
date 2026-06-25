import { useEffect, useRef, useState, type RefObject } from "react";
import { playWhep, type WhepStatus } from "../lib/whep";

/**
 * Manages the WHEP lifecycle for a single camera.
 *
 * Guards against happy-dom (and SSR) environments where RTCPeerConnection is
 * undefined — in that case it immediately returns "failed" without attempting
 * any WebRTC negotiation.
 */
export function useWhep(
  url: string | null,
  videoRef: RefObject<HTMLVideoElement>,
): WhepStatus {
  const [status, setStatus] = useState<WhepStatus>("idle");
  // Keep a ref to the latest setStatus so effects can update without
  // re-running due to the setter reference.
  const setStatusRef = useRef(setStatus);
  setStatusRef.current = setStatus;

  useEffect(() => {
    // Guard: null/empty url means no stream is configured for this camera.
    if (!url) {
      setStatusRef.current("failed");
      return;
    }

    // Guard: happy-dom and SSR environments lack RTCPeerConnection.
    if (typeof RTCPeerConnection === "undefined") {
      setStatusRef.current("failed");
      return;
    }

    const video = videoRef.current;
    if (!video) {
      return;
    }

    const controller = new AbortController();
    let handle: { close(): void } | null = null;

    void playWhep({
      url,
      video,
      signal: controller.signal,
      onStatus: (s) => setStatusRef.current(s),
    }).then((h) => {
      handle = h;
    });

    return () => {
      // Abort any in-flight negotiation.
      controller.abort();
      // Close the WebRTC connection and send DELETE.
      handle?.close();
      // Null out srcObject so the video element releases the stream.
      if (video.srcObject) {
        video.srcObject = null;
      }
    };
  }, [url, videoRef]);

  return status;
}
