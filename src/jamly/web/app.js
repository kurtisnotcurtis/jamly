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

  function formatTime(epochSeconds) {
    if (typeof epochSeconds !== "number" || !Number.isFinite(epochSeconds)) return "";
    return new Date(epochSeconds * 1000).toLocaleString([], {
      month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
    });
  }

  function formatDuration(seconds) {
    if (typeof seconds !== "number" || !Number.isFinite(seconds) || seconds < 0) return "";
    if (seconds < 1) return "<1s";
    const total = Math.round(seconds);
    if (total < 60) return `${total}s`;
    const mins = Math.floor(total / 60);
    const secs = total % 60;
    if (mins < 60) return secs ? `${mins}m ${secs}s` : `${mins}m`;
    const hours = Math.floor(mins / 60);
    const remMins = mins % 60;
    return remMins ? `${hours}h ${remMins}m` : `${hours}h`;
  }

  function formatJobTimes(job, status, isCurrent) {
    const parts = [];
    if (typeof job.requested_at === "number") {
      parts.push(`Requested ${formatTime(job.requested_at)}`);
    }
    if (!["pending", "running"].includes(job.status) && typeof job.finished_at === "number") {
      parts.push(`Finished ${formatTime(job.finished_at)}`);
    }
    if (!["pending", "running"].includes(job.status) && typeof job.duration_seconds === "number") {
      parts.push(`Took ${formatDuration(job.duration_seconds)}`);
    } else if (isCurrent || job.status === "running") {
      const startedAt = typeof job.started_at === "number"
        ? job.started_at
        : (isCurrent && typeof status.current?.started_at === "number" ? status.current.started_at : null);
      if (startedAt !== null) {
        parts.push(`Elapsed ${formatDuration(Math.max(0, Date.now() / 1000 - startedAt))}`);
      }
    }
    if (!parts.length && typeof job.updated_at === "number") {
      parts.push(`Updated ${formatTime(job.updated_at)}`);
    }
    return parts.join(" · ");
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

  const SVG_NS = "http://www.w3.org/2000/svg";
  const ICONS = {
    download: [
      "M12 3v11",
      "M8 10l4 4 4-4",
      "M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2",
    ],
    folder: [
      "M3 6a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z",
    ],
    external: [
      "M14 4h6v6",
      "M10 14L20 4",
      "M20 14v5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h5",
    ],
    file: [
      "M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z",
      "M14 3v6h6",
    ],
    retry: [
      "M21 12a9 9 0 1 1-2.64-6.36",
      "M21 3v6h-6",
    ],
    rescan: [
      "M3 12a9 9 0 0 1 15.36-6.36L21 8",
      "M21 3v5h-5",
      "M21 12a9 9 0 0 1-15.36 6.36L3 16",
      "M3 21v-5h5",
    ],
    clear: [
      "M18 6L6 18",
      "M6 6l12 12",
    ],
  };

  function svgIcon(name) {
    const svg = document.createElementNS(SVG_NS, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("width", "16");
    svg.setAttribute("height", "16");
    svg.setAttribute("fill", "none");
    svg.setAttribute("stroke", "currentColor");
    svg.setAttribute("stroke-width", "2");
    svg.setAttribute("stroke-linecap", "round");
    svg.setAttribute("stroke-linejoin", "round");
    svg.setAttribute("aria-hidden", "true");
    for (const d of ICONS[name] || []) {
      const path = document.createElementNS(SVG_NS, "path");
      path.setAttribute("d", d);
      svg.appendChild(path);
    }
    return svg;
  }

  function enhanceToolbarButton(id, iconName) {
    const el = byId(id);
    if (!el) return;
    const label = el.textContent.trim();
    el.replaceChildren(svgIcon(iconName), document.createTextNode(label));
  }

  function iconButton(iconName, label, task, extraClass = "") {
    const element = document.createElement("button");
    element.type = "button";
    element.className = extraClass ? `icon-btn ${extraClass}` : "icon-btn";
    element.title = label;
    element.setAttribute("aria-label", label);
    element.appendChild(svgIcon(iconName));
    element.addEventListener("click", () => action(element, task));
    return element;
  }

  function downloadButton(text, ariaLabel, task) {
    const element = document.createElement("button");
    element.type = "button";
    element.className = "download-btn";
    element.title = ariaLabel;
    element.setAttribute("aria-label", ariaLabel);
    const span = document.createElement("span");
    span.textContent = text;
    element.append(svgIcon("download"), span);
    element.addEventListener("click", () => action(element, task));
    return element;
  }

  function sourceControl(job) {
    const source = job.source_url;
    if (!source) return null;
    try {
      const parsed = new URL(source);
      if (parsed.protocol === "http:" || parsed.protocol === "https:") {
        const link = document.createElement("a");
        link.className = "icon-btn";
        link.href = parsed.href;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.title = "Open original URL";
        link.setAttribute("aria-label", "Open original URL");
        link.appendChild(svgIcon("external"));
        return link;
      }
    } catch (_error) {
      // Local filesystem paths are opened via the backend folder opener.
    }
    if (source === job.watch_id) return null;
    return iconButton("file", "Show source file folder", () => post("/app/open-folder", {
      id: job.watch_id, target: "source",
    }));
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
    const hasTerminal = status.jobs.some(job => !["pending", "running"].includes(job.status));
    if (byId("clear-exports")) {
      byId("clear-exports").disabled = !hasTerminal;
    }
    byId("jobs").replaceChildren();
    for (const job of status.jobs) {
      const row = document.createElement("li");
      const main = document.createElement("div");
      main.className = "job-main";
      const info = document.createElement("div");
      info.className = "job-info";
      const titleRow = document.createElement("div");
      titleRow.className = "job-title-row";
      const title = document.createElement("p");
      title.className = "job-title";
      title.textContent = job.title || job.watch_id;
      titleRow.appendChild(title);
      const isCurrent = status.current?.watch_id === job.watch_id;
      const stage = isCurrent ? status.current.stage : job.status;
      const badge = document.createElement("span");
      const badgeTone = (job.status === "done" || job.status === "skipped")
        ? "done"
        : (["pending", "running"].includes(job.status) ? "active" : "error");
      badge.className = `status-badge ${badgeTone}`;
      badge.textContent = stages[stage] || stage;
      titleRow.appendChild(badge);
      info.appendChild(titleRow);
      const detail = document.createElement("p");
      detail.className = "job-detail";
      const progressText = isCurrent && typeof status.current?.progress === "number"
        ? ` (${status.current.progress}%)` : "";
      const model = models.find(choice => choice.id === job.model)?.label || job.model;
      const stemTag = job.stem && job.stem !== "drums" ? ` | ${job.stem}` : "";
      detail.textContent = `${stages[stage] || stage}${progressText} | ${model}${stemTag} | ${job.audio_format.toUpperCase()}`;
      info.appendChild(detail);
      const timingText = formatJobTimes(job, status, isCurrent);
      if (timingText) {
        const times = document.createElement("p");
        times.className = "job-times";
        times.textContent = timingText;
        info.appendChild(times);
      }
      if (job.error) {
        const error = document.createElement("p");
        error.className = "error";
        error.textContent = job.error;
        info.appendChild(error);
      }
      main.appendChild(info);

      const actions = document.createElement("div");
      actions.className = "job-actions";
      const hasOutputs = Boolean(job.outputs.length || job.exports?.length);
      if (hasOutputs) {
        for (const output of job.outputs) {
          if (output.kind === "metadata") continue;
          const label = outputLabel(output.kind);
          actions.appendChild(downloadButton(label, "Save a copy: " + label, () => download(output)));
        }
        for (const exp of job.exports || []) {
          const expModel = models.find(choice => choice.id === exp.model)?.label || exp.model;
          const expStem = exp.stem && exp.stem !== "drums" ? ` ${exp.stem}` : "";
          const expFormat = String(exp.audio_format || "").toUpperCase();
          for (const output of exp.outputs || []) {
            if (output.kind === "metadata") continue;
            const kindLabel = outputLabel(output.kind);
            actions.appendChild(downloadButton(
              `${kindLabel} (${expModel}${expStem} ${expFormat})`,
              `Save a copy: ${kindLabel} (${expModel}${expStem} ${expFormat})`,
              () => download(output),
            ));
          }
        }
      }
      const controls = document.createElement("div");
      controls.className = "job-controls";
      const sourceBtn = sourceControl(job);
      if (sourceBtn) controls.appendChild(sourceBtn);
      if (hasOutputs) {
        controls.appendChild(iconButton("folder", "Show files in folder", () => post("/app/open-folder", {
          id: job.watch_id,
        })));
      }
      if (!["pending", "running"].includes(job.status)) {
        controls.appendChild(iconButton("retry", "Retry / check files", async () => {
          await jamlyClient.retry(config, job.watch_id, byId("replace").checked);
          await refresh();
        }));
        controls.appendChild(iconButton("clear", "Remove from list", async () => {
          await post("/app/clear", { id: job.watch_id });
          await refresh();
        }, "danger"));
      }
      if (controls.childElementCount) {
        actions.appendChild(controls);
      }
      if (actions.childElementCount) {
        main.appendChild(actions);
      }
      row.appendChild(main);
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
  enhanceToolbarButton("open-exports-folder", "folder");
  enhanceToolbarButton("rescan-exports", "rescan");
  enhanceToolbarButton("clear-exports", "clear");
  byId("open-exports-folder")?.addEventListener("click", () => action(byId("open-exports-folder"), async () => {
    await post("/settings", settings());
    await post("/app/open-folder");
  }));
  byId("rescan-exports")?.addEventListener("click", () => action(byId("rescan-exports"), async () => {
    await post("/settings", settings());
    const result = await post("/app/rescan");
    await refresh();
    byId("connection").textContent = result.restored
      ? `Restored ${result.restored} export(s) from your output folder.`
      : "Export folder scanned; list is up to date.";
  }));
  byId("clear-exports")?.addEventListener("click", () => action(byId("clear-exports"), async () => {
    const result = await post("/app/clear");
    await refresh();
    byId("connection").textContent = result.removed
      ? `Cleared ${result.removed} item(s) from the list. Audio files remain in your output folder.`
      : "No finished or failed items to clear.";
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
  window.addEventListener("pagehide", (event) => {
    clearTimeout(timer);
    if (!event.persisted) stopped = true;
  });
  refresh();
})();
