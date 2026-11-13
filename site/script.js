(function () {
  "use strict";

  const tabs = Array.from(document.querySelectorAll(".tab[data-tab]"));
  const panels = {
    companion: document.getElementById("panel-companion"),
    cli: document.getElementById("panel-cli"),
    extension: document.getElementById("panel-extension"),
    userscript: document.getElementById("panel-userscript"),
  };

  function selectTab(name) {
    for (const tab of tabs) {
      const active = tab.dataset.tab === name;
      tab.classList.toggle("active", active);
      tab.setAttribute("aria-selected", String(active));
    }
    for (const [key, panel] of Object.entries(panels)) {
      if (!panel) continue;
      const active = key === name;
      panel.classList.toggle("active", active);
      panel.hidden = !active;
    }
  }

  for (const tab of tabs) {
    tab.addEventListener("click", () => selectTab(tab.dataset.tab));
  }

  for (const button of document.querySelectorAll(".copy-btn[data-copy]")) {
    button.addEventListener("click", async () => {
      const target = document.getElementById(button.dataset.copy);
      if (!target || !navigator.clipboard) return;
      const previous = button.textContent;
      try {
        await navigator.clipboard.writeText(target.textContent);
        button.textContent = "Copied";
      } catch (_error) {
        button.textContent = "Copy failed";
      }
      setTimeout(() => {
        button.textContent = previous;
      }, 1500);
    });
  }
})();
