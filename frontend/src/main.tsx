import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { RouterProvider } from "react-router-dom";
import { AppProviders } from "./app/providers";
import { router } from "./app/router";
import "./styles/tokens.css";
import "./styles/base.css";
import "./components/ui/controls.css";
import "./components/ui/display.css";
import "./components/ui/overlay.css";
import "./components/feedback/states.css";
import "./components/layout/shell.css";
import "./features/pages.css";
import "./features/auth/auth.css";
import "./features/landing/landing.css";

const root = document.getElementById("root");
if (!root) throw new Error("ThreadLine: #root element is missing.");

createRoot(root).render(
  <StrictMode>
    <AppProviders>
      <RouterProvider router={router} />
    </AppProviders>
  </StrictMode>,
);
