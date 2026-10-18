import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
// Geist: the same typeface family iFood-ish product UIs use, via fontsource.
import "@fontsource-variable/geist";
import App from "./App";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
