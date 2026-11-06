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
