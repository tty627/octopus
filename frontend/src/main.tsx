import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import * as Tooltip from "@radix-ui/react-tooltip";
import App from "./App";
import "./styles.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 10_000, refetchOnWindowFocus: false },
    mutations: { retry: false },
  },
});

const root = document.getElementById("root");
if (!root) throw new Error("Octopus UI root is missing");

createRoot(root).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <Tooltip.Provider delayDuration={450}>
        <App />
      </Tooltip.Provider>
    </QueryClientProvider>
  </StrictMode>,
);
