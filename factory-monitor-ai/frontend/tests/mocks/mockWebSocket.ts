import type {
  WebSocketLike,
  WsFactory,
} from "../../src/hooks/useLiveIncidents";

export class MockWebSocket implements WebSocketLike {
  static instances: MockWebSocket[] = [];
  url: string;
  sent: string[] = [];
  closed = false;
  onopen: ((ev?: unknown) => void) | null = null;
  onmessage: ((ev: { data: string }) => void) | null = null;
  onclose: ((ev?: unknown) => void) | null = null;
  onerror: ((ev?: unknown) => void) | null = null;

  constructor(url: string) {
    this.url = url;
    MockWebSocket.instances.push(this);
  }

  send(data: string): void {
    this.sent.push(data);
  }

  close(): void {
    this.closed = true;
  }

  // --- test helpers ---
  open(): void {
    this.onopen?.();
  }
  emit(envelope: unknown): void {
    this.onmessage?.({ data: JSON.stringify(envelope) });
  }
  serverClose(): void {
    this.onclose?.();
  }

  static reset(): void {
    MockWebSocket.instances = [];
  }
  static last(): MockWebSocket {
    return MockWebSocket.instances[MockWebSocket.instances.length - 1];
  }
}

export const mockWsFactory: WsFactory = (url: string) => new MockWebSocket(url);
