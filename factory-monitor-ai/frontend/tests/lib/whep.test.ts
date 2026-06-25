import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { playWhep, type WhepStatus } from "../../src/lib/whep";

// ---------------------------------------------------------------------------
// FakePC — a controllable RTCPeerConnection stand-in
// ---------------------------------------------------------------------------

type EventCallback = (ev: Event) => void;

class FakePC {
  iceGatheringState: RTCIceGatheringState = "complete";
  connectionState: RTCPeerConnectionState = "new";

  localDescription: RTCSessionDescription | null = null;

  private _listeners: Record<string, EventCallback[]> = {};

  // Spied methods
  addTransceiver = vi.fn();
  createOffer = vi.fn<[], Promise<RTCSessionDescriptionInit>>();
  setLocalDescription = vi.fn<[RTCSessionDescriptionInit], Promise<void>>();
  setRemoteDescription = vi.fn<
    [RTCSessionDescriptionInit],
    Promise<void>
  >();
  close = vi.fn();

  // Mirror EventTarget interface subset used by whep.ts
  ontrack: ((ev: RTCTrackEvent) => void) | null = null;
  onconnectionstatechange: (() => void) | null = null;

  addEventListener(type: string, cb: EventCallback) {
    if (!this._listeners[type]) this._listeners[type] = [];
    this._listeners[type].push(cb);
  }

  removeEventListener(type: string, cb: EventCallback) {
    if (!this._listeners[type]) return;
    this._listeners[type] = this._listeners[type].filter((f) => f !== cb);
  }

  /** Fire `icegatheringstatechange` (the waiting helper listens for this) */
  fireGatheringComplete() {
    this.iceGatheringState = "complete";
    const handlers = this._listeners["icegatheringstatechange"] ?? [];
    handlers.forEach((h) => h(new Event("icegatheringstatechange")));
  }

  /** Fire a fake track event */
  fireTrack(stream: MediaStream) {
    if (this.ontrack) {
      const ev = Object.assign(new Event("track"), {
        streams: [stream],
      }) as unknown as RTCTrackEvent;
      this.ontrack(ev);
    }
  }

  /** Simulate a connection-state change */
  setConnectionState(state: RTCPeerConnectionState) {
    this.connectionState = state;
    if (this.onconnectionstatechange) {
      this.onconnectionstatechange();
    }
  }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const OFFER_SDP = "v=0\r\no=- 0 0 IN IP4 127.0.0.1\r\ns=offer\r\n";
const ANSWER_SDP = "v=0\r\no=- 1 1 IN IP4 127.0.0.1\r\ns=answer\r\n";
const WHEP_URL = "/whep/cam_01/whep";
const RESOURCE_URL = "/whep/cam_01/whep/session/abc123";

function makeFakeVideo(): HTMLVideoElement {
  return {
    srcObject: null,
  } as unknown as HTMLVideoElement;
}

function makeFakeFetch(opts: {
  status?: number;
  answerSdp?: string;
  location?: string;
}) {
  const {
    status = 201,
    answerSdp = ANSWER_SDP,
    location = RESOURCE_URL,
  } = opts;

  return vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    headers: {
      get: (name: string) => (name === "Location" ? location : null),
    },
    text: () => Promise.resolve(answerSdp),
  });
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("playWhep", () => {
  let pc: FakePC;
  let fetchMock: ReturnType<typeof makeFakeFetch>;
  let video: HTMLVideoElement;
  let statuses: WhepStatus[];

  beforeEach(() => {
    pc = new FakePC();
    // When iceGatheringState is already "complete" the helper resolves immediately.
    pc.iceGatheringState = "complete";

    pc.createOffer.mockResolvedValue({
      type: "offer",
      sdp: OFFER_SDP,
    } as RTCSessionDescriptionInit);

    pc.setLocalDescription.mockImplementation(
      (desc: RTCSessionDescriptionInit) => {
        pc.localDescription = desc as unknown as RTCSessionDescription;
        return Promise.resolve();
      },
    );

    pc.setRemoteDescription.mockResolvedValue(undefined);
    pc.addTransceiver.mockReturnValue({} as RTCRtpTransceiver);

    fetchMock = makeFakeFetch({});
    video = makeFakeVideo();
    statuses = [];
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  // -------------------------------------------------------------------------
  it("POSTs to the WHEP URL with correct method and Content-Type: application/sdp", async () => {
    await playWhep({
      url: WHEP_URL,
      video,
      onStatus: (s) => statuses.push(s),
      pcFactory: () => pc as unknown as RTCPeerConnection,
      fetchImpl: fetchMock,
    });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [calledUrl, calledInit] = fetchMock.mock.calls[0] as [
      string,
      RequestInit,
    ];
    expect(calledUrl).toBe(WHEP_URL);
    expect(calledInit.method).toBe("POST");
    expect((calledInit.headers as Record<string, string>)["Content-Type"]).toBe(
      "application/sdp",
    );
  });

  // -------------------------------------------------------------------------
  it("sends the offer SDP as the POST body", async () => {
    await playWhep({
      url: WHEP_URL,
      video,
      onStatus: (s) => statuses.push(s),
      pcFactory: () => pc as unknown as RTCPeerConnection,
      fetchImpl: fetchMock,
    });

    const [, calledInit] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(calledInit.body).toContain(OFFER_SDP);
  });

  // -------------------------------------------------------------------------
  it("adds recvonly video and audio transceivers before creating the offer", async () => {
    await playWhep({
      url: WHEP_URL,
      video,
      onStatus: (s) => statuses.push(s),
      pcFactory: () => pc as unknown as RTCPeerConnection,
      fetchImpl: fetchMock,
    });

    expect(pc.addTransceiver).toHaveBeenCalledTimes(2);
    expect(pc.addTransceiver).toHaveBeenCalledWith("video", {
      direction: "recvonly",
    });
    expect(pc.addTransceiver).toHaveBeenCalledWith("audio", {
      direction: "recvonly",
    });
    // Both transceiver calls must happen before createOffer
    const addOrder = pc.addTransceiver.mock.invocationCallOrder;
    const offerOrder = pc.createOffer.mock.invocationCallOrder[0];
    addOrder.forEach((order) => expect(order).toBeLessThan(offerOrder));
  });

  // -------------------------------------------------------------------------
  it("calls setRemoteDescription with the answer SDP from the response", async () => {
    await playWhep({
      url: WHEP_URL,
      video,
      onStatus: (s) => statuses.push(s),
      pcFactory: () => pc as unknown as RTCPeerConnection,
      fetchImpl: fetchMock,
    });

    expect(pc.setRemoteDescription).toHaveBeenCalledWith({
      type: "answer",
      sdp: ANSWER_SDP,
    });
  });

  // -------------------------------------------------------------------------
  it("sets video.srcObject when a track arrives", async () => {
    const handle = await playWhep({
      url: WHEP_URL,
      video,
      onStatus: (s) => statuses.push(s),
      pcFactory: () => pc as unknown as RTCPeerConnection,
      fetchImpl: fetchMock,
    });

    const stream = new MediaStream();
    pc.fireTrack(stream);

    expect(video.srcObject).toBe(stream);
    handle.close();
  });

  // -------------------------------------------------------------------------
  it('maps connectionState "connected" → status "live"', async () => {
    const handle = await playWhep({
      url: WHEP_URL,
      video,
      onStatus: (s) => statuses.push(s),
      pcFactory: () => pc as unknown as RTCPeerConnection,
      fetchImpl: fetchMock,
    });

    pc.setConnectionState("connected");

    expect(statuses).toContain("live");
    handle.close();
  });

  // -------------------------------------------------------------------------
  it("close() sends DELETE to the Location URL and calls pc.close()", async () => {
    const handle = await playWhep({
      url: WHEP_URL,
      video,
      onStatus: (s) => statuses.push(s),
      pcFactory: () => pc as unknown as RTCPeerConnection,
      fetchImpl: fetchMock,
    });

    handle.close();

    // Give the microtask queue a turn so the best-effort DELETE runs.
    await Promise.resolve();

    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining(RESOURCE_URL),
      { method: "DELETE" },
    );
    expect(pc.close).toHaveBeenCalledTimes(1);
  });

  // -------------------------------------------------------------------------
  it('emits status "failed" when the server returns a non-2xx response', async () => {
    const errorFetch = makeFakeFetch({ status: 400 });

    await playWhep({
      url: WHEP_URL,
      video,
      onStatus: (s) => statuses.push(s),
      pcFactory: () => pc as unknown as RTCPeerConnection,
      fetchImpl: errorFetch,
    });

    expect(statuses).toContain("failed");
    expect(statuses).not.toContain("live");
  });

  // -------------------------------------------------------------------------
  it("emits connecting before any network call", async () => {
    // "connecting" is the very first synchronous action in playWhep, before any
    // await (createOffer, setLocalDescription, ICE gathering, POST).  We verify
    // this by simply checking statuses after a fully-resolved run — the first
    // element must always be "connecting".
    await playWhep({
      url: WHEP_URL,
      video,
      onStatus: (s) => statuses.push(s),
      pcFactory: () => pc as unknown as RTCPeerConnection,
      fetchImpl: fetchMock,
    });

    expect(statuses[0]).toBe("connecting");
  });
});
