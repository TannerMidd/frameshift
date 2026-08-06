import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const outputRoot = path.join(root, "ui", "styles");
const manifest = [
  ["tokens/tokens.css", "tokens"],
  ["base/fonts.css", "base"],
  ["base/global.css", "base"],
  ["components/startup.css", "components"],
  ["components/navigation-forms.css", "components"],
  ["components/route-progress.css", "components"],
  ["components/tables-system-stations.css", "components"],
  ["features/engineering.css", "features"],
  ["features/status.css", "features"],
  ["features/guides-settings.css", "features"],
  ["panel/shell.css", "panel"],
  ["panel/content.css", "panel"],
  ["panel/arrangement.css", "panel"],
  ["features/ops.css", "features"],
  ["features/specialists.css", "features"],
  ["features/specialists-tools.css", "features"],
  ["features/galaxy.css", "features"],
  ["themes/picker.css", "themes"],
  ["components/dialogs-settings.css", "components"],
  ["panel/telemetry.css", "panel"],
  ["panel/telemetry-session.css", "panel"],
  ["panel/quick-controls.css", "panel"],
  ["features/launch-exploration.css", "features"],
  ["motion/motion.css", "motion"],
  ["features/security-extensions.css", "features"],
  ["panel/hud-controls.css", "panel"],
  ["features/jump-sequence.css", "features"],
  ["features/material-finder.css", "features"],
  // Declared last so the desktop window's frame outranks every app layer.
  ["chrome/titlebar.css", "chrome"],
];

function findCssFiles(directory, prefix = "") {
  return fs.readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const relativePath = path.posix.join(prefix, entry.name);
    return entry.isDirectory()
      ? findCssFiles(path.join(directory, entry.name), relativePath)
      : relativePath.endsWith(".css")
        ? [relativePath]
        : [];
  });
}

const expectedFiles = manifest.map(([relativePath]) => relativePath).sort();
const actualFiles = findCssFiles(outputRoot)
  .filter((relativePath) => relativePath !== "index.css")
  .sort();
if (JSON.stringify(actualFiles) !== JSON.stringify(expectedFiles)) {
  throw new Error("ui/styles contains a missing or unlisted stylesheet.");
}

for (const [relativePath] of manifest) {
  const css = fs.readFileSync(path.join(outputRoot, relativePath), "utf8");
  const lines = css.split(/\r?\n/);
  if (lines.at(-1) === "") lines.pop();
  if (lines.length === 0 || lines.length > 400) {
    throw new Error(`${relativePath} has ${lines.length} lines; expected 1-400.`);
  }
}

const indexCss = fs.readFileSync(path.join(outputRoot, "index.css"), "utf8");
const layerOrder =
  "@layer reset, tokens, base, components, features, panel, themes, motion, chrome;";
if (!indexCss.includes(layerOrder)) {
  throw new Error("styles/index.css is missing the declared semantic layer order.");
}
const imports = [...indexCss.matchAll(/@import url\("([^"]+)"\) layer\(([^)]+)\);/g)].map(
  ([, href, layer]) => [href, layer],
);
const expectedImports = [
  ...manifest.map(([relativePath, layer]) => [`./${relativePath}`, layer]),
  ["../holo-buttons.css", "components"],
];
if (JSON.stringify(imports) !== JSON.stringify(expectedImports)) {
  throw new Error("styles/index.css import order has drifted from the CSS manifest.");
}

const html = fs.readFileSync(path.join(root, "ui", "index.html"), "utf8");
if (!html.includes('href="styles/index.css"')) {
  throw new Error("ui/index.html does not load styles/index.css.");
}
for (const legacyHref of ['href="style.css"', 'href="holo-buttons.css"']) {
  if (html.includes(legacyHref)) {
    throw new Error(`ui/index.html still loads ${legacyHref}.`);
  }
}

console.log(`UI CSS split verified: ${manifest.length} sheets, each at most 400 lines.`);
