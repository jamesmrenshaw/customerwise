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

const unsaved = new Set();

// Report editor (Trix): answers are text only, so files can't be dropped or pasted in.
// Evidence goes through the upload checks instead.
document.addEventListener("trix-file-accept", (event) => event.preventDefault());
document.addEventListener("trix-change", (event) => {
  if (event.target.matches("[data-autosave]")) unsaved.add(event.target);
});

// Autosave: warn before leaving with unsaved changes, and say clearly when a save fails.
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
