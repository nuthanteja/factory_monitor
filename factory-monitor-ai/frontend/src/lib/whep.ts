/**
 * Minimal WHEP (WebRTC-HTTP Egress Protocol) negotiator.
 *
 * Spec reference: draft-ietf-wish-whep
 * This module is a plain .ts file — no React, no JSX.
 */

export type WhepStatus = "idle" | "connecting" | "live" | "failed";

export interface WhepHandle {
  close(): void;
}

export interface WhepOptions {
  /** WHEP endpoint URL (same-origin relative or absolute) */
  url: string;
  /** Video element to attach the incoming stream to */
  video: HTMLVideoElement;
  /** Callback for status transitions */
  onStatus: (status: WhepStatus) => void;
  /** Optional AbortSignal to cancel in-flight negotiation */
  signal?: AbortSignal;
  /**
   * Seam for testing: factory that creates an RTCPeerConnection.
   * Defaults to `new RTCPeerConnection()`.
   */
  pcFactory?: (config?: RTCConfiguration) => RTCPeerConnection;
  /**
   * Seam for testing: fetch implementation.
   * Defaults to the global `fetch`.
   */
  fetchImpl?: typeof fetch;
}

/**
 * Negotiate a WHEP session.
 *
 * - Adds recvonly video + audio transceivers.
 * - Creates an offer, waits for ICE gathering (complete or 2 s timeout).
 * - POSTs the offer SDP to `url` with `Content-Type: application/sdp` (exact).
 * - On success: sets the answer, wires ontrack → video.srcObject, maps
 *   connectionstatechange → onStatus.
 * - Returns a handle whose `close()` sends a best-effort DELETE + pc.close().
 * - Never throws past onStatus("failed").
 */
export async function playWhep(opts: WhepOptions): Promise<WhepHandle> {
  const {
    url,
    video,
    onStatus,
    signal,
    pcFactory = (cfg?: RTCConfiguration) => new RTCPeerConnection(cfg),
    fetchImpl = fetch,
  } = opts;

  onStatus("connecting");

  const pc = pcFactory();

  // Resource URL discovered from the Location response header (for teardown).
  let resourceUrl: string | null = null;
  let closed = false;

  const handle: WhepHandle = {
    close() {
      closed = true;
      // Best-effort DELETE of the WHEP resource.
      if (resourceUrl) {
        void fetchImpl(resourceUrl, { method: "DELETE" }).catch(() => {
          /* ignore */
        });
      }
      pc.close();
    },
  };

  try {
    // Add recvonly transceivers so the offer contains the relevant m-lines.
    pc.addTransceiver("video", { direction: "recvonly" });
    pc.addTransceiver("audio", { direction: "recvonly" });

    // Wire track event before offer so we don't miss an early track.
    // Guard against the tile being torn down before the track arrives.
    pc.ontrack = (ev: RTCTrackEvent) => {
      if (closed) return;
      video.srcObject = ev.streams[0] ?? null;
    };

    // Map connection-state changes to WhepStatus.
    pc.onconnectionstatechange = () => {
      const state = pc.connectionState;
      if (state === "connected") {
        onStatus("live");
      } else if (
        state === "failed" ||
        state === "disconnected" ||
        state === "closed"
      ) {
        onStatus("failed");
      }
    };

    // Create the offer.
    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);

    // Wait for ICE gathering to complete, or timeout after 2 s.
    await waitForIceGathering(pc, 2000, signal);

    // Check abort before sending the network request.
    if (signal?.aborted) {
      onStatus("failed");
      return handle;
    }

    const localSdp = pc.localDescription?.sdp ?? offer.sdp ?? "";

    // POST the offer SDP — Content-Type must be exactly "application/sdp".
    const res = await fetchImpl(url, {
      method: "POST",
      headers: { "Content-Type": "application/sdp" },
      body: localSdp,
      signal,
    });

    if (!res.ok) {
      onStatus("failed");
      return handle;
    }

    // Discover the resource URL for teardown (resolve relative to the origin).
    const loc = res.headers.get("Location");
    if (loc) {
      const base = new URL(url, globalThis.location?.href ?? "http://localhost");
      resourceUrl = new URL(loc, base).href;
    }

    // Set the remote answer.
    const answerSdp = await res.text();
    await pc.setRemoteDescription({ type: "answer", sdp: answerSdp });
  } catch (err) {
    // Absorb everything — never propagate past onStatus.
    if (err instanceof DOMException && err.name === "AbortError") {
      // Cancelled by the caller — not a user-visible failure.
    } else {
      onStatus("failed");
    }
  }

  return handle;
}

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

function waitForIceGathering(
  pc: RTCPeerConnection,
  timeoutMs: number,
  signal?: AbortSignal,
): Promise<void> {
  return new Promise<void>((resolve) => {
    if (pc.iceGatheringState === "complete") {
      resolve();
      return;
    }

    const timer = setTimeout(resolve, timeoutMs);

    const onGatheringChange = () => {
      if (pc.iceGatheringState === "complete") {
        clearTimeout(timer);
        pc.removeEventListener("icegatheringstatechange", onGatheringChange);
        resolve();
      }
    };

    pc.addEventListener("icegatheringstatechange", onGatheringChange);

    signal?.addEventListener("abort", () => {
      clearTimeout(timer);
      pc.removeEventListener("icegatheringstatechange", onGatheringChange);
      resolve();
    });
  });
}
