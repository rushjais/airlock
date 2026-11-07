import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import App from "./App";
import Approve from "./Approve";
import TryIt from "./TryIt";

try { if (localStorage.getItem("airlock-theme") === "light") document.documentElement.classList.add("light"); } catch { /* storage blocked */ }
const root = createRoot(document.getElementById("root")!);
const render = (Page: () => React.JSX.Element) => root.render(<StrictMode><Page /></StrictMode>);

// /approve/... is the admin approval screen, /console the full operator console. "/" is the Try-it page when the
// server runs in DEMO_MODE (the hosted demo), and the console otherwise (local setup with real integrations).
if (location.pathname.startsWith("/approve/")) render(Approve);
else if (location.pathname.startsWith("/console")) render(App);
else fetch("/health").then((r) => r.json()).then((h) => render(h.demo_mode ? TryIt : App)).catch(() => render(App));
