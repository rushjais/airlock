import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import App from "./App";
import Approve from "./Approve";

try { if (localStorage.getItem("airlock-theme") === "light") document.documentElement.classList.add("light"); } catch { /* storage blocked */ }
const Page = location.pathname.startsWith("/approve/") ? Approve : App;
createRoot(document.getElementById("root")!).render(<StrictMode><Page /></StrictMode>);
