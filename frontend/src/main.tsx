import "./styles/base.css";
import "./styles/today.css";
import "./styles/work.css";
import "./styles/reminders.css";
import "./styles/roadmaps.css";
import "./styles/people.css";
import "./styles/resources.css";
import "./styles/agents.css";
import "./styles/chats.css";
import "./styles/discussions.css";
import "./styles/review.css";
import "./styles/settings.css";
import "./styles/usage.css";
import "./styles/guide.css";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
const client = new QueryClient({
  defaultOptions: {
    queries: { retry: 1, refetchOnWindowFocus: true },
    mutations: { retry: false },
  },
});
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={client}>
      <App />
    </QueryClientProvider>
  </StrictMode>,
);
