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
  { tab: "AI Settings", heading: "AI Settings" },
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
      let imageConfigSubmission = null;
      let enhancedRequests = 0;
      page.on("pageerror", (error) => runtimeErrors.push(error.message));
      await page.route("http://127.0.0.1:8765/image-chat/config", async (route) => {
        if (route.request().method() !== "POST") return route.continue();
        imageConfigSubmission = route.request().postDataJSON();
        await route.fulfill({ status: 200, contentType: "application/json",
          body: JSON.stringify({ configured: true, model: imageConfigSubmission.model,
            provider: "OpenAI Images API", key_persistence: "session_only_or_environment" }),
        });
      });
      // Test auto-detect and routed draft processing without uploading a real image
      // or relying on whichever production Engine version is installed.
      const tinyPng = Buffer.from(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScL/nwAAAABJRU5ErkJggg==",
        "base64");
      await page.route("http://127.0.0.1:8765/enhance/*", async (route) => {
        enhancedRequests++;
        if (route.request().url().includes("/analyze")) {
          await route.fulfill({ status: 200, contentType: "application/json",
            body: JSON.stringify({
              category: "sticker_cartoon", mode: "sticker_clean",
              confidence: 0.58, method: "deterministic-local-heuristics-v1",
              reasons: ["Strong outlines", "Flat colors"], scores: {},
              requires_review: true, print_ready: false,
              supported_modes: ["sticker_clean","gentle_clean"],
            }),
          });
        } else {
          await route.fulfill({ status: 200, contentType: "image/png",
            headers: { "X-Enhance-Mode": "sticker_clean", "X-Enhance-Draft": "true" },
            body: tinyPng });
        }
      });
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
            await page.locator('input[type="file"]').first().setInputFiles({
              name: "qa-sticker.png", mimeType: "image/png", buffer: tinyPng,
            });
            await page.getByRole("heading", { name: "Auto Enhance Artwork" }).waitFor();
            await page.getByText("50 MB / file", { exact: true }).waitFor();
            await page.getByRole("img", { name: "Original: qa-sticker.png" }).waitFor();
            await page.getByText("Ảnh gốc", { exact: true }).waitFor();
            await page.getByText("Ảnh sau xử lý", { exact: true }).waitFor();
            await page.getByRole("link", { name: "Download draft PNG" }).waitFor();
            await page.getByRole("img", { name: "Enhanced: qa-sticker.png" }).waitFor();
            if (enhancedRequests !== 2) throw new Error("Upload did not auto-analyze AND auto-enhance");
            await page.getByLabel("Chế độ xử lý").selectOption("sticker_clean");
            await page.getByRole("link", { name: "Download draft PNG" }).waitFor();
            const enhancedOverflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 2);
            if (enhancedOverflow) throw new Error(`${viewport.name}: Auto Enhance horizontal overflow`);
          }
          if (view.tab === "AI Image Chat") {
            await page.getByRole("button", { name: "Mở AI Settings" }).waitFor();
            if (await page.getByRole("textbox", { name: "OpenAI API key" }).count()) {
              throw new Error("Image Chat must not duplicate AI credentials settings");
            }
            if (await page.getByRole("button", { name: "Tạo phiên bản mới" }).isEnabled()) {
              throw new Error("Image chat sends before attaching artwork and configuring key");
            }
            if (viewport.name === "desktop") {
              await page.getByRole("button", { name: "Mở AI Settings" }).click();
              await page.getByRole("heading", { name: "AI Settings", exact: true }).waitFor();
            }
          }
          if (view.tab === "AI Settings") {
            await page.getByRole("heading", { name: "Image AI", exact: true }).waitFor();
            await page.getByRole("heading", { name: "AI Reconstruction", exact: true }).waitFor();
            await page.getByRole("textbox", { name: "Image model" }).waitFor();
            await page.getByRole("textbox", { name: "OpenAI API key" }).waitFor();
            if (viewport.name === "desktop") {
              await page.getByRole("textbox", { name: "Image model" }).fill("gpt-image-1");
              await page.getByRole("textbox", { name: "OpenAI API key" }).fill("sk-qa-not-real");
              await page.getByRole("button", { name: "Save Image AI" }).click();
              await page.getByText("Đã lưu cấu hình Image AI.", { exact: false }).waitFor();
              if (imageConfigSubmission?.api_key !== "sk-qa-not-real" ||
                  imageConfigSubmission?.model !== "gpt-image-1") {
                throw new Error("Centralized Image AI form did not submit the intended configuration");
              }
              if (await page.getByRole("textbox", { name: "OpenAI API key" }).inputValue()) {
                throw new Error("Image API key must be cleared from form after saving");
              }
            }
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
