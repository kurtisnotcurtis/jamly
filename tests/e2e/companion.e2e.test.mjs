import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import fs from "node:fs/promises";
import fsSync from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "node:test";
import { chromium } from "playwright-core";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(HERE, "..", "..");

function browserCandidates({ preferExtensionSupport = false } = {}) {
  const byPlatform = {
    linux: preferExtensionSupport
      ? [
          "/usr/bin/chromium",
          "/usr/bin/chromium-browser",
          "/usr/bin/microsoft-edge",
          "/usr/bin/microsoft-edge-stable",
          "/usr/bin/google-chrome",
          "/usr/bin/google-chrome-stable",
        ]
      : [
          "/usr/bin/google-chrome",
          "/usr/bin/google-chrome-stable",
          "/usr/bin/chromium",
          "/usr/bin/chromium-browser",
          "/usr/bin/microsoft-edge",
          "/usr/bin/microsoft-edge-stable",
        ],
    darwin: preferExtensionSupport
      ? [
          "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
          "/Applications/Chromium.app/Contents/MacOS/Chromium",
          "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        ]
      : [
          "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
          "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
          "/Applications/Chromium.app/Contents/MacOS/Chromium",
        ],
    win32: preferExtensionSupport
      ? [
          path.join(process.env["PROGRAMFILES(X86)"] || "C:/Program Files (x86)", "Microsoft/Edge/Application/msedge.exe"),
          path.join(process.env.PROGRAMFILES || "C:/Program Files", "Microsoft/Edge/Application/msedge.exe"),
          path.join(process.env.PROGRAMFILES || "C:/Program Files", "Google/Chrome/Application/chrome.exe"),
          path.join(process.env["PROGRAMFILES(X86)"] || "C:/Program Files (x86)", "Google/Chrome/Application/chrome.exe"),
        ]
      : [
          path.join(process.env.PROGRAMFILES || "C:/Program Files", "Google/Chrome/Application/chrome.exe"),
          path.join(process.env["PROGRAMFILES(X86)"] || "C:/Program Files (x86)", "Google/Chrome/Application/chrome.exe"),
          path.join(process.env["PROGRAMFILES(X86)"] || "C:/Program Files (x86)", "Microsoft/Edge/Application/msedge.exe"),
          path.join(process.env.PROGRAMFILES || "C:/Program Files", "Microsoft/Edge/Application/msedge.exe"),
        ],
  };
  return byPlatform[process.platform] || [];
}

function findBrowser(options = {}) {
  if (process.env.CHROME_BIN && fsSync.existsSync(process.env.CHROME_BIN)) {
    return process.env.CHROME_BIN;
  }
  const found = browserCandidates(options).find(candidate => fsSync.existsSync(candidate));
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

function makeWavBuffer(seconds = 0.6, sampleRate = 44100, freq = 440) {
  const frames = Math.floor(seconds * sampleRate);
  const buffer = Buffer.alloc(44 + frames * 4);
  buffer.write("RIFF", 0);
  buffer.writeUInt32LE(buffer.length - 8, 4);
  buffer.write("WAVEfmt ", 8);
  buffer.writeUInt32LE(16, 16);
  buffer.writeUInt16LE(1, 20);
  buffer.writeUInt16LE(2, 22);
  buffer.writeUInt32LE(sampleRate, 24);
  buffer.writeUInt32LE(sampleRate * 4, 28);
  buffer.writeUInt16LE(4, 32);
  buffer.writeUInt16LE(16, 34);
  buffer.write("data", 36);
  buffer.writeUInt32LE(frames * 4, 40);
  for (let i = 0; i < frames; i++) {
    const sample = Math.round(7000 * Math.sin((i * 2 * Math.PI * freq) / sampleRate));
    buffer.writeInt16LE(sample, 44 + i * 4);
    buffer.writeInt16LE(sample, 46 + i * 4);
  }
  return buffer;
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
    [path.join(HERE, "mock_server.py"), "--state-dir", stateDir, "--output-dir", outputDir],
    { env, stdio: ["ignore", "pipe", "pipe"] },
  );
  let stderr = "";
  proc.stderr.on("data", chunk => { stderr += chunk; });

  const info = await new Promise((resolve, reject) => {
    let buffer = "";
    const timer = setTimeout(() => reject(new Error("Timed out starting mock_server: " + stderr)), 15000);
    proc.stdout.on("data", chunk => {
      buffer += chunk;
      const newline = buffer.indexOf("\n");
      if (newline >= 0) {
        clearTimeout(timer);
        try {
          resolve(JSON.parse(buffer.slice(0, newline)));
        } catch (err) {
          reject(err);
        }
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
        headers: {
          Authorization: "Bearer " + info.token,
          "Content-Type": "application/json",
        },
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

  return { proc, info, stop };
}

test("companion UI DOM E2E: pairing, uploads, filters, player, extension setup, and shutdown", async t => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "jamly-pw-"));
  const server = await startMockServer(root);
  const browser = await chromium.launch({
    executablePath: findBrowser(),
    headless: true,
    args: ["--no-first-run", "--no-default-browser-check"],
  });
  t.after(async () => {
    await browser.close();
    await server.stop();
    await fs.rm(root, { recursive: true, force: true }).catch(() => {});
  });

  const context = await browser.newContext();
  const page = await context.newPage();
  const pageErrors = [];
  const consoleErrors = [];
  page.on("pageerror", error => pageErrors.push(error.message));
  page.on("console", msg => {
    if (msg.type() === "error") consoleErrors.push(msg.text());
  });

  // 1. Automatic launch-file pairing redirects to /app/ without credentials in URL and deletes launch file.
  await page.goto(server.info.launch_file);
  await page.waitForSelector("#workspace:not([hidden])");
  assert.equal(new URL(page.url()).pathname, "/app/");
  assert.equal(new URL(page.url()).search, "");
  assert.equal(new URL(page.url()).hash, "");
  assert.equal(fsSync.existsSync(server.info.launch_path), false);

  // 2. Model/stem reconciliation and persistent defaults across reload.
  await page.selectOption("#stem", "guitar");
  assert.equal(await page.inputValue("#model"), "htdemucs_6s");
  await page.selectOption("#model", "htdemucs");
  assert.equal(await page.inputValue("#stem"), "drums");
  await page.selectOption("#format", "mp3");
  await Promise.all([
    page.waitForResponse(res => res.url().endsWith("/settings") && res.ok()),
    page.click("#save-settings"),
  ]);
  await page.reload();
  await page.waitForSelector("#workspace:not([hidden])");
  assert.equal(await page.inputValue("#format"), "mp3");

  // 3. Error -> retry flow and live progress -> cancel flow.
  await page.selectOption("#format", "wav");
  await page.fill("#inputs", "https://example.test/fail");
  await page.click("#submit");
  await page.waitForSelector("#jobs .status-badge.error");
  assert.match(await page.locator("#jobs p.error").first().textContent(), /Simulated separation failure/);
  await page.locator('#jobs button[aria-label="Retry / check files"]').first().click();
  await page.waitForSelector("#jobs .status-badge.done");

  await page.fill("#inputs", "https://example.test/slow");
  await page.click("#submit");
  await page.waitForFunction(() => document.getElementById("jobs").textContent.includes("35%"));
  await page.locator("#jobs .cancel-btn").first().click();
  await page.waitForSelector("#jobs .status-badge.muted");

  // 4. Local file upload via #files input, secondary preset export, and practice player stem switching.
  const wavPath = path.join(root, "Groove - Studio Take.wav");
  await fs.writeFile(wavPath, makeWavBuffer(0.6, 44100, 440));
  await page.setInputFiles("#files", wavPath);
  await page.click("#submit");
  await page.waitForFunction(() => document.getElementById("jobs").textContent.includes("Groove - Studio Take"));

  // Re-submit same local upload with a second preset (htdemucs_6s + guitar) to verify secondary export group.
  await page.selectOption("#stem", "guitar");
  await page.setInputFiles("#files", wavPath);
  await page.click("#submit");
  await page.waitForSelector("#jobs .stem-group .stem-btn");

  // Play Original, seek forward, switch to another stem on the same track, and verify position is preserved.
  const grooveRow = page.locator("#jobs li", { hasText: "Groove - Studio Take" });
  await grooveRow.locator('button.player-toggle[aria-label="Original"]').click();
  await page.waitForSelector("#player:not([hidden])");
  await page.waitForFunction(() => {
    const audio = document.getElementById("player-audio");
    return audio.readyState >= 1 && Number.isFinite(audio.duration) && audio.duration > 0.2;
  });
  await page.evaluate(() => {
    const audio = document.getElementById("player-audio");
    audio.pause();
    audio.currentTime = 0.25;
  });
  await grooveRow.locator('button.player-toggle[aria-label="guitar"]').click();
  await page.waitForFunction(() => {
    const audio = document.getElementById("player-audio");
    return document.getElementById("player-stem").textContent === "guitar"
      && audio.readyState >= 1
      && audio.currentTime >= 0.15;
  });

  // Play a secondary preset export button and confirm the player switches to it.
  await grooveRow.locator(".stem-group .stem-btn").first().click();
  await page.waitForFunction(() => {
    const audio = document.getElementById("player-audio");
    return document.getElementById("player-stem").textContent.includes("Balanced")
      && audio.readyState >= 1;
  });

  // Playback speed slider and 1x reset.
  await page.locator("#player-speed").fill("75");
  await page.locator("#player-speed").dispatchEvent("input");
  assert.equal(await page.evaluate(() => document.getElementById("player-audio").playbackRate), 0.75);
  assert.equal(await page.evaluate(() => document.getElementById("player-audio").preservesPitch), true);
  await page.click("#player-speed-reset");
  assert.equal(await page.evaluate(() => document.getElementById("player-audio").playbackRate), 1);

  // Close practice player.
  await page.click("#player-close");
  await page.locator("#player").waitFor({ state: "hidden" });

  // 5. Exports search, status/model filters, reset, open folder, clear list, and rescan folder.
  await page.fill("#filter-query", "Groove");
  await page.waitForFunction(() => document.querySelectorAll("#jobs > li").length === 1);
  await page.click("#filter-reset");
  await page.waitForFunction(() => document.querySelectorAll("#jobs > li").length === 3);

  await page.selectOption("#filter-status", "cancelled");
  await page.waitForFunction(() => document.querySelectorAll("#jobs > li").length === 1);
  await page.click("#filter-reset");
  await page.waitForFunction(() => document.querySelectorAll("#jobs > li").length === 3);

  await page.selectOption("#filter-model", "htdemucs_6s");
  await page.waitForFunction(() => document.querySelectorAll("#jobs > li").length === 1);
  await page.click("#filter-reset");
  await page.waitForFunction(() => document.querySelectorAll("#jobs > li").length === 3);

  await Promise.all([
    page.waitForResponse(res => res.url().endsWith("/app/open-folder") && res.ok()),
    page.click("#open-exports-folder"),
  ]);
  const openedFolders = JSON.parse(await fs.readFile(server.info.opened_log, "utf8"));
  assert.equal(openedFolders.includes(server.info.output_dir), true);

  await page.click("#clear-exports");
  await page.waitForFunction(() => document.getElementById("jobs-empty").textContent.includes("No exports yet."));
  await page.click("#rescan-exports");
  await page.waitForFunction(() => document.querySelectorAll("#jobs > li").length >= 2);

  // 6. Extension setup UI exposes token command and origin authorization, NEVER the master pairing token.
  await page.locator("details summary", { hasText: "Optional Chrome / Edge extension" }).click();
  await page.click("#prepare-extension");
  await page.waitForSelector("#extension-setup:not([hidden])");
  assert.match(await page.textContent("#extension-token-cmd"), /jamly-service token/);
  const fullHtml = await page.content();
  assert.equal(fullHtml.includes(server.info.token), false, "Master token must never appear in companion DOM");

  // Invalid origin is rejected; valid extension origin is persisted and shown.
  await page.fill("#extension-origin", "https://not-an-extension.example");
  await page.click("#allow-extension-origin");
  await page.waitForFunction(() => document.getElementById("error").textContent.includes("chrome-extension"));
  const sampleOrigin = "chrome-extension://" + "a".repeat(32);
  await page.fill("#extension-origin", sampleOrigin);
  await page.click("#allow-extension-origin");
  await page.waitForFunction(
    expected => document.getElementById("extension-origins").textContent.includes(expected),
    sampleOrigin,
  );

  // 7. Fresh context without sessionStorage shows manual pairing form and pairs cleanly.
  const manualContext = await browser.newContext();
  const manualPage = await manualContext.newPage();
  await manualPage.goto(server.info.url + "/app/");
  await manualPage.waitForSelector("#pairing:not([hidden])");
  await manualPage.locator("#pairing details summary").click();
  await manualPage.fill("#pair-token", server.info.token);
  await manualPage.click('#pair-form button[type="submit"]');
  await manualPage.waitForSelector("#workspace:not([hidden])");
  await manualContext.close();

  // 8. Quit companion shuts down backend cleanly.
  page.once("dialog", dialog => dialog.accept());
  await page.click("#quit");
  await page.waitForFunction(() => document.getElementById("connection").textContent.includes("Companion stopped"));
  await new Promise((resolve, reject) => {
    if (server.proc.exitCode !== null || server.proc.signalCode !== null) {
      resolve();
      return;
    }
    const timer = setTimeout(() => reject(new Error("Backend did not exit after Quit companion")), 10000);
    server.proc.once("exit", () => {
      clearTimeout(timer);
      resolve();
    });
  });

  assert.deepEqual(pageErrors, []);
  // Only the deliberate 400 from testing invalid extension origin may log a browser resource error;
  // assert no unexpected JS runtime or CSP console errors occurred.
  const unexpectedConsole = consoleErrors.filter(line => !line.includes("status of 400"));
  assert.deepEqual(unexpectedConsole, []);
});

test("real unpacked browser extension verifies origin authorization, /handshake, and popup submission", async t => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "jamly-ext-pw-"));
  const server = await startMockServer(root);
  const profileDir = path.join(root, "profile");
  const extensionDir = path.join(REPO_ROOT, "browser", "extension");
  let context;
  try {
    context = await chromium.launchPersistentContext(profileDir, {
      executablePath: findBrowser({ preferExtensionSupport: true }),
      headless: false,
      args: [
        "--headless=new",
        "--no-first-run",
        "--no-default-browser-check",
        `--disable-extensions-except=${extensionDir}`,
        `--load-extension=${extensionDir}`,
      ],
    });
  } catch (error) {
    await server.stop();
    await fs.rm(root, { recursive: true, force: true }).catch(() => {});
    t.skip("Browser launch for unpacked extension unsupported: " + error.message);
    return;
  }
  t.after(async () => {
    await context.close();
    await server.stop();
    await fs.rm(root, { recursive: true, force: true }).catch(() => {});
  });

  let [worker] = context.serviceWorkers();
  if (!worker) {
    try {
      worker = await context.waitForEvent("serviceworker", { timeout: 6000 });
    } catch (_timeout) {
      t.skip("Installed browser build disabled --load-extension in headless mode.");
      return;
    }
  }
  // WHATWG URL returns origin "null" for non-special schemes like chrome-extension://, so build the origin from protocol + host.
  const workerUrl = new URL(worker.url());
  const extensionOrigin = workerUrl.protocol + "//" + workerUrl.hostname;
  assert.match(extensionOrigin, /^chrome-extension:\/\/[a-p]{32}$/);

  const pageErrors = [];
  const optionsPage = await context.newPage();
  optionsPage.on("pageerror", error => pageErrors.push(error.message));
  await optionsPage.goto(extensionOrigin + "/jamly_options.html");
  assert.equal(await optionsPage.textContent("#ext-origin"), extensionOrigin);

  // Before authorizing the extension origin, pairing must be rejected with 403 via POST /handshake.
  await optionsPage.fill("#url", server.info.url);
  await optionsPage.fill("#token", server.info.token);
  await optionsPage.click("#save");
  await optionsPage.waitForFunction(() => document.getElementById("status").textContent.length > 0);
  assert.match(await optionsPage.textContent("#status"), /not allowed|403/i);

  // Authorize exact extension origin via backend API, then save & pair.
  const allowRes = await fetch(server.info.url + "/app/extension", {
    method: "POST",
    headers: {
      Authorization: "Bearer " + server.info.token,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ allow_origin: extensionOrigin }),
  });
  assert.equal(allowRes.status, 200);

  await optionsPage.click("#save");
  await optionsPage.waitForFunction(() => document.getElementById("status").textContent.includes("Paired."));

  // Open popup, verify live connected status, enqueue a track via service worker + /handshake, and confirm exports.
  const popupPage = await context.newPage();
  popupPage.on("pageerror", error => pageErrors.push(error.message));
  await popupPage.goto(extensionOrigin + "/jamly_popup.html");
  await popupPage.waitForFunction(() => document.getElementById("connection").textContent.includes("Connected"));
  await popupPage.fill("#inputs", "https://example.test/extension-track");
  await popupPage.click("#submit");
  await popupPage.waitForFunction(() => {
    const jobsText = document.getElementById("jobs").textContent;
    return jobsText.includes("Extension Track") && jobsText.includes("Download drums") && jobsText.includes("Download drumless");
  });
  assert.deepEqual(pageErrors, []);
});
