// Dialogs: HTMX loads a <dialog> fragment into #modal; open it as a real modal dialog
// (focus trap, Esc to close) and remove it when it closes.
document.addEventListener("htmx:afterSwap", (event) => {
  const dialog = event.detail.target.querySelector("dialog");
  if (dialog && !dialog.open) {
    dialog.showModal();
    const first = dialog.querySelector("input:not([type=hidden]), select, textarea, button");
    if (first) first.focus();
  }
});

document.addEventListener("click", (event) => {
  const closer = event.target.closest("[data-close-dialog]");
  if (closer) {
    event.preventDefault();
    closer.closest("dialog").close();
  }
});

document.addEventListener(
  "close",
  (event) => {
    if (event.target.matches("#modal dialog")) event.target.remove();
  },
  true,
);

// Report editor: formatting buttons insert the simple markup (D06) and trigger autosave.
document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-format]");
  if (!button) return;
  const field = document.getElementById(button.dataset.for);
  if (!field) return;
  const { selectionStart: start, selectionEnd: end, value } = field;
  if (button.dataset.format === "bold") {
    const selected = value.slice(start, end) || "bold text";
    field.setRangeText(`**${selected}**`, start, end, "end");
  } else {
    const lineStart = value.lastIndexOf("\n", start - 1) + 1;
    field.setRangeText("- ", lineStart, lineStart, "end");
  }
  field.focus();
  field.dispatchEvent(new Event("input", { bubbles: true }));
});

// Autosave: warn before leaving with unsaved changes, and say clearly when a save fails.
const unsaved = new Set();
document.addEventListener("input", (event) => {
  if (event.target.matches("[data-autosave]")) unsaved.add(event.target);
});
document.addEventListener("change", (event) => {
  if (event.target.matches("[data-autosave]")) unsaved.add(event.target);
});
document.addEventListener("htmx:afterRequest", (event) => {
  const el = event.detail.elt;
  if (!el.matches || !el.matches("[data-autosave]")) return;
  const status = document.getElementById("save-status");
  if (event.detail.successful) {
    unsaved.delete(el);
    if (status) status.classList.remove("text-error");
  } else if (status) {
    status.textContent = event.detail.xhr && event.detail.xhr.status === 422
      ? event.detail.xhr.responseText
      : "Not saved: check your connection. Your text is still here.";
    status.classList.add("text-error");
  }
});
document.addEventListener("submit", () => unsaved.clear());
window.addEventListener("beforeunload", (event) => {
  if (unsaved.size) {
    event.preventDefault();
    event.returnValue = "";
  }
});
