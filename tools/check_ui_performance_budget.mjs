import fs from "node:fs";
import path from "node:path";
import process from "node:process";

const root = process.cwd();
const sourceRoot = path.join(root, "ui", "src");
const stylesRoot = path.join(root, "ui", "styles");
const budgets = Object.freeze({
  productionModules: 145,
  javascriptBytes: 800 * 1024,
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
  .filter(({ bytes }) => bytes > budgets.singleModuleBytes);

const failures = [];
if (modules.length > budgets.productionModules) {
  failures.push(
    `${modules.length} production modules exceed the ${budgets.productionModules}-module budget.`,
  );
}
if (javascriptBytes > budgets.javascriptBytes) {
  failures.push(
    `${javascriptBytes} JavaScript bytes exceed the ${budgets.javascriptBytes}-byte budget.`,
  );
}
if (stylesheetBytes > budgets.stylesheetBytes) {
  failures.push(
    `${stylesheetBytes} stylesheet bytes exceed the ${budgets.stylesheetBytes}-byte budget.`,
  );
}
if (htmlBytes > budgets.htmlBytes) {
  failures.push(`${htmlBytes} HTML bytes exceed the ${budgets.htmlBytes}-byte shell budget.`);
}
for (const { file, bytes } of oversizedModules) {
  failures.push(
    `${path.relative(root, file)} is ${bytes} bytes; the per-module budget is ` +
      `${budgets.singleModuleBytes} bytes.`,
  );
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
