## Frameshift v2.10.0 — a window that gets out of the way

Frameshift has always drawn a cockpit inside a stock Windows frame: grey title
bar, blue close button, and the app's own black-and-orange starting an inch
below it. Next to a full-screen game it read as exactly what it was, a browser
window left open on the second monitor.

The frame is gone. Frameshift draws its own now, and it can disappear entirely.

### Fullscreen, properly

Press **F11**. The window covers the whole monitor, taskbar included, and takes
the title bar with it. **Esc** brings it back.

Panel mode has had a ⛶ FULL button for a while and inside the app it did
nothing at all. It was asking the *page* to go fullscreen — something a browser
can do and an embedded window cannot, because there is no browser window there
to expand. The button clicked, and that was the end of it. It drives the real
window now. Opened from a tablet it still does the browser version, which is the
right answer on a tablet.

However you leave — F11, Esc, or backing out of Panel mode — the window returns
to exactly where it was, still maximised if it was maximised before. The choice
sticks, too: close Frameshift in fullscreen and it opens in fullscreen.

### A title bar in the same paint as everything else

Thin and dark, with the accent diamond and FRAMESHIFT on the left, minimize,
maximize and close on the right, all in the same holo styling as the rest of the
app. It follows your Color theme — and so does the thin system border around the
window, which is the one piece of it CSS cannot reach.

Giving up the real frame means giving up everything Windows was quietly doing
with it, so all of that had to be rebuilt: dragging the bar, double-clicking it
to maximize, grabbing any edge or corner to resize, Aero Snap, and the Windows 11
snap layouts that appear when you hover over the maximize button. Maximizing
still stops at the taskbar. Fullscreen is the only thing that covers it.

The in-app browser windows — the ones Inara links open in — keep their ordinary
Windows frame on purpose. Cockpit chrome wrapped around somebody else's website
looks like a spoof.

### More room on a large monitor

The desktop layout was capped at 1100 pixels wide, which is sensible in a 1060
pixel window and faintly ridiculous at 1440p. It now widens in two steps, at 1500
and 2200 pixels of window width, out to about 2100. Those are the same tiers the
Panel status page has been using. Tables get the extra room; nothing is stretched
to fill it.

### Upgrade notes

- Update normally from any 2.x release. No database changes, no migration.
- Fullscreen starts off and is remembered per install, alongside your other
  settings in the `data` folder.
- The new window chrome is Windows-only, which is where the released builds run.
