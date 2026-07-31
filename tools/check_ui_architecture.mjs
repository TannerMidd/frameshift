import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import process from "node:process";

const root = process.cwd();
const uiRoot = path.join(root, "ui");
const sourceRoot = path.join(uiRoot, "src");
const apiClientExceptions = new Set(["errors.js", "index.js", "query.js"]);
const forbiddenModuleNames = new Set(["legacy-base.js", "legacy-runtime.js"]);
const forbiddenTransitionComment =
  /\b(?:generated\s+mechanical|mechanical(?:ly)?\s+(?:esm\s+)?(?:carve|split|transition)|feature\s+ownership\s+cleanup\s+follows|transitional\s+legacy\s+feature)\b/iu;
// Lead bytes of a UTF-8 sequence re-read as latin-1. The bare-character class
// covers the C2/C3/E2/F0 ranges; the EF range needs its second byte too, so a
// legitimate i-diaeresis (naïve) is not mistaken for corruption. EF BC/BD are
// the fullwidth forms used by the ＋ buttons, EF BB is a stray BOM.
const forbiddenMojibake = /[ÂÃâð�]|ï[¼½»]/u;

/** @param {string} directory @param {string} suffix */
function walk(directory, suffix) {
  return fs.readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const target = path.join(directory, entry.name);
    if (entry.isDirectory()) return walk(target, suffix);
    return entry.isFile() && target.endsWith(suffix) ? [target] : [];
  });
}

/** @param {string} source */
function countLines(source) {
  if (!source) return 0;
  return source.replace(/\r?\n$/u, "").split(/\r?\n/u).length;
}

/** @param {string} source */
function comments(source) {
  return [...source.matchAll(/\/\*[\s\S]*?\*\/|\/\/[^\r\n]*/gu)].map((match) => match[0]);
}

/**
 * Extract balanced type expressions from JSDoc tags so prose containing the
 * word "any" cannot create a false positive.
 *
 * @param {string} source
 * @returns {string[]}
 */
function jsdocTypeExpressions(source) {
  const expressions = [];
  const tag =
    /@(?:arg|argument|enum|extends|implements|import|param|property|return|returns|satisfies|template|this|throws|type|typedef|yield|yields)\b/gu;
  for (const blockMatch of source.matchAll(/\/\*\*[\s\S]*?\*\//gu)) {
    const block = blockMatch[0];
    for (const tagMatch of block.matchAll(tag)) {
      const nextTag = block.indexOf("@", tagMatch.index + tagMatch[0].length);
      const open = block.indexOf("{", tagMatch.index + tagMatch[0].length);
      if (open < 0 || (nextTag >= 0 && open > nextTag)) continue;
      let depth = 0;
      for (let index = open; index < block.length; index += 1) {
        if (block[index] === "{") depth += 1;
        if (block[index] !== "}") continue;
        depth -= 1;
        if (depth === 0) {
          expressions.push(block.slice(open + 1, index));
          break;
        }
      }
    }
  }
  return expressions;
}

/** @param {string} source @returns {string[]} */
function promiseTypeExpressions(source) {
  const expressions = [];
  for (const match of source.matchAll(/\bPromise\s*</gu)) {
    const open = match.index + match[0].lastIndexOf("<");
    let depth = 0;
    for (let index = open; index < source.length; index += 1) {
      if (source[index] === "<") depth += 1;
      if (source[index] !== ">") continue;
      depth -= 1;
      if (depth === 0) {
        expressions.push(source.slice(open + 1, index).trim());
        break;
      }
    }
  }
  return expressions;
}

/** @param {string} expression */
function isGenericPromiseDto(expression) {
  return (
    /(?:^|[|&(<,])\s*\{/u.test(expression) ||
    /\bRecord\s*</u.test(expression) ||
    /(?:^|[|&(<,])\s*(?:any|unknown|object|Object|JsonObject|JsonValue)(?:\[\])?(?=\s*(?:[|>&),]|$))/u.test(
      expression,
    ) ||
    /\b(?:Array|ReadonlyArray)\s*<\s*(?:any|unknown|object|Object|JsonObject|JsonValue)\b/u.test(
      expression,
    )
  );
}

/** @param {string} sourceRelative */
function isDomainApiClient(sourceRelative) {
  if (!sourceRelative.startsWith("api/")) return false;
  const segments = sourceRelative.split("/");
  return segments.length === 2 && !apiClientExceptions.has(segments[1]);
}

/**
 * @param {string} uiRelative POSIX-style path relative to ui.
 * @param {string} source
 * @returns {string[]}
 */
export function uiTextPolicyFailures(uiRelative, source) {
  return forbiddenMojibake.test(source)
    ? [`ui/${uiRelative} contains literal mojibake instead of valid Unicode text.`]
    : [];
}

/** @param {string} value */
function escapeRegExp(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/gu, "\\$&");
}

/**
 * @param {string} sourceRelative POSIX-style path relative to ui/src.
 * @param {string} source
 * @param {number} [lineTotal]
 * @returns {string[]}
 */
export function modulePolicyFailures(sourceRelative, source, lineTotal = countLines(source)) {
  const moduleFailures = [];
  const relative = `ui/src/${sourceRelative}`;
  const sourceArea = sourceRelative.split("/")[0];
  const isApiConsumer = ["features", "shell", "bootstrap"].includes(sourceArea);
  const maximumLines = sourceArea === "data" ? 550 : 400;

  if (lineTotal > maximumLines) {
    moduleFailures.push(`${relative} has ${lineTotal} lines (maximum ${maximumLines}).`);
  }
  if (source.includes("@ts-nocheck")) {
    moduleFailures.push(`${relative} disables checkJs with @ts-nocheck.`);
  }
  if (forbiddenModuleNames.has(path.posix.basename(sourceRelative))) {
    moduleFailures.push(`${relative} is a forbidden legacy base/runtime module.`);
  }
  if (/\blegacy-(?:base|runtime)\.js\b/u.test(source)) {
    moduleFailures.push(`${relative} imports a forbidden legacy base/runtime module.`);
  }
  if (comments(source).some((comment) => forbiddenTransitionComment.test(comment))) {
    moduleFailures.push(`${relative} retains a generated mechanical-transition comment.`);
  }
  if (jsdocTypeExpressions(source).some((expression) => /\bany\b/u.test(expression))) {
    moduleFailures.push(`${relative} uses an explicit JSDoc any type.`);
  }

  if (isDomainApiClient(sourceRelative)) {
    const basename = path.posix.basename(sourceRelative, ".js");
    const contractName = basename === "system" ? "state" : basename;
    const contractSpecifier = `./contracts/${contractName}.js`;
    const contractImport = new RegExp(
      `\\b(?:from|import)\\s*["']${escapeRegExp(contractSpecifier)}["']`,
      "u",
    );
    if (!contractImport.test(source)) {
      moduleFailures.push(
        `${relative} does not import its explicit ${contractSpecifier} endpoint contract.`,
      );
    }
    const genericResponses = promiseTypeExpressions(source).filter(isGenericPromiseDto);
    if (genericResponses.length) {
      moduleFailures.push(
        `${relative} uses generic Promise response DTO${
          genericResponses.length === 1 ? "" : "s"
        }: ${genericResponses.join(", ")}.`,
      );
    }
  }

  if (sourceRelative !== "core/http.js" && /\bfetch\s*\(/u.test(source)) {
    moduleFailures.push(`${relative} bypasses the named API/HTTP seam.`);
  }
  if (isApiConsumer && /core\/http\.js/u.test(source)) {
    moduleFailures.push(`${relative} imports the transport instead of a named domain API client.`);
  }
  if (isApiConsumer && /["'`]\/api\//u.test(source)) {
    moduleFailures.push(
      `${relative} embeds an endpoint instead of using a named domain API client.`,
    );
  }
  if (
    sourceRelative !== "core/html.js" &&
    sourceRelative !== "shell/view.js" &&
    /\braw\s*\(/u.test(source)
  ) {
    moduleFailures.push(`${relative} uses the reviewed-static-markup escape hatch.`);
  }
  if (
    sourceRelative !== "core/html.js" &&
    /\.(?:innerHTML|outerHTML)\s*(?:\+?=)|\.insertAdjacentHTML\s*\(/u.test(source)
  ) {
    moduleFailures.push(`${relative} bypasses the safe HTML rendering seam.`);
  }
  if (
    sourceArea === "bootstrap" &&
    (/\baddEventListener\s*\(/u.test(source) ||
      /\b(?:requireById|getElementById|querySelector(?:All)?)\s*\(/u.test(source))
  ) {
    moduleFailures.push(
      `${relative} owns DOM wiring; bootstrap modules may only compose named initializers/loaders.`,
    );
  }
  return moduleFailures;
}

function runSelfTest() {
  const expectFailure = (name, sourceRelative, source, expected, lineTotal) => {
    const found = modulePolicyFailures(sourceRelative, source, lineTotal);
    assert.ok(
      found.some((failure) => failure.includes(expected)),
      `${name}: expected "${expected}", got ${JSON.stringify(found)}`,
    );
  };

  assert.deepEqual(modulePolicyFailures("features/clean.js", "export const clean = true;\n"), []);
  assert.deepEqual(
    uiTextPolicyFailures("src/features/clean.js", 'export const label = "Saving…";\n'),
    [],
  );
  assert.deepEqual(
    uiTextPolicyFailures("src/features/clean.js", 'export const label = "＋ CONDITION";\n'),
    [],
  );
  assert.deepEqual(
    uiTextPolicyFailures("src/features/clean.js", 'export const label = "naïve";\n'),
    [],
  );
  assert.deepEqual(
    modulePolicyFailures(
      "api/widgets.js",
      '/** @import {WidgetResponse} from "./contracts/widgets.js" */\n' +
        "/** @returns {Promise<WidgetResponse>} */\nexport function widget() {}\n",
    ),
    [],
  );
  assert.deepEqual(modulePolicyFailures("data/catalog.js", "export default [];\n", 550), []);

  expectFailure("nocheck", "features/bad.js", "// @ts-nocheck\n", "@ts-nocheck");
  expectFailure("legacy module", "core/legacy-runtime.js", "export {};\n", "forbidden legacy");
  expectFailure(
    "legacy import",
    "features/bad.js",
    'import { runtime } from "../core/legacy-runtime.js";\n',
    "forbidden legacy",
  );
  expectFailure(
    "transition comment",
    "features/bad.js",
    "/* Generated mechanical ESM carve. */\n",
    "mechanical-transition",
  );
  assert.deepEqual(
    uiTextPolicyFailures("src/features/bad.js", 'export const label = "Savingâ€¦";\n'),
    ["ui/src/features/bad.js contains literal mojibake instead of valid Unicode text."],
  );
  // The fullwidth ＋ mangled this way shipped undetected once; EF-range lead
  // bytes were the gap in the class above.
  assert.deepEqual(
    uiTextPolicyFailures("src/features/bad.js", 'export const label = "ï¼‹ CONDITION";\n'),
    ["ui/src/features/bad.js contains literal mojibake instead of valid Unicode text."],
  );
  expectFailure(
    "JSDoc any",
    "features/bad.js",
    "/** @param {() => any} callback */\n",
    "JSDoc any",
  );
  expectFailure(
    "JSDoc Record any",
    "features/bad.js",
    "/** @type {Record<string, any>} */\n",
    "JSDoc any",
  );
  expectFailure("module size", "features/bad.js", "export {};\n", "maximum 400", 401);
  expectFailure("data size", "data/catalog.js", "export {};\n", "maximum 550", 551);
  expectFailure(
    "missing API contract",
    "api/widgets.js",
    "/** @returns {Promise<WidgetResponse>} */\n",
    "endpoint contract",
  );
  expectFailure(
    "contract path in prose",
    "api/widgets.js",
    "// ./contracts/widgets.js\n/** @returns {Promise<WidgetResponse>} */\n",
    "endpoint contract",
  );
  for (const responseType of [
    "unknown",
    "any[]",
    "JsonObject",
    "Record<string, unknown>",
    "{ok: boolean}",
  ]) {
    expectFailure(
      `generic Promise ${responseType}`,
      "api/widgets.js",
      '/** @import {WidgetResponse} from "./contracts/widgets.js" */\n' +
        `/** @returns {Promise<${responseType}>} */\n`,
      "generic Promise",
    );
  }
  process.stdout.write("UI architecture policy self-test OK: 19 zero-tolerance cases.\n");
}

if (process.argv.includes("--self-test")) {
  runSelfTest();
} else {
  const failures = [];
  let domainApiClients = 0;
  const uiTextSources = [".js", ".html", ".css"].flatMap((suffix) => walk(uiRoot, suffix));
  for (const filename of uiTextSources) {
    const uiRelative = path.relative(uiRoot, filename).split(path.sep).join("/");
    const source = fs.readFileSync(filename, "utf8");
    failures.push(...uiTextPolicyFailures(uiRelative, source));
  }
  const productionJs = walk(sourceRoot, ".js").filter((file) => !file.endsWith(".test.js"));
  for (const filename of productionJs) {
    const sourceRelative = path.relative(sourceRoot, filename).split(path.sep).join("/");
    const source = fs.readFileSync(filename, "utf8");
    if (isDomainApiClient(sourceRelative)) domainApiClients += 1;
    failures.push(...modulePolicyFailures(sourceRelative, source));
  }
  const indexPath = path.join(root, "ui", "index.html");
  const index = fs.readFileSync(indexPath, "utf8");
  const indexLines = countLines(index);
  if (indexLines > 200) failures.push(`ui/index.html has ${indexLines} lines (maximum 200).`);
  if (!index.includes('<script type="module" src="src/main.js"></script>')) {
    failures.push("ui/index.html does not boot through the native ESM entrypoint.");
  }

  for (const obsolete of ["app.js", "style.css", "galaxy-data.js"]) {
    if (fs.existsSync(path.join(root, "ui", obsolete))) {
      failures.push(`Obsolete UI monolith still exists: ui/${obsolete}.`);
    }
  }

  const sourceCouplingPatterns = [/ui[/\\]app\.js/iu, /ui[/\\]style\.css/iu, /vm\.runInContext/iu];
  const sourceReadPatterns = [
    /\bread_text\s*\(/iu,
    /\breadFileSync\s*\(/iu,
    /\breadFile\s*\(/iu,
    /\bopen\s*\(/iu,
  ];
  const uiSourcePathPatterns = [/ui[/\\]src/iu, /["'`]ui["'`]\s*[,/]\s*["'`]src["'`]/iu];
  for (const filename of walk(path.join(root, "tests"), ".py").concat(
    walk(path.join(root, "tests"), ".cjs"),
    walk(path.join(root, "tests"), ".js"),
    walk(path.join(root, "tests"), ".mjs"),
  )) {
    const source = fs.readFileSync(filename, "utf8");
    if (sourceCouplingPatterns.some((pattern) => pattern.test(source))) {
      failures.push(`${path.relative(root, filename)} is coupled to obsolete UI source text.`);
    }
    if (
      sourceReadPatterns.some((pattern) => pattern.test(source)) &&
      uiSourcePathPatterns.some((pattern) => pattern.test(source))
    ) {
      failures.push(
        `${path.relative(root, filename)} reads ui/src implementation text instead of importing ` +
          "behavior through the module graph.",
      );
    }
  }

  if (failures.length) {
    process.stderr.write(`UI architecture check failed:\n  ${failures.join("\n  ")}\n`);
    process.exitCode = 1;
  } else {
    process.stdout.write(
      `UI architecture OK: ${productionJs.length} modules, ${domainApiClients} contracted API ` +
        `clients, ${indexLines}-line shell, zero forbidden constructs or seam bypasses.\n`,
    );
  }
}
