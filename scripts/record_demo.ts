/**
 * record_demo.ts — Playwright walkthrough for the Engram dashboard demo video.
 *
 * Uses Playwright's built-in recordVideo to produce a .webm file.
 * Expects engram-bridge to be running with seeded data (see seed_demo.py).
 *
 * Usage:
 *   npx tsx scripts/record_demo.ts [output-dir]
 *
 * Storyboard (8 scenes, ~45s total):
 *   1. /board      — Kanban loads with issues across 4 columns
 *   2. /board      — Drag issue from Backlog → In Progress
 *   3. /board      — Click issue card to show detail panel
 *   4. /memory     — Navigate to Memory, browse items
 *   5. /memory     — Search "decay curves"
 *   6. /coordination — Navigate to Agents, view registry
 *   7. /coordination — Click "Route All Pending"
 *   8. /           — Navigate to Chat, type a message
 */

import { chromium, type Page, type BrowserContext } from "@playwright/test";
import * as path from "path";
import * as fs from "fs";

const BASE = "http://127.0.0.1:8200";
const VIEWPORT = { width: 1440, height: 900 };

// Output directory (default: cwd)
const outputDir = process.argv[2] || process.cwd();

async function sleep(ms: number) {
  return new Promise((r) => setTimeout(r, ms));
}

/**
 * Slowly type text into the focused element for visual effect.
 */
async function slowType(page: Page, text: string, delay = 80) {
  for (const char of text) {
    await page.keyboard.type(char, { delay });
  }
}

/**
 * Perform a visual drag using mouse events.
 * Moves slowly for the video recording.
 */
async function visualDrag(
  page: Page,
  fromSelector: string,
  toSelector: string,
  steps = 30,
) {
  const from = page.locator(fromSelector).first();
  const to = page.locator(toSelector).first();

  const fromBox = await from.boundingBox();
  const toBox = await to.boundingBox();
  if (!fromBox || !toBox) {
    console.warn("  [warn] Could not find drag source/target, skipping drag");
    return;
  }

  const startX = fromBox.x + fromBox.width / 2;
  const startY = fromBox.y + fromBox.height / 2;
  const endX = toBox.x + toBox.width / 2;
  const endY = toBox.y + toBox.height / 2;

  await page.mouse.move(startX, startY);
  await sleep(200);
  await page.mouse.down();
  await sleep(300);

  // Move in steps for smooth visual drag
  for (let i = 1; i <= steps; i++) {
    const ratio = i / steps;
    const x = startX + (endX - startX) * ratio;
    const y = startY + (endY - startY) * ratio;
    await page.mouse.move(x, y);
    await sleep(30);
  }

  await sleep(200);
  await page.mouse.up();
  await sleep(500);
}

/**
 * Navigate using the AppBar tab buttons.
 */
async function navigateTo(page: Page, label: string) {
  // AppBar nav buttons contain icon + text
  const btn = page.locator("button", { hasText: label }).first();
  await btn.click();
  await page.waitForLoadState("networkidle");
  await sleep(1000);
}

async function main() {
  console.log("Launching Chromium with video recording...");

  const videoDir = path.join(outputDir, "playwright-videos");
  fs.mkdirSync(videoDir, { recursive: true });

  const browser = await chromium.launch({ headless: true });
  const context: BrowserContext = await browser.newContext({
    viewport: VIEWPORT,
    deviceScaleFactor: 2,
    recordVideo: {
      dir: videoDir,
      size: VIEWPORT,
    },
  });

  const page = await context.newPage();

  try {
    // ═══════════════════════════════════════════
    // Scene 1: Board view — Kanban with issues
    // ═══════════════════════════════════════════
    console.log("Scene 1: Board view loads...");
    await page.goto(`${BASE}/board`, { waitUntil: "networkidle" });
    await sleep(3000); // Let the board render fully
    await sleep(2000); // Hold for viewing

    // ═══════════════════════════════════════════
    // Scene 2: Drag issue from first column to second
    // ═══════════════════════════════════════════
    console.log("Scene 2: Drag issue between columns...");
    // Try to find the first card in the first column and drag to second column
    // @hello-pangea/dnd uses data-rbd-draggable-id and data-rbd-droppable-id
    const firstCard = page
      .locator('[data-rbd-draggable-id]')
      .first();
    const secondDroppable = page
      .locator('[data-rbd-droppable-id]')
      .nth(1);

    const cardBox = await firstCard.boundingBox();
    const dropBox = await secondDroppable.boundingBox();

    if (cardBox && dropBox) {
      await visualDrag(
        page,
        "[data-rbd-draggable-id]",
        "[data-rbd-droppable-id] >> nth=1",
        25,
      );
    } else {
      // Fallback: just hover over the board for visual effect
      console.log("  [info] Drag targets not found, hovering instead");
      await page.mouse.move(400, 450);
      await sleep(1000);
      await page.mouse.move(700, 450);
      await sleep(1000);
    }
    await sleep(2000);

    // ═══════════════════════════════════════════
    // Scene 3: Click an issue card to show detail
    // ═══════════════════════════════════════════
    console.log("Scene 3: Click issue card...");
    // Click on a card title to open detail
    const issueCard = page
      .locator("h4")
      .filter({ hasText: /Fix Python|Optimize vector/ })
      .first();
    if (await issueCard.isVisible()) {
      await issueCard.click();
    } else {
      // Click any visible card
      const anyCard = page.locator('[data-rbd-draggable-id]').first();
      if (await anyCard.isVisible()) {
        await anyCard.click();
      }
    }
    await sleep(4000);

    // Close detail if a modal/panel opened (press Escape)
    await page.keyboard.press("Escape");
    await sleep(500);

    // ═══════════════════════════════════════════
    // Scene 4: Navigate to Memory view
    // ═══════════════════════════════════════════
    console.log("Scene 4: Navigate to Memory...");
    await page.goto(`${BASE}/memory`, { waitUntil: "networkidle" });
    await sleep(2000);

    // Browse: click on a category if visible
    const categoryBtn = page
      .locator("button")
      .filter({ hasText: /All/i })
      .first();
    if (await categoryBtn.isVisible()) {
      await categoryBtn.click();
      await sleep(1500);
    }

    // Click on a memory item if any visible
    const memoryItem = page.locator("button.w-full.text-left").first();
    if (await memoryItem.isVisible()) {
      await memoryItem.click();
      await sleep(2000);
    }
    await sleep(1500);

    // ═══════════════════════════════════════════
    // Scene 5: Search "decay curves" in Memory
    // ═══════════════════════════════════════════
    console.log('Scene 5: Search "decay curves"...');
    const memorySearch = page.locator(
      'input[placeholder="Search memories..."]',
    );
    if (await memorySearch.isVisible()) {
      await memorySearch.click();
      await slowType(page, "decay curves", 100);
      await sleep(3000); // Wait for search results
    }
    await sleep(2000);

    // ═══════════════════════════════════════════
    // Scene 6: Navigate to Coordination/Agents
    // ═══════════════════════════════════════════
    console.log("Scene 6: Navigate to Agents...");
    await page.goto(`${BASE}/coordination`, { waitUntil: "networkidle" });
    await sleep(2000);

    // Click on an agent to show detail
    const agentCard = page
      .locator("button")
      .filter({ hasText: /claude-code/i })
      .first();
    if (await agentCard.isVisible()) {
      await agentCard.click();
      await sleep(2000);
    }
    await sleep(2000);

    // ═══════════════════════════════════════════
    // Scene 7: Click "Route All Pending"
    // ═══════════════════════════════════════════
    console.log('Scene 7: Click "Route All Pending"...');
    const routeBtn = page
      .locator("button")
      .filter({ hasText: "Route All Pending" });
    if (await routeBtn.isVisible()) {
      await routeBtn.click();
      await sleep(3000); // Wait for routing animation
    } else {
      console.log("  [info] Route All Pending not visible (no unassigned tasks?)");
      await sleep(2000);
    }
    await sleep(1000);

    // ═══════════════════════════════════════════
    // Scene 8: Navigate to Chat, type a message
    // ═══════════════════════════════════════════
    console.log("Scene 8: Navigate to Chat...");
    await page.goto(`${BASE}/`, { waitUntil: "networkidle" });
    await sleep(2000);

    const chatInput = page.locator(
      'textarea[placeholder*="Send a message"]',
    );
    if (await chatInput.isVisible()) {
      await chatInput.click();
      await slowType(
        page,
        "Create a new issue: benchmark memory retrieval latency across 100k items",
        60,
      );
      await sleep(2000);
    }
    await sleep(2000);

    console.log("Recording complete!");
  } finally {
    // Close page + context to finalize video
    await page.close();
    await context.close();
    await browser.close();
  }

  // Find the recorded .webm file
  const files = fs.readdirSync(videoDir).filter((f) => f.endsWith(".webm"));
  if (files.length === 0) {
    console.error("ERROR: No .webm files found in", videoDir);
    process.exit(1);
  }

  // Move the most recent recording to output dir as demo.webm
  const latestVideo = files.sort().pop()!;
  const src = path.join(videoDir, latestVideo);
  const dest = path.join(outputDir, "demo.webm");
  fs.copyFileSync(src, dest);
  console.log(`Video saved: ${dest}`);

  // Cleanup temp dir
  for (const f of fs.readdirSync(videoDir)) {
    fs.unlinkSync(path.join(videoDir, f));
  }
  fs.rmdirSync(videoDir);
}

main().catch((err) => {
  console.error("Recording failed:", err);
  process.exit(1);
});
