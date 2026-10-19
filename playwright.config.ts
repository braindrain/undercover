import { defineConfig } from "@playwright/test";

// The tests run on their own servers and database, apart from npm run dev.
const backendUrl = "http://127.0.0.1:8001";
const dashboardUrl = "http://localhost:5174";

export default defineConfig({
  testDir: "e2e",
  outputDir: "e2e/test-results",
  reporter: "list",
  use: { baseURL: dashboardUrl },
  webServer: [
    {
      command:
        "uv run -m backend.seed && uv run -m uvicorn backend.main:app --port 8001",
      url: `${backendUrl}/api/health`,
      env: { DATABASE_PATH: "e2e/undercover-e2e.db" },
      reuseExistingServer: false,
    },
    {
      command: "npx vite dashboard --port 5174 --strictPort",
      url: dashboardUrl,
      env: { BACKEND_URL: backendUrl },
      reuseExistingServer: false,
    },
  ],
});
