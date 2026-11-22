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
  let searchTimer;
  let lastRenderKey = "";
  let connectionError = "";
  const queryState = { q: "", status: "all", model: "all", format: "all", page: 1, page_size: 25 };
  const stages = {
    pending: "Queued", metadata: "Checking media", download: "Downloading audio",
    separate: "Loading model / separating audio",
    mix: "Preparing exports", publish: "Saving files", done: "Complete",
    skipped: "Reused existing files", error: "Failed", cancelling: "Cancelling", cancelled: "Cancelled", running: "Working",
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
    drum: [
      "M4 10c0-1.66 3.58-3 8-3s8 1.34 8 3-3.58 3-8 3-8-1.34-8-3z",
      "M4 10v7c0 1.66 3.58 3 8 3s8-1.34 8-3v-7",
      "M8 12.6v6.9",
      "M16 12.6v6.9",
      "M6 2.5l5 5.5",
      "M18 2.5l-5 5.5",
    ],
    "drum-off": [
      "M4 10c0-1.66 3.58-3 8-3s8 1.34 8 3-3.58 3-8 3-8-1.34-8-3z",
      "M4 10v7c0 1.66 3.58 3 8 3s8-1.34 8-3v-7",
      "M6 2.5l5 5.5",
      "M18 2.5l-5 5.5",
      "M2.5 21.5l19-19",
    ],
    note: [
      "M9 18V5l12-2v13",
      "M3 18a3 3 0 1 0 6 0a3 3 0 1 0 -6 0",
      "M15 16a3 3 0 1 0 6 0a3 3 0 1 0 -6 0",
    ],
    "note-off": [
      "M9 18V5l12-2v13",
      "M3 18a3 3 0 1 0 6 0a3 3 0 1 0 -6 0",
      "M15 16a3 3 0 1 0 6 0a3 3 0 1 0 -6 0",
      "M2.5 21.5l19-19",
    ],
    wave: [
      "M2 12s1.5-3 4.5-3 4 3 4 3 1-3 4.5-3 4.5 3 4.5 3",
      "M2 6s1.5-3 4.5-3 4 3 4 3 1-3 4.5-3 4.5 3 4.5 3",
      "M2 18s1.5-3 4.5-3 4 3 4 3 1-3 4.5-3 4.5 3 4.5 3",
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
    stop: [
      "M6 6h12v12H6z",
    ],
  };

  const STATUS_ICONS = {
    "jar-fill": [
      [
        ["M7 6.2H17A1.1 1.1 0 0 1 17 8.4H16.4L18.5 10.1C18.8 10.4 19 10.9 19 11.4V20A2.5 2.5 0 0 1 16.5 22.5H7.5A2.5 2.5 0 0 1 5 20V11.4C5 10.9 5.2 10.4 5.5 10.1L7.6 8.4H7A1.1 1.1 0 0 1 7 6.2ZM7 11.2V19.6A1 1 0 0 0 8 20.6H16A1 1 0 0 0 17 19.6V11.2Z", "evenodd"],
        ["M5 1.2C8.8 1.2 11.8 2.4 12.8 5.2H10.2C9.4 3.8 7.6 3.2 5 3.2Z"],
        ["M12 7.8C13 9.2 13.7 10.1 13.7 11A1.7 1.7 0 0 1 10.3 11C10.3 10.1 11 9.2 12 7.8Z"],
        ["M7 17.5Q9.5 16.4 12 17.5T17 17.5V19.6A1 1 0 0 1 16 20.6H8A1 1 0 0 1 7 19.6Z"],
      ],
      [
        ["M7 6.2H17A1.1 1.1 0 0 1 17 8.4H16.4L18.5 10.1C18.8 10.4 19 10.9 19 11.4V20A2.5 2.5 0 0 1 16.5 22.5H7.5A2.5 2.5 0 0 1 5 20V11.4C5 10.9 5.2 10.4 5.5 10.1L7.6 8.4H7A1.1 1.1 0 0 1 7 6.2ZM7 11.2V19.6A1 1 0 0 0 8 20.6H16A1 1 0 0 0 17 19.6V11.2Z", "evenodd"],
        ["M5 1.2C9.5 1.2 13.2 2.8 13.2 7.4V12.5H10.8V7.4C10.8 4.4 8.6 3.2 5 3.2Z"],
        ["M7 12.4Q9.5 13.5 12 12.4T17 12.4V19.6A1 1 0 0 1 16 20.6H8A1 1 0 0 1 7 19.6Z"],
      ],
    ],
    "toast-spread": [
      [
        ["M2 10.5C2 7.7 5.2 6.5 9.3 6.5C13.4 6.5 16.6 7.7 16.6 10.5C16.6 12.1 15.7 13 15.1 13.5V20.5A2 2 0 0 1 13.1 22.5H5.5A2 2 0 0 1 3.5 20.5V13.5C2.9 13 2 12.1 2 10.5ZM4.1 10.6C4.1 9.1 6.2 8.5 9.3 8.5C12.4 8.5 14.5 9.1 14.5 10.6C14.5 11.5 13.8 12 13.2 12.4V20.4H5.4V12.4C4.8 12 4.1 11.5 4.1 10.6Z", "evenodd"],
        ["M6.1 13.3C6.1 11.9 7.5 11.2 9 11.7C10.1 12.1 10.5 13.4 10 14.6C9.4 15.8 7.6 16.1 6.6 15.2C6.1 14.7 6.1 14 6.1 13.3Z"],
        ["M17.2 1.4C18.3 0.7 19.7 1 20.4 2.1C21.1 3.2 20.8 4.6 19.7 5.3L16.3 7.4L14.5 4.6ZM14 5.2L15.7 7.9L10.3 11.3C9.4 11.9 8.1 11.3 8.3 10.2C8.5 9.1 9.3 8 10.7 7.1Z"],
      ],
      [
        ["M2 10.5C2 7.7 5.2 6.5 9.3 6.5C13.4 6.5 16.6 7.7 16.6 10.5C16.6 12.1 15.7 13 15.1 13.5V20.5A2 2 0 0 1 13.1 22.5H5.5A2 2 0 0 1 3.5 20.5V13.5C2.9 13 2 12.1 2 10.5ZM4.1 10.6C4.1 9.1 6.2 8.5 9.3 8.5C12.4 8.5 14.5 9.1 14.5 10.6C14.5 11.5 13.8 12 13.2 12.4V20.4H5.4V12.4C4.8 12 4.1 11.5 4.1 10.6Z", "evenodd"],
        ["M6 12C7.8 11 11.2 11 12.6 12.3V17.4C12.6 18.6 11.1 19.2 9.3 19.2C7.5 19.2 6 18.5 6 17.2Z"],
        ["M20.2 8.2C21.5 7.8 22.8 8.6 23.1 9.9C23.4 11.2 22.6 12.5 21.3 12.8L17.4 13.8L16.4 10.3ZM15.7 10.6L16.7 14L10.6 15.7C9.6 16 8.6 15.1 9 14C9.4 13 10.5 12.1 12.1 11.6Z"],
      ],
    ],
    "toast-cut": [
      [
        ["M4.5 12.6C4.5 10 7.8 9 12 9C16.2 9 19.5 10 19.5 12.6C19.5 14.1 18.6 14.9 18 15.4V21A2 2 0 0 1 16 23H8A2 2 0 0 1 6 21V15.4C5.4 14.9 4.5 14.1 4.5 12.6Z"],
        ["M10.8 1A1.2 1.2 0 0 1 13.2 1V4.2H10.8ZM10.4 4.8H13.6V6.6C13.6 7.4 12.8 8 11.6 8C10.8 8 10.4 7.5 10.4 6.8Z"],
      ],
      [
        ["M2.3 12.5C2.3 10 5.3 9 9.4 8.9L10.2 23H5.8A2 2 0 0 1 3.8 21V15.1C3.1 14.6 2.3 13.8 2.3 12.5Z"],
        ["M21.7 12.5C21.7 10 18.7 9 14.6 8.9L13.8 23H18.2A2 2 0 0 0 20.2 21V15.1C20.9 14.6 21.7 13.8 21.7 12.5Z"],
        ["M10.8 2.5A1.2 1.2 0 0 1 13.2 2.5V7.2H10.8ZM10.8 8H13.2V18.8C13.2 19.8 12.5 20.6 11.8 20.6C11.1 20.6 10.8 19.9 10.8 19.1Z"],
      ],
    ],
    "jar-explode": [
      [
        ["M7.5 2L16.3 0.6L16.7 3.1L7.9 4.5Z"],
        ["M4.3 9.4L9.5 10.8L7.5 14.3L10.2 16.7L8.7 21.8H6.4A2.2 2.2 0 0 1 4.2 19.6V11.2Z"],
        ["M19.7 9.4L14.5 10.8L16.5 14.3L13.8 16.7L15.3 21.8H17.6A2.2 2.2 0 0 0 19.8 19.6V11.2Z"],
        ["M9.8 18.8L12 17.5L14.2 18.8L13.8 22.2H10.2Z"],
        ["M1.8 5.2L5.5 6.8L4.5 8.7L1 6.9ZM22.2 5.2L23 6.9L19.5 8.7L18.5 6.8ZM11.1 5.2H12.9L13.6 9.6H10.4Z"],
      ],
    ],
  };

  const STAGE_ICONS = {
    metadata: "jar-fill",
    download: "jar-fill",
    separate: "toast-spread",
    running: "toast-spread",
    mix: "toast-cut",
    publish: "toast-cut",
    error: "jar-explode",
  };

  function statusIcon(name) {
    const frames = STATUS_ICONS[name];
    const svg = document.createElementNS(SVG_NS, "svg");
    svg.setAttribute("class", "status-icon");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("width", "18");
    svg.setAttribute("height", "18");
    svg.setAttribute("fill", "currentColor");
    svg.setAttribute("aria-hidden", "true");
    if (frames.length > 1) {
      svg.style.setProperty("--jamly-anim-delay", `-${Math.floor(performance.now() % 800)}ms`);
    }
    frames.forEach((paths, index) => {
      const group = document.createElementNS(SVG_NS, "g");
      if (frames.length > 1) {
        group.setAttribute("class", index === 0 ? "frame-a" : "frame-b");
      }
      for (const [d, fillRule] of paths) {
        const path = document.createElementNS(SVG_NS, "path");
        path.setAttribute("d", d);
        if (fillRule) path.setAttribute("fill-rule", fillRule);
        group.appendChild(path);
      }
      svg.appendChild(group);
    });
    return svg;
  }

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

  const STEM_SHORT = { vocals: "vox", bass: "bass", guitar: "gtr", piano: "keys", other: "other" };

  function stemButton(job, output, label) {
    const element = document.createElement("button");
    element.type = "button";
    element.className = "stem-btn";
    element.dataset.url = output.url;
    const ariaLabel = "Play " + label;
    element.title = ariaLabel;
    element.setAttribute("aria-label", ariaLabel);
    const negative = output.kind.startsWith("no_");
    const base = negative ? output.kind.slice(3) : output.kind;
    if (base === "drums") {
      element.appendChild(svgIcon(negative ? "drum-off" : "drum"));
    } else {
      element.appendChild(svgIcon(negative ? "note-off" : "note"));
      const span = document.createElement("span");
      span.textContent = (negative ? "no " : "") + (STEM_SHORT[base] || base);
      element.appendChild(span);
    }
    element.addEventListener("click", () => action(element, () => playOutput(job, output, label)));
    return element;
  }

  function stemIconKey(kind) {
    const negative = typeof kind === "string" && kind.startsWith("no_");
    const base = negative ? kind.slice(3) : kind;
    return (base === "drums" ? "drum" : "note") + (negative ? "-off" : "");
  }

  function stemGroup(job, outputs, labelFor, caption) {
    const group = document.createElement("div");
    group.className = "stem-group";
    const grid = document.createElement("div");
    grid.className = "stem-grid";
    for (const output of outputs) {
      if (output.kind === "metadata") continue;
      grid.appendChild(stemButton(job, output, labelFor(output)));
    }
    if (!grid.childElementCount) return null;
    group.appendChild(grid);
    if (caption) {
      const text = document.createElement("span");
      text.className = "stem-caption";
      text.textContent = caption;
      group.appendChild(text);
    }
    return group;
  }

  function localSource(job) {
    const source = job.source_url;
    if (!source || source === job.watch_id) return null;
    try {
      const parsed = new URL(source);
      if (parsed.protocol === "http:" || parsed.protocol === "https:") return null;
    } catch (_error) {
      // Local filesystem paths are not valid URLs; treat as local.
    }
    return true;
  }

  function buildPlayable(job) {
    const entries = [];
    if (localSource(job)) {
      entries.push({ key: "/source/" + job.watch_id, label: "Original", kind: "source", name: "" });
    }
    const outputs = (job.outputs || []).filter(o => o.kind !== "metadata");
    const positives = outputs.filter(o => !o.kind.startsWith("no_"));
    const negatives = outputs.filter(o => o.kind.startsWith("no_"));
    for (const output of positives.concat(negatives)) {
      entries.push({ key: output.url, label: outputLabel(output.kind), kind: "output", name: output.name, stemKind: output.kind });
    }
    return entries;
  }

    function guessContentType(url) {
    const match = /\.(\w+)$/i.exec(typeof url === "string" ? url : "");
    switch ((match && match[1].toLowerCase()) || "") {
      case "wav": return "audio/wav";
      case "mp3": return "audio/mpeg";
      case "flac": return "audio/flac";
      case "m4a": return "audio/mp4";
      case "ogg": return "audio/ogg";
      case "opus": return "audio/opus";
      case "aac": return "audio/aac";
      case "aiff":
      case "aif": return "audio/aiff";
      default: return "audio/mpeg";
    }
  }

  function audioFetchUrl(key) {
    if (typeof key === "string" && /^\/(source|outputs)\//.test(key)) {
      const segment = decodeURIComponent(key.split("/")[2]);
      if (key.startsWith("/source/")) return jamlyClient.sourceURL(config, segment);
      return jamlyClient.outputURL(config, key);
    }
    return key;
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

  const player = { url: null, jobId: null, objectURL: null, controller: null };

  function playbackRate() {
    return Number(byId("player-speed").value) / 100;
  }

  function applyPlaybackRate() {
    const audio = byId("player-audio");
    const rate = playbackRate();
    audio.preservesPitch = true;
    audio.mozPreservesPitch = true;
    audio.webkitPreservesPitch = true;
    audio.defaultPlaybackRate = rate;
    audio.playbackRate = rate;
    byId("player-speed-value").textContent = Math.round(rate * 100) + "%";
  }

  function refreshSelectionState() {
    const audio = byId("player-audio");
    const active = Boolean(player.url);
    const playing = active && !audio.paused;
    for (const button of document.querySelectorAll("#jobs .stem-btn, #jobs .player-toggle, #player .player-toggle")) {
      const isActive = active && button.dataset.url === player.url;
      button.classList.toggle("active", isActive);
      button.classList.toggle("playing", isActive && playing);
      button.setAttribute("aria-pressed", String(isActive));
    }
  }

  function buildToggle(entry) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "player-toggle";
    button.dataset.url = entry.key;
    const iconKey = entry.kind === "source" ? "wave" : stemIconKey(entry.stemKind);
    button.appendChild(svgIcon(iconKey));
    button.setAttribute("aria-label", entry.label);
    return button;
  }

  function renderPlayerToggles() {
    const container = byId("player-toggles");
    if (!container) return;
    container.replaceChildren();
    if (!player.url || !Array.isArray(player.entries)) return;
    for (const entry of player.entries) {
      const button = buildToggle(entry);
      button.addEventListener("click", () => selectAudio(player.job, entry));
      container.appendChild(button);
    }
  }

  function playbackCluster(job) {
    const cluster = document.createElement("div");
    cluster.className = "player-toggles";
    cluster.setAttribute("role", "group");
    cluster.setAttribute("aria-label", "Play audio sources");
    for (const entry of buildPlayable(job)) {
      const button = buildToggle(entry);
      button.addEventListener("click", () => action(button, () => selectAudio(job, entry)));
      cluster.appendChild(button);
    }
    return cluster;
  }

  async function selectAudio(job, entry) {
    const audio = byId("player-audio");
  if (player.url === entry.key && !player.controller) {
    if (audio.paused) {
      return audio.play();
    }
    audio.pause();
    return;
  }
  const sameTrack = player.jobId === job.watch_id && Boolean(player.url);
  const resumeAt = sameTrack ? audio.currentTime : 0;
  const resume = sameTrack ? !audio.paused : true;
  player.controller?.abort();
  const controller = new AbortController();
  player.controller = controller;
  byId("player").hidden = false;
  document.body.classList.add("has-player");
  byId("player-title").textContent = job.title || job.watch_id;
  byId("player-stem").textContent = `Loading ${entry.label}...`;
  try {
    const response = await fetch(audioFetchUrl(entry.key), {
      headers: { Authorization: "Bearer " + config.token },
      cache: "no-store",
      signal: controller.signal,
    });
    if (!response.ok) {
      let message = "";
      try {
        message = (await response.json()).error || "";
      } catch (_error) {
        // Non-JSON error bodies fall back to the HTTP status below.
      }
      throw new Error(message || "Could not load this audio (HTTP " + response.status + ")");
    }
    const data = await response.blob();
    const type = entry.kind === "source" ? guessContentType(entry.key)
      : (/\.wav$/i.test(entry.name || "") ? "audio/wav" : "audio/mpeg");
    const objectURL = URL.createObjectURL(new Blob([data], { type }));
    const previous = player.objectURL;
    Object.assign(player, {
      url: entry.key, jobId: job.watch_id, objectURL, controller: null,
      entries: buildPlayable(job), job,
    });
    audio.src = objectURL;
    if (previous) URL.revokeObjectURL(previous);
    audio.addEventListener("loadedmetadata", () => {
      if (resumeAt && Number.isFinite(audio.duration)) {
        audio.currentTime = Math.min(resumeAt, audio.duration);
      }
      applyPlaybackRate();
      if (resume) audio.play().catch(() => refreshSelectionState());
    }, { once: true });
    audio.load();
    byId("player-stem").textContent = entry.label;
    renderPlayerToggles();
    refreshSelectionState();
  } catch (error) {
    if (error.name === "AbortError") return;
    if (player.controller === controller) player.controller = null;
    byId("player-stem").textContent = player.url ? "Could not switch audio" : "Could not load audio";
    throw error;
  }
}

function playOutput(job, output, label) {
  selectAudio(job, { key: output.url, label, kind: "output", name: output.name });
}

  function closePlayer() {
    const audio = byId("player-audio");
    player.controller?.abort();
    audio.pause();
    audio.removeAttribute("src");
    audio.load();
    if (player.objectURL) URL.revokeObjectURL(player.objectURL);
    Object.assign(player, {
      url: null, jobId: null, objectURL: null, controller: null, entries: [], job: null,
    });
    byId("player").hidden = true;
    document.body.classList.remove("has-player");
    renderPlayerToggles();
    refreshSelectionState();
  }

  function renderPagination(status) {
    const pagination = status.pagination || {
      page: 1,
      page_size: status.jobs.length || 25,
      total_matched: status.jobs.length,
      total_pages: 1,
    };
    queryState.page = pagination.page;
    const filtered = Boolean(
      queryState.q || queryState.status !== "all" || queryState.model !== "all" || queryState.format !== "all"
    );
    if (byId("filter-reset")) {
      byId("filter-reset").hidden = !filtered;
    }
    const matchText = filtered ? ` | Matched: ${pagination.total_matched}` : "";
    byId("counts").textContent =
      `Queued: ${status.pending} | Complete: ${status.done} | Failed: ${status.failed}${matchText}`;
    if (byId("jobs-empty")) {
      const empty = byId("jobs-empty");
      if (!status.jobs.length) {
        empty.hidden = false;
        empty.textContent = status.total === 0
          ? "No exports yet."
          : "No matching exports. Adjust or reset your filters.";
      } else {
        empty.hidden = true;
      }
    }
    if (byId("pagination")) {
      const nav = byId("pagination");
      nav.hidden = pagination.total_matched === 0;
      if (pagination.total_matched > 0) {
        const start = (pagination.page - 1) * pagination.page_size + 1;
        const end = start + status.jobs.length - 1;
        byId("page-summary").textContent = `Showing ${start}-${end} of ${pagination.total_matched}`;
        byId("page-indicator").textContent = `Page ${pagination.page} of ${pagination.total_pages}`;
        byId("page-prev").disabled = pagination.page <= 1;
        byId("page-next").disabled = pagination.page >= pagination.total_pages;
      }
    }
  }

  function render(status) {
    models = status.models;
    stems = status.stems || [
      { id: "drums", label: "Drums (drums + drumless)", models: ["htdemucs", "htdemucs_ft", "htdemucs_6s"] },
    ];
    if (!initialized) {
      byId("model").replaceChildren();
      if (byId("filter-model")) {
        const allOpt = document.createElement("option");
        allOpt.value = "all";
        allOpt.textContent = "All presets";
        byId("filter-model").replaceChildren(allOpt);
      }
      for (const model of models) {
        const option = document.createElement("option");
        option.value = model.id;
        option.textContent = model.label;
        byId("model").appendChild(option);
        if (byId("filter-model")) {
          byId("filter-model").appendChild(option.cloneNode(true));
        }
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
    renderPagination(status);
    const hasTerminal = status.done > 0 || status.failed > 0 || status.jobs.some(
      job => !["pending", "running"].includes(job.status)
    );
    if (byId("clear-exports")) {
      byId("clear-exports").disabled = !hasTerminal;
    }
    const renderKey = JSON.stringify({
      jobs: status.jobs,
      current: status.current,
      pagination: status.pagination,
      error: status.error,
    });
    if (!status.current && renderKey === lastRenderKey) {
      refreshSelectionState();
      return;
    }
    lastRenderKey = renderKey;
    byId("jobs").replaceChildren();
    for (const job of status.jobs) {
      const row = document.createElement("li");
      const main = document.createElement("div");
      main.className = "job-main";
      const info = document.createElement("div");
      info.className = "job-info";
      const title = document.createElement("p");
      title.className = "job-title";
      title.textContent = job.title || job.watch_id;
      info.appendChild(title);
      const isCurrent = status.current?.watch_id === job.watch_id;
      const isCancelling = isCurrent && Boolean(status.current?.cancelling);
      const stage = isCancelling ? "cancelling" : (isCurrent ? status.current.stage : job.status);
      const badge = document.createElement("span");
      const badgeTone = (job.status === "done" || job.status === "skipped")
        ? "done"
        : (["pending", "running"].includes(job.status)
          ? "active"
          : (job.status === "cancelled" ? "muted" : "error"));
      badge.className = `status-badge ${badgeTone}`;
      const iconName = STAGE_ICONS[stage];
      if (iconName) badge.appendChild(statusIcon(iconName));
      const progressText = isCurrent && !isCancelling && typeof status.current?.progress === "number"
        ? ` ${status.current.progress}%` : "";
      badge.appendChild(document.createTextNode((stages[stage] || stage) + progressText));
      const detail = document.createElement("div");
      detail.className = "job-detail";
      const model = models.find(choice => choice.id === job.model)?.label || job.model;
      const stemTag = job.stem && job.stem !== "drums" ? ` | ${job.stem}` : "";
      const summary = document.createElement("span");
      summary.textContent = `${job.audio_format.toUpperCase()} | ${model}${stemTag}`;
      detail.append(summary, badge);
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
        actions.appendChild(playbackCluster(job));
        for (const exp of job.exports || []) {
          const expModel = models.find(choice => choice.id === exp.model)?.label || exp.model;
          const expStem = exp.stem && exp.stem !== "drums" ? ` ${exp.stem}` : "";
          const expFormat = String(exp.audio_format || "").toUpperCase();
          const suffix = `${expModel}${expStem} ${expFormat}`;
          const group = stemGroup(job, exp.outputs || [],
            output => `${outputLabel(output.kind)} (${suffix})`, suffix);
          if (group) actions.appendChild(group);
        }
      }
      const controls = document.createElement("div");
      controls.className = "job-controls";
      const topRow = document.createElement("div");
      topRow.className = "control-row";
      const bottomRow = document.createElement("div");
      bottomRow.className = "control-row";
      const sourceBtn = sourceControl(job);
      if (sourceBtn) topRow.appendChild(sourceBtn);
      if (hasOutputs) {
        topRow.appendChild(iconButton("folder", "Show files in folder", () => post("/app/open-folder", {
          id: job.watch_id,
        })));
      }
      if (["pending", "running"].includes(job.status)) {
        const cancelBtn = document.createElement("button");
        cancelBtn.type = "button";
        cancelBtn.className = "toolbar-btn cancel-btn";
        cancelBtn.disabled = isCancelling;
        cancelBtn.title = isCancelling ? "Cancelling processing" : "Cancel processing";
        cancelBtn.setAttribute("aria-label", cancelBtn.title);
        const label = document.createElement("span");
        label.textContent = isCancelling ? "Cancelling..." : "Cancel";
        cancelBtn.append(svgIcon("stop"), label);
        cancelBtn.addEventListener("click", () => action(cancelBtn, async () => {
          await jamlyClient.cancel(config, job.watch_id);
          await refresh();
        }));
        bottomRow.appendChild(cancelBtn);
      } else {
        bottomRow.appendChild(iconButton("retry", "Retry / check files", async () => {
          await jamlyClient.retry(config, job.watch_id, byId("replace").checked);
          await refresh();
        }));
        bottomRow.appendChild(iconButton("clear", "Remove from list", async () => {
          await post("/app/clear", { id: job.watch_id });
          await refresh();
        }, "danger"));
      }
      for (const controlRow of [topRow, bottomRow]) {
        if (controlRow.childElementCount) controls.appendChild(controlRow);
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
    refreshSelectionState();
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
    let pollDelay = 2000;
    try {
      const status = await jamlyClient.status(config, queryState);
      byId("connection").textContent = "Connected to your local companion. Jobs keep running even after you close this page.";
      if (byId("error").textContent === connectionError) byId("error").textContent = "";
      connectionError = "";
      render(status);
      pollDelay = (status.pending > 0 || Boolean(status.current)) ? 2000 : 6000;
    } catch (error) {
      byId("connection").textContent = "Offline or session expired. Reopen Jamly; displayed jobs may be stale.";
      byId("pairing").hidden = false;
      connectionError = error.message || String(error);
      showError(error);
    } finally {
      refreshing = false;
      if (!stopped) timer = setTimeout(refresh, pollDelay);
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
    closePlayer();
    sessionStorage.removeItem("jamly-token");
    byId("workspace").hidden = true;
    byId("connection").textContent = "Companion stopped. Open Jamly again to resume.";
  }));
  byId("filter-query")?.addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      queryState.q = byId("filter-query").value.trim();
      queryState.page = 1;
      refresh();
    }, 200);
  });
  for (const [id, key] of [["filter-status", "status"], ["filter-model", "model"], ["filter-format", "format"]]) {
    byId(id)?.addEventListener("change", () => {
      queryState[key] = byId(id).value;
      queryState.page = 1;
      refresh();
    });
  }
  byId("filter-reset")?.addEventListener("click", () => {
    clearTimeout(searchTimer);
    Object.assign(queryState, { q: "", status: "all", model: "all", format: "all", page: 1 });
    byId("filter-query").value = "";
    byId("filter-status").value = "all";
    byId("filter-model").value = "all";
    byId("filter-format").value = "all";
    refresh();
  });
  byId("page-size")?.addEventListener("change", () => {
    queryState.page_size = Number(byId("page-size").value) || 25;
    queryState.page = 1;
    refresh();
  });
  for (const [id, delta] of [["page-prev", -1], ["page-next", 1]]) {
    byId(id)?.addEventListener("click", () => {
      queryState.page = Math.max(1, queryState.page + delta);
      refresh().then(() => {
        document.querySelector(".exports-header")?.scrollIntoView({ behavior: "smooth", block: "nearest" });
      });
    });
  }
  byId("player-speed").addEventListener("input", applyPlaybackRate);
  byId("player-speed-reset").addEventListener("click", () => {
    byId("player-speed").value = "100";
    applyPlaybackRate();
  });
  byId("player-close").addEventListener("click", closePlayer);
  for (const eventName of ["play", "pause", "ended"]) {
    byId("player-audio").addEventListener(eventName, refreshSelectionState);
  }
  applyPlaybackRate();
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
