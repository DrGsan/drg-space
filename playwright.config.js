import { defineConfig } from "@playwright/test";
import { existsSync } from "node:fs";
const channel = process.env.DRG_BROWSER_CHANNEL || undefined;
const executablePath =
  process.env.CHROMIUM_PATH ||
  (!channel && existsSync("/usr/bin/chromium") ? "/usr/bin/chromium" : undefined);
export const browserServer={
    command: `${process.env.DRG_PYTHON || ".venv/bin/python"} -m tests.browser_server`,
    url: "http://127.0.0.1:8001/api/health",
    reuseExistingServer: false,
    gracefulShutdown: { signal: 'SIGTERM', timeout: 70000 },
    timeout: 30000,
  };
export default defineConfig({
  testDir: "./tests/browser",
  workers: 1,
  timeout: 60000,
  use: {
    actionTimeout: 10000,
    baseURL: "http://127.0.0.1:8001",
    channel,
    launchOptions: { executablePath, args: ["--no-sandbox", "--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"] },
    headless: true,
    timezoneId: "UTC",
    locale: "ru-RU",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
});
