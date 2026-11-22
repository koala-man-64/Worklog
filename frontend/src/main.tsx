import "./styles/base.css";
import "./styles/today.css";
import "./styles/work.css";
import "./styles/pull-requests.css";
import "./styles/roadmaps.css";
import "./styles/people.css";
import "./styles/resources.css";
import "./styles/agents.css";
import "./styles/live.css";
import "./styles/chats.css";
import "./styles/discussions.css";
import "./styles/review.css";
import "./styles/settings.css";
import "./styles/usage.css";
import "./styles/guide.css";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
import { createQueryClient } from "./hooks";
const client = createQueryClient();
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={client}>
      <App />
    </QueryClientProvider>
  </StrictMode>,
);
