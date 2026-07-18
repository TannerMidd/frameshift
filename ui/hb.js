/* Holo Bracket press FX — one delegated listener, app-wide. */
document.addEventListener("pointerdown", (e) => {
  const b = e.target.closest(".hb");
  if (!b || b.disabled) return;
  const s = document.createElement("span");
  s.className = "hb-fx";
  b.appendChild(s);
  setTimeout(() => s.remove(), 400);
});
