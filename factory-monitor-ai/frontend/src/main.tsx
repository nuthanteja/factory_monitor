import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { AppProviders } from "./providers";
import { setupBrowserTelemetry } from "./telemetry/setup";

setupBrowserTelemetry(); // inert no-op unless an endpoint is configured

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <AppProviders>
      <App />
    </AppProviders>
  </React.StrictMode>,
);
