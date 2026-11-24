"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const { test } = require("node:test");
const tick = () => new Promise(resolve => setImmediate(resolve));

function optionsPage({ stored = {}, granted = true, status } = {}) {
  function element() {
    return {
      textContent: "", value: "", events: {},
      addEventListener(name, callback) { this.events[name] = callback; },
    };
  }
  const elements = Object.fromEntries(["ext-origin", "url", "token", "save", "status"].map(id => [id, element()]));
  const calls = { permissions: [], saved: [], status: [] };
  const context = {
    location: { origin: "chrome-extension://" + "b".repeat(32) },
    document: { getElementById: id => elements[id] },
    URL,
    jamlyClient: {
      baseURL: url => (url || "http://127.0.0.1:8765").replace(/\/+$/, ""),
      async status(config) {
        calls.status.push(config);
        if (status) return status(config);
        return { pending: 0 };
      },
    },
    chrome: {
      storage: { local: {
        async get() { return stored; },
        async set(values) { calls.saved.push(values); },
      } },
      permissions: { async request(request) { calls.permissions.push(request); return granted; } },
    },
  };
  vm.runInNewContext(fs.readFileSync(__dirname + "/jamly_options.js", "utf8"), context);
  return { elements, calls };
}

test("options page shows the extension origin and loads stored pairing", async () => {
  const { elements } = optionsPage({ stored: { jamly_serverUrl: "http://127.0.0.1:9000", jamly_serverToken: "saved" } });
  await tick();
  assert.equal(elements["ext-origin"].textContent, "chrome-extension://" + "b".repeat(32));
  assert.equal(elements.url.value, "http://127.0.0.1:9000");
  assert.equal(elements.token.value, "saved");
});

test("options page defaults the backend URL when nothing is stored", async () => {
  const { elements } = optionsPage();
  await tick();
  assert.equal(elements.url.value, "http://127.0.0.1:8765");
  assert.equal(elements.token.value, "");
});

test("an empty token is rejected before requesting permissions or saving", async () => {
  const { elements, calls } = optionsPage();
  await tick();
  elements.token.value = "   ";
  await elements.save.events.click();
  assert.match(elements.status.textContent, /jamly service token/);
  assert.equal(calls.permissions.length, 0);
  assert.equal(calls.saved.length, 0);
});

test("a denied host permission is reported and nothing is saved", async () => {
  const { elements, calls } = optionsPage({ granted: false });
  await tick();
  elements.token.value = "token-value";
  await elements.save.events.click();
  assert.match(elements.status.textContent, /Host permission is required/);
  assert.equal(JSON.stringify(calls.permissions[0].origins), JSON.stringify(["http://127.0.0.1/*"]));
  assert.equal(calls.saved.length, 0);
});

test("saving requests the backend host, stores the pairing, and verifies it", async () => {
  const { elements, calls } = optionsPage();
  await tick();
  elements.url.value = " http://localhost:8800/ ";
  elements.token.value = " token-value ";
  await elements.save.events.click();
  assert.equal(JSON.stringify(calls.permissions[0].origins), JSON.stringify(["http://localhost/*"]));
  assert.equal(calls.saved[0].jamly_serverUrl, "http://localhost:8800");
  assert.equal(calls.saved[0].jamly_serverToken, "token-value");
  assert.equal(calls.status[0].token, "token-value");
  assert.match(elements.status.textContent, /^Paired\./);
});

test("verification failures surface the backend error", async () => {
  const { elements } = optionsPage({ status: () => { throw new Error("origin not allowed (403)"); } });
  await tick();
  elements.token.value = "token-value";
  await elements.save.events.click();
  assert.equal(elements.status.textContent, "origin not allowed (403)");
});

test("async storage load does not overwrite user edits already typed into the form", async () => {
  const { elements } = optionsPage({ stored: {} });
  elements.url.value = "http://127.0.0.1:9123";
  elements.token.value = "typed-before-storage-resolved";
  await tick();
  assert.equal(elements.url.value, "http://127.0.0.1:9123");
  assert.equal(elements.token.value, "typed-before-storage-resolved");
});
