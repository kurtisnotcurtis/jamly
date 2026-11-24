"use strict";
const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const fs = require("node:fs");
const http = require("node:http");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const client = require("./jamly_client.js");

function makeClientProof(token, nonce, port) {
  const context = JSON.stringify(["jamly-client-v1", nonce, port]);
  return crypto.createHmac("sha256", token).update(context).digest("hex");
}

async function backend(t, { token = "paired-token-0123456789abcdef012345", failHandshake = false } = {}) {
  const received = [];
  const authHeaders = [];
  const server = http.createServer((request, response) => {
    const chunks = [];
    request.on("data", chunk => chunks.push(chunk));
    request.on("end", () => {
      response.setHeader("Content-Type", "application/json");
      if (request.headers.authorization) {
        authHeaders.push({ url: request.url, auth: request.headers.authorization });
      }
      if (request.url.startsWith("/handshake?")) {
        const parsed = new URL("http://127.0.0.1" + request.url);
        const nonce = parsed.searchParams.get("nonce") || "";
        if (failHandshake) {
          response.end(JSON.stringify({ api_version: 3, client_proof: "0".repeat(64) }));
          return;
        }
        response.end(JSON.stringify({
          api_version: 3,
          client_proof: makeClientProof(token, nonce, server.address().port),
        }));
        return;
      }
      if (request.headers.authorization !== "Bearer " + token) {
        response.writeHead(401);
        response.end(JSON.stringify({ error: "Pair the client first" }));
        return;
      }
      if (request.url.startsWith("/status")) {
        received.push({ url: request.url });
        response.end(JSON.stringify({ api_version: 3, jobs: [], pending: 0, done: 0 }));
      } else {
        received.push({ url: request.url, body: JSON.parse(Buffer.concat(chunks).toString()) });
        response.writeHead(202);
        response.end(JSON.stringify({ newly_added: 1 }));
      }
    });
  });
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  t.after(() => new Promise(resolve => server.close(resolve)));
  return {
    config: { url: "http://127.0.0.1:" + server.address().port, token },
    received,
    authHeaders,
  };
}

test("paired JSON status, submission and retry preserve input and options", async t => {
  const { config, received } = await backend(t);
  assert.equal((await client.status(config)).api_version, 3);
  await client.status(config, { q: "jazz", status: "done", model: "all", page: 2, page_size: 25 });
  assert.equal((await client.addInputs(config, ["https://example.test/a"], true, {
    model: "htdemucs_ft", audio_format: "wav",
  })).newly_added, 1);
  await client.retry(config, "src-id", false);
  await client.cancel(config, "src-id");
  assert.deepEqual(received, [
    { url: "/status" },
    { url: "/status?q=jazz&status=done&page=2&page_size=25" },
    { url: "/add", body: {
      inputs: ["https://example.test/a"], replace_existing: true, model: "htdemucs_ft", audio_format: "wav",
    } },
    { url: "/retry/src-id", body: { replace_existing: false } },
    { url: "/cancel/src-id", body: {} },
  ]);
});

test("HTTP errors reject rather than becoming successful queue responses", async t => {
  const { config, authHeaders } = await backend(t);
  await assert.rejects(client.status({ ...config, token: "" }), /Pair the client first/);
  await assert.rejects(
    client.addInputs({ ...config, token: "wrong-token-0123456789abcdef012345" }, ["url"]),
    /identity/,
  );
  // Handshake mismatch stopped the request before attaching Authorization.
  assert.equal(authHeaders.length, 0);
});

test("stand-in listener that fails handshake never receives the pairing token", async t => {
  const { config, authHeaders } = await backend(t, { failHandshake: true });
  await assert.rejects(client.status(config), /identity/);
  await assert.rejects(client.addInputs(config, ["https://example.test/a"]), /identity/);
  await assert.rejects(client.retry(config, "id"), /identity/);
  await assert.rejects(client.cancel(config, "id"), /identity/);
  assert.deepEqual(authHeaders, []);
});

test("userscript verifies backend handshake before attaching its pairing token", async () => {
  const seen = [];
  const source = fs.readFileSync(
    path.join(__dirname, "..", "userscripts", "jamly.user.js"),
    "utf8",
  );
  const elements = [];
  function fakeElement() {
    return {
      style: {}, textContent: "", disabled: false,
      append(...children) { elements.push(...children); },
      appendChild(child) { elements.push(child); },
      addEventListener() {},
      remove() {},
    };
  }
  let doneResolve;
  const done = new Promise(resolve => { doneResolve = resolve; });
  const context = {
    crypto: globalThis.crypto,
    TextEncoder: globalThis.TextEncoder,
    Uint8Array: globalThis.Uint8Array,
    location: { href: "https://www.youtube.com/watch?v=dQw4w9WgXcQ" },
    window: { prompt() { return ""; } },
    document: { createElement: fakeElement, body: fakeElement() },
    setTimeout() { doneResolve(); return 1; },
    clearTimeout() {},
    GM_registerMenuCommand() {},
    GM_getValue(key, fallback) { return key === "token" ? "userscript-secret-token-0123456789ab" : fallback; },
    GM_setValue() {},
    GM_xmlhttpRequest(opts) {
      seen.push({ url: opts.url, headers: opts.headers || {} });
      // Stand-in listener returns bogus handshake proof.
      opts.onload({
        status: 200,
        responseText: JSON.stringify({ api_version: 3, client_proof: "f".repeat(64) }),
      });
    },
  };
  vm.runInNewContext(source, context);
  await done;
  assert.equal(seen.length, 1);
  assert.match(seen[0].url, /\/handshake\?nonce=[a-f0-9]{64}$/);
  assert.equal(seen[0].headers.Authorization, undefined);
});

test("artifact downloads stay on the paired backend", () => {
  const config = { url: "https://example.test/jamly" };
  assert.equal(client.outputURL(config, "/outputs/id/drums?generation=one"),
               "https://example.test/jamly/outputs/id/drums?generation=one");
  assert.throws(() => client.outputURL(config, "https://other.example/file"), /invalid/);
  assert.throws(() => client.outputURL(config, "/outputs/../../private"), /invalid/);
  assert.throws(() => client.baseURL("http://127.0.0.1/?token=secret"), /base URL/);
});

test("source URLs stay on the paired backend", () => {
  const config = { url: "https://example.test/jamly" };
  assert.equal(client.sourceURL(config, "abc-123"),
               "https://example.test/jamly/source/abc-123");
  assert.throws(() => client.sourceURL(config, ""), /invalid source id/);
  assert.throws(() => client.sourceURL(config, undefined), /invalid source id/);
});

test("model and stem helpers keep selection and descriptions in sync", () => {
  const models = [
    { id: "htdemucs", description: "Fast 4-stem." },
    { id: "htdemucs_6s", description: "6-stem." },
  ];
  const stems = [
    { id: "drums", description: "Drums pair.", models: ["htdemucs", "htdemucs_6s"] },
    { id: "guitar", description: "Guitar pair.", models: ["htdemucs_6s"] },
    { id: "all", description: "All tracks.", models: ["htdemucs", "htdemucs_6s"] },
  ];
  assert.deepEqual(client.reconcileModelStem(stems, "htdemucs", "guitar", "stem"), {
    model: "htdemucs_6s", stem: "guitar",
  });
  assert.deepEqual(client.reconcileModelStem(stems, "htdemucs", "guitar", "model"), {
    model: "htdemucs", stem: "drums",
  });
  const desc = client.describeModelStem(models, stems, "htdemucs", "all");
  assert.equal(desc.helpText, "Fast 4-stem. All tracks.");
  assert.equal(desc.submitLabel, "Split all stems");
});
