import "@testing-library/jest-dom/vitest";
import { afterAll, afterEach, beforeAll } from "vitest";
import { server } from "../tests/mocks/server";

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

// happy-dom media shims -------------------------------------------------------

// 1. Writable srcObject (happy-dom omits it entirely).
//    Per-instance storage via WeakMap so parallel multi-tile tests cannot
//    cross-read each other's stream reference.
if (!Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, "srcObject")) {
  const _srcObjects = new WeakMap<HTMLMediaElement, MediaStream | null>();
  Object.defineProperty(HTMLMediaElement.prototype, "srcObject", {
    get(this: HTMLMediaElement) {
      return _srcObjects.get(this) ?? null;
    },
    set(this: HTMLMediaElement, v: MediaStream | null) {
      _srcObjects.set(this, v);
    },
    configurable: true,
  });
}

// 2. play() → Promise.resolve() stub (happy-dom throws NotImplementedError)
if (typeof HTMLMediaElement.prototype.play !== "function") {
  HTMLMediaElement.prototype.play = () => Promise.resolve();
} else {
  const desc = Object.getOwnPropertyDescriptor(
    HTMLMediaElement.prototype,
    "play",
  );
  if (!desc || desc.value?.toString().includes("NotImplemented")) {
    HTMLMediaElement.prototype.play = () => Promise.resolve();
  }
}
