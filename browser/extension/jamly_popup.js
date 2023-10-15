(function () {
  "use strict";
  const byId = id => document.getElementById(id);
  let timer;
  let refreshing = false;
  let closed = false;
  let connectionError = "";
  let models = [];
  let stems = [];

  function downloadLabel(kind) {
    const labels = {
      no_drums: "Download drumless",
      no_vocals: "Download karaoke",
      no_bass: "Download bassless",
      no_guitar: "Download guitarless",
      no_piano: "Download pianoless",
      no_other: "Download no-other",
    };
    return labels[kind] || ("Download " + kind);
  }

  function modelHelp() {
    const modelDesc = models.find(model => model.id === byId("model").value)?.description || "";
    const stemValue = byId("stem")?.value || "drums";
    const stemChoice = stems.find(stem => stem.id === stemValue);
    const stemDesc = stemChoice?.description ? " " + stemChoice.description : "";
    byId("model-help").textContent = modelDesc + stemDesc;
    byId("model").title = modelDesc;
    if (byId("stem") && stemChoice?.description) {
      byId("stem").title = stemChoice.description;
    }
    if (byId("submit")) {
      byId("submit").textContent = stemValue === "all" ? "Split all stems" : "Split " + stemValue;
    }
  }

  async function send(message) {
    const response = await chrome.runtime.sendMessage(message);
    if (!response || !response.ok) throw new Error(response?.error || "No response from Jamly.");
    return response.result;
  }

  function showError(error) {
    byId("error").textContent = error.message || String(error);
  }

  function button(label, action) {
    const element = document.createElement("button");
    element.textContent = label;
    element.addEventListener("click", async () => {
      element.disabled = true;
      try {
        await action();
        byId("error").textContent = "";
      } catch (error) {
        showError(error);
      } finally {
        element.disabled = false;
      }
    });
    return element;
  }

  function render(status) {
    if (!models.length) {
      models = status.models;
      for (const model of models) {
        const option = document.createElement("option");
        option.value = model.id;
        option.textContent = model.label;
        byId("model").appendChild(option);
      }
      byId("model").value = status.defaults.model;
      if (byId("stem") && Array.isArray(status.stems)) {
        stems = status.stems;
        for (const stem of stems) {
          const option = document.createElement("option");
          option.value = stem.id;
          option.textContent = stem.label;
          byId("stem").appendChild(option);
        }
        byId("stem").value = status.defaults.stem || "drums";
      }
      modelHelp();
    }
    byId("counts").textContent =
      `Queued: ${status.pending} | Complete: ${status.done} | Failed: ${status.failed}`;
    byId("jobs").replaceChildren();
    for (const job of status.jobs) {
      const row = document.createElement("li");
      const state = document.createElement("div");
      state.className = "job-status";
      const isCurrent = status.current?.watch_id === job.watch_id;
      const stage = isCurrent ? status.current.stage : job.status;
      const pct = isCurrent && typeof status.current?.progress === "number"
        ? ` (${status.current.progress}%)` : "";
      state.textContent = `${String(stage).toUpperCase()}${pct}: ${job.title || job.watch_id}`;
      row.appendChild(state);
      const preset = document.createElement("div");
      preset.textContent = models.find(model => model.id === job.model)?.label || job.model;
      row.appendChild(preset);
      if (job.error) {
        const error = document.createElement("div");
        error.className = "error";
        error.textContent = job.error;
        row.appendChild(error);
      }
      for (const output of job.outputs || []) {
        if (output.kind === "metadata") continue;
        row.appendChild(button(
          downloadLabel(output.kind),
          () => send({ type: "download", output }),
        ));
      }
      if (!["pending", "running"].includes(job.status)) {
        row.appendChild(button("Retry / check files", async () => {
          await send({ type: "retry", id: job.watch_id, replace: byId("replace").checked });
          await refresh();
        }));
      }
      byId("jobs").appendChild(row);
    }
    if (status.error) showError(new Error(status.error));
  }

  async function refresh() {
    if (refreshing || closed) return;
    clearTimeout(timer);
    refreshing = true;
    try {
      const status = await send({ type: "status" });
      byId("connection").textContent = "Connected to the paired backend";
      if (byId("error").textContent === connectionError) byId("error").textContent = "";
      connectionError = "";
      render(status);
    } catch (error) {
      byId("connection").textContent = "Offline or not paired; displayed jobs may be stale.";
      connectionError = error.message || String(error);
      showError(error);
    } finally {
      refreshing = false;
      if (!closed) timer = setTimeout(refresh, 2000);
    }
  }

  byId("options").addEventListener("click", () => chrome.runtime.openOptionsPage());
  byId("model").addEventListener("change", () => {
    const stemSelect = byId("stem");
    if (stemSelect) {
      const stemChoice = stems.find(item => item.id === stemSelect.value);
      if (stemChoice?.models && !stemChoice.models.includes(byId("model").value)) {
        stemSelect.value = "drums";
      }
    }
    modelHelp();
  });
  byId("stem")?.addEventListener("change", () => {
    const stemChoice = stems.find(item => item.id === byId("stem").value);
    if (stemChoice?.models && !stemChoice.models.includes(byId("model").value)) {
      byId("model").value = stemChoice.models[0];
    }
    modelHelp();
  });
  byId("current").addEventListener("click", async () => {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab?.url || !/^https?:/.test(tab.url)) {
      showError(new Error("Open a supported media page first."));
      return;
    }
    byId("inputs").value = tab.url;
  });
  byId("submit").addEventListener("click", async () => {
    const inputs = byId("inputs").value.split(/\r?\n/).map(value => value.trim()).filter(Boolean);
    if (!inputs.length) {
      showError(new Error("Enter at least one individual media URL."));
      return;
    }
    byId("submit").disabled = true;
    try {
      const payload = {
        type: "enqueue", inputs, replace: byId("replace").checked, model: byId("model").value,
      };
      if (byId("stem")?.value) payload.stem = byId("stem").value;
      const result = await send(payload);
      byId("error").textContent = "";
      byId("connection").textContent = `Queued ${result.newly_added} new job(s).`;
      await refresh();
    } catch (error) {
      showError(error);
    } finally {
      byId("submit").disabled = false;
    }
  });
  window.addEventListener("unload", () => { closed = true; clearTimeout(timer); });
  refresh();
})();
