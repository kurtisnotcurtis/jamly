import { spawn } from "node:child_process";
import fs from "node:fs/promises";
import fsSync from "node:fs";
import { createRequire } from "node:module";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(HERE, "..");
const E2E_DIR = path.join(REPO_ROOT, "tests", "e2e");
const OUTPUT_PATH = path.join(REPO_ROOT, "site", "companion-preview.png");

const requireFromE2E = createRequire(path.join(E2E_DIR, "package.json"));
const { chromium } = requireFromE2E("playwright-core");

function findBrowser() {
  if (process.env.CHROME_BIN && fsSync.existsSync(process.env.CHROME_BIN)) {
    return process.env.CHROME_BIN;
  }
  const byPlatform = {
    linux: [
      "/usr/bin/google-chrome",
      "/usr/bin/google-chrome-stable",
      "/usr/bin/chromium",
      "/usr/bin/chromium-browser",
      "/usr/bin/microsoft-edge",
    ],
    darwin: [
      "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
      "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
      "/Applications/Chromium.app/Contents/MacOS/Chromium",
    ],
    win32: [
      path.join(process.env.PROGRAMFILES || "C:/Program Files", "Google/Chrome/Application/chrome.exe"),
      path.join(process.env["PROGRAMFILES(X86)"] || "C:/Program Files (x86)", "Google/Chrome/Application/chrome.exe"),
      path.join(process.env["PROGRAMFILES(X86)"] || "C:/Program Files (x86)", "Microsoft/Edge/Application/msedge.exe"),
      path.join(process.env.PROGRAMFILES || "C:/Program Files", "Microsoft/Edge/Application/msedge.exe"),
    ],
  };
  const found = (byPlatform[process.platform] || []).find(candidate => fsSync.existsSync(candidate));
  if (!found) {
    throw new Error("No Chrome, Chromium, or Edge binary found; set CHROME_BIN.");
  }
  return found;
}

function findPython() {
  if (process.env.JAMLY_PYTHON) return process.env.JAMLY_PYTHON;
  const venvPython = path.join(
    REPO_ROOT,
    process.platform === "win32" ? ".venv/Scripts/python.exe" : ".venv/bin/python",
  );
  if (fsSync.existsSync(venvPython)) return venvPython;
  return process.platform === "win32" ? "python" : "python3";
}

async function startMockServer(root) {
  const stateDir = path.join(root, "state");
  const outputDir = path.join(root, "output");
  const env = { ...process.env };
  for (const key of Object.keys(env)) {
    if (key.startsWith("JAMLY_")) delete env[key];
  }
  const proc = spawn(
    findPython(),
    [
      path.join(E2E_DIR, "mock_server.py"),
      "--state-dir", stateDir,
      "--output-dir", outputDir,
      "--tone-seconds", "12",
    ],
    { env, stdio: ["ignore", "pipe", "pipe"] },
  );
  let stderr = "";
  proc.stderr.on("data", chunk => { stderr += chunk; });

  const info = await new Promise((resolve, reject) => {
    let buffer = "";
    const timer = setTimeout(() => {
      try { proc.kill("SIGKILL"); } catch (_err) {}
      reject(new Error("Timed out starting mock_server: " + stderr));
    }, 30000);
    proc.stdout.on("data", chunk => {
      buffer += chunk;
      const newline = buffer.indexOf("\n");
      if (newline >= 0) {
        clearTimeout(timer);
        resolve(JSON.parse(buffer.slice(0, newline)));
      }
    });
    proc.on("exit", code => {
      clearTimeout(timer);
      reject(new Error(`mock_server exited early (${code}): ${stderr}`));
    });
  });

  async function stop() {
    if (proc.exitCode !== null || proc.signalCode !== null) return;
    try {
      await fetch(info.url + "/app/stop", {
        method: "POST",
        headers: { Authorization: "Bearer " + info.token, "Content-Type": "application/json" },
        body: "{}",
      });
    } catch (_err) {
      // Server may already be stopping.
    }
    await new Promise(resolve => {
      const killTimer = setTimeout(() => {
        try { proc.kill(); } catch (_err) {}
        resolve();
      }, 5000);
      proc.once("exit", () => {
        clearTimeout(killTimer);
        resolve();
      });
    });
  }

  return { info, stop };
}

async function main() {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "jamly-site-preview-"));
  const server = await startMockServer(root);
  const browser = await chromium.launch({
    executablePath: findBrowser(),
    headless: true,
    args: ["--no-first-run", "--no-default-browser-check"],
  });
  try {
    const context = await browser.newContext({
      viewport: { width: 960, height: 1380 },
      deviceScaleFactor: 1.5,
      colorScheme: "dark",
    });
    const page = await context.newPage();

    await page.goto(server.info.launch_file);
    await page.waitForSelector("#workspace:not([hidden])");

    // Create one completed 6-stem WAV export so the Exports card and practice player are populated.
    await page.selectOption("#model", "htdemucs_6s");
    await page.selectOption("#format", "wav");
    await page.fill("#inputs", "https://example.test/session-groove");
    await page.click("#submit");
    await page.waitForSelector("#jobs .status-badge.done");

    // Load drums stem into the docked practice player and set practice speed to 80%.
    await page.locator('#jobs button.player-toggle[aria-label="drums"]').first().click();
    await page.waitForSelector("#player:not([hidden])");
    await page.waitForFunction(() => {
      const audio = document.getElementById("player-audio");
      return audio.readyState >= 1 && Number.isFinite(audio.duration) && audio.duration >= 10;
    });
    await page.locator("#player-speed").fill("80");
    await page.locator("#player-speed").dispatchEvent("input");
    await page.evaluate(() => {
      const audio = document.getElementById("player-audio");
      audio.pause();
      audio.currentTime = 5;
      document.getElementById("inputs").value =
        "https://youtu.be/dQw4w9WgXcQ\n~/Music/Session_Take_04.wav";
      document.getElementById("output-dir").value = "~/Music/jamly";
      document.getElementById("connection").textContent =
        "Connected to your local companion. Jobs keep running even after you close this page.";
      document.activeElement?.blur();
      window.scrollTo(0, 0);
    });
    // Size the viewport height tightly so the completed export card sits cleanly above the docked player.
    const targetHeight = await page.evaluate(() => {
      const jobsBottom = document.getElementById("jobs").getBoundingClientRect().bottom;
      const playerHeight = document.getElementById("player").getBoundingClientRect().height;
      return Math.ceil(jobsBottom + playerHeight + 12);
    });
    await page.setViewportSize({ width: 960, height: targetHeight });
    await page.mouse.move(0, 0);
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.waitForTimeout(150);

    await page.screenshot({ path: OUTPUT_PATH, type: "png" });
    console.log(`Wrote ${OUTPUT_PATH} (${960}x${targetHeight} @1.5x)`);
  } finally {
    await browser.close();
    await server.stop();
    await fs.rm(root, { recursive: true, force: true }).catch(() => {});
  }
}

await main();
