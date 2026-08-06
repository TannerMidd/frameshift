import { requireById } from "../../core/dom.js";

let initialized = false;

function closeNotes() {
  requireById("notes-modal").classList.add("hidden");
}

/** Own release-note dialog dismissal behavior. */
export function initializeUpdaterControls() {
  if (initialized) return;
  initialized = true;
  requireById("notes-close").addEventListener("click", closeNotes);
  requireById("notes-modal").addEventListener("click", (event) => {
    if (event.target === event.currentTarget) closeNotes();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    const modal = requireById("notes-modal");
    if (modal.classList.contains("hidden")) return;
    closeNotes();
    // Escape is shared — leaving the desktop window's fullscreen listens for it
    // too. Marking it used is how the one nearest the commander wins, without
    // either handler having to know the other exists.
    event.preventDefault();
  });
}
