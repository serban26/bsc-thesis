import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./styles/base.css";
import "./styles/layout.css";
import "./styles/controls.css";
import "./styles/status.css";
import "./styles/results.css";

const container = document.getElementById("root");
const root = createRoot(container);
root.render(<App />);
