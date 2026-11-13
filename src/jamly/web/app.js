(function () {
  "use strict";
  const byId = id => document.getElementById(id);
  const config = { url: location.origin, token: sessionStorage.getItem("jamly-token") || "" };
  let models = [];
  let stems = [];
  let initialized = false;
  let stopped = false;
  let refreshing = false;
  let timer;
  let connectionError = "";
  const stages = {
    pending: "Queued", metadata: "Checking media", download: "Downloading audio",
    separate: "Loading model / separating audio (first use downloads weights)",
    mix: "Preparing exports", publish: "Saving files", done: "Complete",
    skipped: "Reused existing files", error: "Failed", cancelled: "Cancelled", running: "Working",
  };

  function showError(error) {
    byId("error").textContent = error.message || String(error);
  }

  async function action(button, task) {
    button.disabled = true;
    byId("error").textContent = "";
    try {
      await task();
    } catch (error) {
      showError(error);
    } finally {
      button.disabled = false;
    }
  }

  const post = (path, body = {}) => jamlyClient.request(config, path, "POST", body);

  async function uploadFile(file) {
    const response = await fetch(
      `${location.origin}/app/upload?name=${encodeURIComponent(file.name)}`,
      {
        method: "POST",
        headers: {
          Accept: "application/json",
          Authorization: "Bearer " + config.token,
          "Content-Type": file.type && file.type.startsWith("audio/") ? file.type : "application/octet-stream",
        },
        body: file,
        cache: "no-store",
      },
    );
    let payload;
    try {
      payload = await response.json();
    } catch (_error) {
      throw new Error("The backend returned an invalid upload response.");
    }
    if (!response.ok) {
      throw new Error(payload.error || "Upload failed with HTTP " + response.status);
    }
    return payload;
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

  function outputLabel(kind) {
    const labels = {
      no_drums: "drumless",
      no_vocals: "karaoke (no vocals)",
      no_bass: "bassless",
      no_guitar: "guitarless",
      no_piano: "pianoless",
      no_other: "no other",
    };
    return labels[kind] || kind;
  }

  function settings() {
    const result = {
      model: byId("model").value, audio_format: byId("format").value,
      output_dir: byId("output-dir").value.trim(),
    };
    if (byId("stem")?.value) {
      result.stem = byId("stem").value;
    }
    return result;
  }

  function button(label, task) {
    const element = document.createElement("button");
    element.type = "button";
    element.textContent = label;
    element.addEventListener("click", () => action(element, task));
    return element;
  }

  function download(output) {
    const url = new URL(jamlyClient.outputURL(config, output.url));
    const form = document.createElement("form");
    form.method = "post";
    form.target = "_blank";
    form.rel = "noopener";
    form.action = "/app/download/" + url.pathname.slice("/outputs/".length) + url.search;
    const input = document.createElement("input");
    input.type = "hidden";
    input.name = "token";
    input.value = config.token;
    form.appendChild(input);
    document.body.appendChild(form);
    form.submit();
    form.remove();
  }

  function render(status) {
    models = status.models;
    stems = status.stems || [
      { id: "drums", label: "Drums (drums + drumless)", models: ["htdemucs", "htdemucs_ft", "htdemucs_6s"] },
    ];
    if (!initialized) {
      byId("model").replaceChildren();
      for (const model of models) {
        const option = document.createElement("option");
        option.value = model.id;
        option.textContent = model.label;
        byId("model").appendChild(option);
      }
      if (byId("stem")) {
        byId("stem").replaceChildren();
        for (const stem of stems) {
          const option = document.createElement("option");
          option.value = stem.id;
          option.textContent = stem.label;
          byId("stem").appendChild(option);
        }
        byId("stem").value = status.defaults.stem || "drums";
      }
      byId("model").value = status.defaults.model;
      byId("format").value = status.defaults.audio_format;
      byId("output-dir").value = status.defaults.output_dir;
      modelHelp();
      initialized = true;
    }
    byId("workspace").hidden = false;
    byId("pairing").hidden = true;
    byId("counts").textContent =
      `Queued: ${status.pending} | Complete: ${status.done} | Failed: ${status.failed}`;
    byId("jobs").replaceChildren();
    for (const job of status.jobs) {
      const row = document.createElement("li");
      const title = document.createElement("p");
      title.className = "job-title";
      title.textContent = job.title || job.watch_id;
      row.appendChild(title);
      const detail = document.createElement("p");
      const isCurrent = status.current?.watch_id === job.watch_id;
      const stage = isCurrent ? status.current.stage : job.status;
      const progressText = isCurrent && typeof status.current?.progress === "number"
        ? ` (${status.current.progress}%)` : "";
      const model = models.find(choice => choice.id === job.model)?.label || job.model;
      const stemTag = job.stem && job.stem !== "drums" ? ` | ${job.stem}` : "";
      detail.textContent = `${stages[stage] || stage}${progressText} | ${model}${stemTag} | ${job.audio_format.toUpperCase()}`;
      row.appendChild(detail);
      if (job.error) {
        const error = document.createElement("p");
        error.className = "error";
        error.textContent = job.error;
        row.appendChild(error);
      }
      if (job.outputs.length || job.exports?.length) {
        row.appendChild(button("Show files", () => post("/app/open-folder", { id: job.watch_id })));
        for (const output of job.outputs) {
          if (output.kind === "metadata") continue;
          row.appendChild(button(
            "Save a copy: " + outputLabel(output.kind),
            () => download(output),
          ));
        }
        for (const exp of job.exports || []) {
          const expModel = models.find(choice => choice.id === exp.model)?.label || exp.model;
          const expStem = exp.stem && exp.stem !== "drums" ? ` ${exp.stem}` : "";
          const expFormat = String(exp.audio_format || "").toUpperCase();
          for (const output of exp.outputs || []) {
            if (output.kind === "metadata") continue;
            row.appendChild(button(
              `Save a copy: ${outputLabel(output.kind)} (${expModel}${expStem} ${expFormat})`,
              () => download(output),
            ));
          }
        }
      }
      if (!["pending", "running"].includes(job.status)) {
        row.appendChild(button("Retry / check files", async () => {
          await jamlyClient.retry(config, job.watch_id, byId("replace").checked);
          await refresh();
        }));
      }
      byId("jobs").appendChild(row);
    }
    if (status.error) showError(new Error(status.error));
  }

  async function refresh() {
    if (stopped || refreshing || document.hidden) return;
    clearTimeout(timer);
    if (!config.token) {
      byId("connection").textContent = "Open the Jamly application to connect this page.";
      byId("pairing").hidden = false;
      return;
    }
    refreshing = true;
    try {
      const status = await jamlyClient.status(config);
      byId("connection").textContent = "Connected to your local companion. Jobs continue when this page closes.";
      if (byId("error").textContent === connectionError) byId("error").textContent = "";
      connectionError = "";
      render(status);
    } catch (error) {
      byId("connection").textContent = "Offline or session expired. Reopen Jamly; displayed jobs may be stale.";
      byId("pairing").hidden = false;
      connectionError = error.message || String(error);
      showError(error);
    } finally {
      refreshing = false;
      if (!stopped) timer = setTimeout(refresh, 2000);
    }
  }

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
  byId("pair-form").addEventListener("submit", async event => {
    event.preventDefault();
    await action(event.submitter, async () => {
      config.token = byId("pair-token").value.trim();
      await jamlyClient.status(config);
      sessionStorage.setItem("jamly-token", config.token);
      byId("pair-token").value = "";
      await refresh();
    });
  });
  byId("save-settings").addEventListener("click", () => action(byId("save-settings"), async () => {
    await post("/settings", settings());
    byId("connection").textContent = "Defaults saved. Already queued jobs keep their settings.";
  }));
  byId("split-form").addEventListener("submit", async event => {
    event.preventDefault();
    await action(byId("submit"), async () => {
      const inputs = byId("inputs").value.split(/\r?\n/).map(value => value.trim()).filter(Boolean);
      const selectedFiles = Array.from(byId("files")?.files || []);
      if (!inputs.length && !selectedFiles.length) {
        throw new Error("Enter at least one media URL, local file path, or choose a local audio file.");
      }
      const options = settings();
      await post("/settings", options);
      for (const file of selectedFiles) {
        byId("connection").textContent = `Uploading local audio file ${file.name}...`;
        const uploaded = await uploadFile(file);
        inputs.push(uploaded.source);
      }
      const result = await jamlyClient.addInputs(config, inputs, byId("replace").checked, options);
      byId("inputs").value = "";
      if (byId("files")) byId("files").value = "";
      await refresh();
      byId("connection").textContent = `Queued ${result.newly_added} new job(s).`;
    });
  });
  byId("open-output").addEventListener("click", () => action(byId("open-output"), async () => {
    await post("/settings", settings());
    await post("/app/open-folder");
  }));
  byId("prepare-extension").addEventListener("click", () => action(byId("prepare-extension"), async () => {
    const extension = await post("/app/extension");
    byId("extension-path").textContent = extension.directory;
    byId("extension-url").textContent = location.origin;
    byId("extension-token").value = extension.token;
    byId("extension-setup").hidden = false;
  }));
  byId("copy-token").addEventListener("click", () => action(byId("copy-token"), async () => {
    await navigator.clipboard.writeText(byId("extension-token").value);
    byId("connection").textContent = "Pairing token copied. Keep it private.";
  }));
  byId("quit").addEventListener("click", () => action(byId("quit"), async () => {
    if (!window.confirm("Stop the companion? Unfinished jobs will resume the next time you open Jamly.")) return;
    await post("/app/stop");
    stopped = true;
    clearTimeout(timer);
    sessionStorage.removeItem("jamly-token");
    byId("workspace").hidden = true;
    byId("connection").textContent = "Companion stopped. Open Jamly again to resume.";
  }));
  document.addEventListener("visibilitychange", () => {
    clearTimeout(timer);
    if (!document.hidden) refresh();
  });
  window.addEventListener("unload", () => { stopped = true; clearTimeout(timer); });
  refresh();
})();
