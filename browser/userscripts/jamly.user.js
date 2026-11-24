// ==UserScript==
// @name         Jamly status helper
// @namespace    https://github.com/kurtisnotcurtis/jamly
// @version      0.3.1
// @description  Optional status/enqueue helper for a paired local Jamly backend.
// @match        https://www.youtube.com/*
// @match        https://music.youtube.com/*
// @grant        GM_xmlhttpRequest
// @grant        GM_getValue
// @grant        GM_setValue
// @grant        GM_registerMenuCommand
// @connect      127.0.0.1
// @connect      localhost
// @run-at       document-end
// ==/UserScript==

(function () {
  "use strict";
  let closed = false;
  let timer;
  const panel = document.createElement("aside");
  panel.style.cssText = "position:fixed;right:12px;bottom:12px;z-index:2147483647;background:#202124;color:white;padding:12px;font:14px system-ui;max-width:320px";
  const status = document.createElement("div");
  const split = document.createElement("button");
  const close = document.createElement("button");
  split.textContent = "Split this page";
  close.textContent = "Close";
  panel.append(status, split, close);
  document.body.appendChild(panel);

  GM_registerMenuCommand("Pair Jamly", () => {
    const token = window.prompt("Paste the token printed by jamly-service token:");
    if (token) GM_setValue("token", token.trim());
  });

  const BACKEND_URL = "http://127.0.0.1:8765";
  const BACKEND_PORT = 8765;

  function hexBytes(bytes) {
    return Array.from(bytes, byte => byte.toString(16).padStart(2, "0")).join("");
  }

  function constantTimeEqual(left, right) {
    if (typeof left !== "string" || typeof right !== "string" || left.length !== right.length) {
      return false;
    }
    let diff = 0;
    for (let i = 0; i < left.length; i++) {
      diff |= left.charCodeAt(i) ^ right.charCodeAt(i);
    }
    return diff === 0;
  }

  function gmJson(path, { method = "GET", headers = {}, body } = {}) {
    return new Promise((resolve, reject) => {
      GM_xmlhttpRequest({
        url: BACKEND_URL + path,
        method,
        headers,
        data: body === undefined ? undefined : JSON.stringify(body),
        anonymous: true,
        timeout: 10000,
        onload: response => {
          try {
            const value = JSON.parse(response.responseText);
            if (response.status < 200 || response.status >= 300) {
              throw new Error(value.error || "Backend HTTP " + response.status);
            }
            resolve(value);
          } catch (error) { reject(error); }
        },
        onerror: () => reject(new Error("Cannot reach Jamly. Start jamly-server.")),
        ontimeout: () => reject(new Error("Jamly request timed out.")),
      });
    });
  }

  async function verifyBackend(token) {
    if (!crypto?.subtle || !crypto?.getRandomValues) {
      throw new Error("Web Crypto is required to verify the Jamly backend.");
    }
    const nonceBytes = new Uint8Array(32);
    crypto.getRandomValues(nonceBytes);
    const nonce = hexBytes(nonceBytes);
    const handshake = await gmJson("/handshake?nonce=" + nonce, {
      headers: { Accept: "application/json" },
    });
    if (handshake.api_version !== 3) {
      throw new Error("Update the backend and userscript together.");
    }
    const encoder = new TextEncoder();
    const key = await crypto.subtle.importKey(
      "raw", encoder.encode(token), { name: "HMAC", hash: "SHA-256" }, false, ["sign"],
    );
    const context = JSON.stringify(["jamly-client-v1", nonce, BACKEND_PORT]);
    const signature = await crypto.subtle.sign("HMAC", key, encoder.encode(context));
    const expected = hexBytes(new Uint8Array(signature));
    if (!constantTimeEqual(handshake.client_proof, expected)) {
      throw new Error("Backend identity mismatch; no pairing token was sent.");
    }
  }

  async function request(path, body) {
    const token = String(GM_getValue("token", "") || "").trim();
    if (!token) {
      throw new Error("Use the Pair Jamly userscript menu to set your pairing token.");
    }
    await verifyBackend(token);
    const headers = { Accept: "application/json", Authorization: "Bearer " + token };
    if (body !== undefined) headers["Content-Type"] = "application/json";
    return gmJson(path, { method: body === undefined ? "GET" : "POST", headers, body });
  }

  async function poll() {
    if (closed) return;
    try {
      const state = await request("/status");
      if (state.api_version !== 3) throw new Error("Update the backend and userscript together.");
      status.textContent = state.current
        ? "Jamly: " + state.current.stage
        : `Queued ${state.pending}; complete ${state.done}; failed ${state.failed}.`;
      status.textContent += " Use the Jamly extension to download or retry results.";
    } catch (error) {
      status.textContent = error.message || String(error);
    } finally {
      if (!closed) timer = setTimeout(poll, 3000);
    }
  }
  split.addEventListener("click", async () => {
    try {
      split.disabled = true;
      const response = await request("/add", { inputs: [location.href] });
      status.textContent = `Queued ${response.newly_added} job(s).`;
    } catch (error) {
      status.textContent = error.message || String(error);
    } finally { split.disabled = false; }
  });
  close.addEventListener("click", () => {
    closed = true;
    clearTimeout(timer);
    panel.remove();
  });
  poll();
})();
