import { startBackgroundPolling } from "./bootstrap/background-polling.js";
import { initializeFeatureActions } from "./bootstrap/feature-actions.js";
import { initializeShellNavigation } from "./bootstrap/shell-navigation.js";
import { initializeProfileLifecycle, pollApplicationState } from "./bootstrap/state-coordinator.js";
import { initializeTabNavigation } from "./bootstrap/tab-navigation.js";
import { initializeWindowChrome } from "./bootstrap/window-chrome.js";

document.addEventListener("DOMContentLoaded", async () => {
  // Before the shell renders, so the window is never briefly captionless. Mounts
  // as a sibling of #app-root, so later renders into it leave the bar alone.
  initializeWindowChrome();
  initializeProfileLifecycle();
  if (!(await initializeShellNavigation(initializeTabNavigation))) return;
  initializeFeatureActions();
  startBackgroundPolling(pollApplicationState);
});
