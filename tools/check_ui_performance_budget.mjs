import fs from "node:fs";
import path from "node:path";
import process from "node:process";

const root = process.cwd();
const sourceRoot = path.join(root, "ui", "src");
const stylesRoot = path.join(root, "ui", "styles");
const softBudgets = Object.freeze({
  productionModules: 145,
  javascriptBytes: 800 * 1024,
});
const hardBudgets = Object.freeze({
  productionModules: 160,
  javascriptBytes: 900 * 1024,
  stylesheetBytes: 180 * 1024,
  htmlBytes: 4 * 1024,
  singleModuleBytes: 24 * 1024,
});

/** @param {string} directory @param {string} suffix */
function walk(directory, suffix) {
  return fs.readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const target = path.join(directory, entry.name);
    if (entry.isDirectory()) return walk(target, suffix);
    return entry.isFile() && target.endsWith(suffix) ? [target] : [];
  });
}

const modules = walk(sourceRoot, ".js").filter((file) => !file.endsWith(".test.js"));
const stylesheets = walk(stylesRoot, ".css");
const javascriptBytes = modules.reduce((total, file) => total + fs.statSync(file).size, 0);
const stylesheetBytes = stylesheets.reduce((total, file) => total + fs.statSync(file).size, 0);
const htmlBytes = fs.statSync(path.join(root, "ui", "index.html")).size;
const oversizedModules = modules
  .map((file) => ({ file, bytes: fs.statSync(file).size }))
  .filter(({ bytes }) => bytes > hardBudgets.singleModuleBytes);

const warnings = [];
const failures = [];
if (modules.length > hardBudgets.productionModules) {
  failures.push(
    `${modules.length} production modules exceed the ${hardBudgets.productionModules}-module hard cap.`,
  );
} else if (modules.length > softBudgets.productionModules) {
  warnings.push(
    `${modules.length} production modules exceed the ${softBudgets.productionModules}-module soft threshold; ` +
      `the hard cap is ${hardBudgets.productionModules}.`,
  );
}
if (javascriptBytes > hardBudgets.javascriptBytes) {
  failures.push(
    `${javascriptBytes} JavaScript bytes exceed the ${hardBudgets.javascriptBytes}-byte hard cap.`,
  );
} else if (javascriptBytes > softBudgets.javascriptBytes) {
  warnings.push(
    `${javascriptBytes} JavaScript bytes exceed the ${softBudgets.javascriptBytes}-byte soft threshold; ` +
      `the hard cap is ${hardBudgets.javascriptBytes} bytes.`,
  );
}
if (stylesheetBytes > hardBudgets.stylesheetBytes) {
  failures.push(
    `${stylesheetBytes} stylesheet bytes exceed the ${hardBudgets.stylesheetBytes}-byte hard cap.`,
  );
}
if (htmlBytes > hardBudgets.htmlBytes) {
  failures.push(`${htmlBytes} HTML bytes exceed the ${hardBudgets.htmlBytes}-byte shell hard cap.`);
}
for (const { file, bytes } of oversizedModules) {
  failures.push(
    `${path.relative(root, file)} is ${bytes} bytes; the per-module hard cap is ` +
      `${hardBudgets.singleModuleBytes} bytes.`,
  );
}

if (warnings.length) {
  process.stderr.write(`UI performance budget warning:\n  ${warnings.join("\n  ")}\n`);
}
if (failures.length) {
  process.stderr.write(`UI performance budget failed:\n  ${failures.join("\n  ")}\n`);
  process.exitCode = 1;
} else {
  process.stdout.write(
    `UI performance budget OK: ${modules.length} modules / ${javascriptBytes} JS bytes, ` +
      `${stylesheetBytes} CSS bytes, ${htmlBytes}-byte shell.\n`,
  );
}
