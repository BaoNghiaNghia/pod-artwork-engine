/**
 * Desktop UI release smoke test.
 * Uses system Chrome (Playwright does not download a bundled browser).
 * Prerequisite: npm run dev on http://127.0.0.1:1420 and the local POD engine.
 * No screenshots, credentials, or user artwork are stored.
 */
import { chromium } from "playwright";

const baseURL = process.env.POD_UI_QA_URL || "http://127.0.0.1:1420";
const sizes = [
  { width: 1280, height: 800, name: "desktop" },
  { width: 1024, height: 768, name: "small-desktop" },
  { width: 768, height: 1024, name: "tablet" },
  { width: 390, height: 844, name: "mobile" },
];
const views = [
  { tab: "Artwork", heading: "Artwork Reconstruction" },
  { tab: "AI Image Chat", heading: "AI Image Studio" },
  { tab: "Datasets", heading: "Historical Dataset" },
  { tab: "AI Settings", heading: "AI Reconstruction" },
];

async function run() {
  const browser = await chromium.launch({
    channel: process.env.POD_UI_QA_BROWSER || "chrome",
    headless: true,
  });
  let checks = 0;
  try {
    for (const viewport of sizes) {
      const context = await browser.newContext({ viewport, deviceScaleFactor: 1 });
      const page = await context.newPage();
      const runtimeErrors = [];
      page.on("pageerror", (error) => runtimeErrors.push(error.message));
      // The QA frontend may run against the previous installed engine.
      // Mock only the newly introduced, read-only safe diagnostic snapshot.
      await page.route("http://127.0.0.1:8765/system/diagnostics", async (route) => {
        await route.fulfill({ status: 200, contentType: "application/json",
          body: JSON.stringify({
            schema_version: 1, app_version: "0.1.12", generated_at: new Date().toISOString(),
            recent_event_sample: { count: 3, window_hours: 24, max_scanned: 100, error_or_failure_count: 0, error_level_count: 0 },
          }),
        });
      });
      try {
        await page.goto(baseURL, { waitUntil: "networkidle", timeout: 25000 });
        await page.getByRole("navigation", { name: "Tool sections" }).waitFor();
        for (const view of views) {
          await page.getByRole("navigation", { name: "Tool sections" })
            .getByRole("button", { name: view.tab, exact: true }).click();
          await page.getByRole("heading", { name: view.heading, exact: true }).waitFor();
          const overflow = await page.evaluate(() => {
            const root = document.documentElement;
            const width = Math.min(window.innerWidth, root.clientWidth);
            return { documentWidth: root.scrollWidth, viewportWidth: width };
          });
          if (overflow.documentWidth > overflow.viewportWidth + 2) {
            throw new Error(`${viewport.name}/${view.tab} horizontal overflow: ${overflow.documentWidth}px exceeds ${overflow.viewportWidth}px`);
          }
          if (view.tab === "Artwork") {
            if (await page.getByRole("button", { name: "Generate artwork" }).isEnabled()) {
              throw new Error("Generate is enabled with no reference images");
            }
          }
          if (view.tab === "AI Image Chat") {
            if (await page.getByRole("button", { name: "Tạo phiên bản mới" }).isEnabled()) {
              throw new Error("Image chat sends before attaching artwork and configuring key");
            }
          }
          if (view.tab === "AI Settings") {
            await page.getByText("Advanced connection options").click();
            await page.getByRole("textbox", { name: "Provider name" }).waitFor();
            await page.getByRole("heading", { name: "Version & updates" }).waitFor();
            await page.getByRole("heading", { name: "Logger" }).waitFor();
            await page.getByRole("button", { name: "Check for updates" }).waitFor();
            await page.getByRole("link", { name: "Export sanitized diagnostics report" }).waitFor();
          }
          checks++;
        }
        if (runtimeErrors.length) {
          throw new Error(`Runtime exceptions (${viewport.name}): ${runtimeErrors.join(" | ")}`);
        }
        console.log(`PASS ${viewport.name} ${viewport.width}x${viewport.height}: 4 screens; no horizontal overflow`);
      } finally {
        await context.close();
      }
    }
    console.log(`PASS ${checks} screen/viewport checks; no user files retained`);
  } finally {
    await browser.close();
  }
}

run().catch((error) => {
  console.error(`FAIL UI QA: ${error.message}`);
  process.exitCode = 1;
});
