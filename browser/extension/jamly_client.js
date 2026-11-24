(function (global) {
  "use strict";

  function baseURL(value) {
    const url = new URL(value || "http://127.0.0.1:8765");
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password || url.search || url.hash) {
      throw new Error("Use an http(s) backend base URL without credentials, query, or fragment.");
    }
    return url.href.replace(/\/+$/, "");
  }

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

  async function clientProof(token, nonce, port) {
    const cryptoApi = global.crypto;
    if (!cryptoApi?.subtle || !cryptoApi?.getRandomValues) {
      throw new Error("Web Crypto is required to verify the Jamly backend.");
    }
    const encoder = new TextEncoder();
    const key = await cryptoApi.subtle.importKey(
      "raw", encoder.encode(token), { name: "HMAC", hash: "SHA-256" }, false, ["sign"],
    );
    const context = JSON.stringify(["jamly-client-v1", nonce, port]);
    const signature = await cryptoApi.subtle.sign("HMAC", key, encoder.encode(context));
    return hexBytes(new Uint8Array(signature));
  }

  async function verifyBackend(config) {
    if (!config.token) return;
    const base = baseURL(config.url);
    const parsed = new URL(base);
    const port = Number(parsed.port) || (parsed.protocol === "https:" ? 443 : 80);
    const cryptoApi = global.crypto;
    if (!cryptoApi?.subtle || !cryptoApi?.getRandomValues) {
      throw new Error("Web Crypto is required to verify the Jamly backend.");
    }
    const nonceBytes = new Uint8Array(32);
    cryptoApi.getRandomValues(nonceBytes);
    const nonce = hexBytes(nonceBytes);
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 10000);
    let payload;
    try {
      const response = await global.fetch(base + "/handshake?nonce=" + nonce, {
        method: "GET",
        headers: { Accept: "application/json" },
        signal: controller.signal,
        cache: "no-store",
      });
      try {
        payload = await response.json();
      } catch (_error) {
        throw new Error("The backend identity could not be verified; no pairing token was sent.");
      }
      if (!response.ok) {
        throw new Error(payload.error || "The backend identity could not be verified; no pairing token was sent.");
      }
    } finally {
      clearTimeout(timer);
    }
    if (payload.api_version !== 3) {
      throw new Error("Backend and browser versions differ; update both Jamly components.");
    }
    const expected = await clientProof(config.token, nonce, port);
    if (!constantTimeEqual(payload.client_proof, expected)) {
      throw new Error("The backend identity does not match this pairing token; no credential was sent.");
    }
  }

  async function request(config, path, method = "GET", body, options = {}) {
    const attachAuth = options.auth !== false && Boolean(config.token);
    if (attachAuth && config.verifyBackend !== false) {
      await verifyBackend(config);
    }
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 10000);
    try {
      const headers = { Accept: "application/json" };
      if (attachAuth) headers.Authorization = "Bearer " + config.token;
      if (body !== undefined) headers["Content-Type"] = "application/json";
      const response = await global.fetch(baseURL(config.url) + path, {
        method, headers, body: body === undefined ? undefined : JSON.stringify(body),
        signal: controller.signal, cache: "no-store",
      });
      let payload;
      try {
        payload = await response.json();
      } catch (_error) {
        throw new Error("The backend returned an invalid response.");
      }
      if (!response.ok) {
        throw new Error(payload.error || "Backend HTTP " + response.status);
      }
      return payload;
    } finally {
      clearTimeout(timer);
    }
  }

  async function status(config, query = {}) {
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(query)) {
      if (value !== undefined && value !== null && value !== "" && value !== "all") {
        params.set(key, String(value));
      }
    }
    const suffix = params.toString() ? "?" + params.toString() : "";
    const result = await request(config, "/status" + suffix);
    if (result.api_version !== 3 || !Array.isArray(result.jobs)) {
      throw new Error("Backend and browser versions differ; update both Jamly components.");
    }
    return result;
  }

  function outputURL(config, relative) {
    const base = baseURL(config.url);
    if (typeof relative !== "string" || !relative.startsWith("/outputs/")) {
      throw new Error("The backend returned an invalid output URL.");
    }
    const url = new URL(base + relative);
    if (url.origin !== new URL(base).origin ||
        !url.pathname.startsWith(new URL(base).pathname.replace(/\/$/, "") + "/outputs/")) {
      throw new Error("The backend returned an invalid output URL.");
    }
    return url.href;
  }

  function sourceURL(config, watchId) {
    const base = baseURL(config.url);
    if (typeof watchId !== "string" || !watchId) {
      throw new Error("The backend returned an invalid source id.");
    }
    const url = new URL(base + "/source/" + encodeURIComponent(watchId));
    if (url.origin !== new URL(base).origin) {
      throw new Error("The backend returned an invalid source URL.");
    }
    return url.href;
  }

  function reconcileModelStem(stems, modelId, stemId, changed) {
    let model = modelId;
    let stem = stemId || "drums";
    const stemChoice = (stems || []).find(item => item.id === stem);
    if (stemChoice?.models && !stemChoice.models.includes(model)) {
      if (changed === "stem") {
        model = stemChoice.models[0];
      } else {
        stem = "drums";
      }
    }
    return { model, stem };
  }

  function describeModelStem(models, stems, modelId, stemId) {
    const stemValue = stemId || "drums";
    const modelDescription = (models || []).find(item => item.id === modelId)?.description || "";
    const stemChoice = (stems || []).find(item => item.id === stemValue);
    const stemDescription = stemChoice?.description || "";
    return {
      modelDescription,
      stemDescription,
      helpText: modelDescription + (stemDescription ? " " + stemDescription : ""),
      submitLabel: stemValue === "all" ? "Split all stems" : "Split " + stemValue,
    };
  }

  const client = {
    baseURL, outputURL, sourceURL, status, request, verifyBackend,
    reconcileModelStem, describeModelStem,
    health: config => request(config, "/healthz", "GET", undefined, { auth: false }),
    addInputs: (config, inputs, replace = false, options = {}) => request(config, "/add", "POST", {
      inputs, replace_existing: replace, model: options.model, audio_format: options.audio_format,
      stem: options.stem,
    }),
    retry: (config, id, replace = false) => request(
      config, "/retry/" + encodeURIComponent(id), "POST", { replace_existing: replace }
    ),
    cancel: (config, id) => request(
      config, "/cancel/" + encodeURIComponent(id), "POST", {}
    ),
  };
  global.jamlyClient = client;
  if (typeof module !== "undefined" && module.exports) module.exports = client;
})(globalThis);
