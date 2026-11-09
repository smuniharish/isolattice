import { createHash } from "node:crypto";
import { existsSync, promises as fs } from "node:fs";
import { basename, dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { execSync, spawn } from "node:child_process";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const sourceDirectory = join(root, "diagrams");
const outputDirectory = join(root, "docs", "assets", "diagrams");
const configPath = join(sourceDirectory, "mermaid-config.json");
const manifestPath = join(outputDirectory, "manifest.json");
const checkOnly = process.argv.includes("--check");
// Diagrams are rendered with exactly this Mermaid CLI version, installed globally;
// the manifest records it.
const requiredMermaidVersion = "12.2.0";
// Puppeteer's install script downloads the headless browser the CLI renders with.
const installCommand = `npm install --global --allow-scripts=puppeteer @mermaid-js/mermaid-cli@${requiredMermaidVersion}`;
const renderOptions = { scale: 2, background: "white" };

async function listMermaidFiles(directory) {
  const entries = await fs.readdir(directory, { withFileTypes: true });
  const files = await Promise.all(
    entries.map(async (entry) => {
      const entryPath = join(directory, entry.name);
      if (entry.isDirectory()) return listMermaidFiles(entryPath);
      return entry.isFile() && entry.name.endsWith(".mmd") ? [entryPath] : [];
    }),
  );
  return files.flat().sort((left, right) => left.localeCompare(right, "en"));
}

async function installedCli() {
  let globalModules;
  try {
    globalModules = execSync("npm root --global", { encoding: "utf8" }).trim();
  } catch {
    throw new Error(`npm was not found. Install Node.js, then run \`${installCommand}\`.`);
  }
  const cliPackage = join(globalModules, "@mermaid-js", "mermaid-cli");
  const cli = join(cliPackage, "src", "cli.js");
  if (!existsSync(cli)) {
    throw new Error(`Mermaid CLI is not installed. Run \`${installCommand}\`.`);
  }
  const installed = JSON.parse(await fs.readFile(join(cliPackage, "package.json"), "utf8")).version;
  if (installed !== requiredMermaidVersion) {
    throw new Error(
      `Mermaid CLI ${requiredMermaidVersion} is required; found ${installed}. Run \`${installCommand}\`.`,
    );
  }
  return cli;
}

function render(cli, source, output) {
  return new Promise((resolveRender, reject) => {
    const child = spawn(
      process.execPath,
      [
        cli,
        "-i",
        source,
        "-o",
        output,
        "-c",
        configPath,
        "-b",
        renderOptions.background,
        "-s",
        String(renderOptions.scale),
      ],
      { cwd: root, shell: false, stdio: "inherit" },
    );
    child.on("error", reject);
    child.on("close", (code) => {
      if (code === 0) resolveRender();
      else reject(new Error(`Mermaid rendering failed for ${relative(root, source)} (exit ${code}).`));
    });
  });
}

async function checksum(path) {
  // Hash with LF line endings so CRLF and LF checkouts of the same sources agree.
  const text = (await fs.readFile(path, "utf8")).replace(/\r\n/g, "\n");
  return createHash("sha256").update(text).digest("hex");
}

const files = await listMermaidFiles(sourceDirectory);
if (files.length === 0) throw new Error("No Mermaid sources found in diagrams.");
await fs.mkdir(outputDirectory, { recursive: true });
const configChecksum = await checksum(configPath);
const expectedManifest = {
  mermaidVersion: requiredMermaidVersion,
  renderOptions,
  configChecksum,
  sources: Object.fromEntries(
    await Promise.all(
      files.map(async (source) => [relative(sourceDirectory, source), await checksum(source)]),
    ),
  ),
};
const expectedImages = new Set(files.map((source) => `${basename(source, ".mmd")}.png`));
const orphanImages = (await fs.readdir(outputDirectory)).filter(
  (name) => name.endsWith(".png") && !expectedImages.has(name),
);

if (checkOnly) {
  if (!existsSync(manifestPath)) {
    throw new Error("Missing diagram manifest. Render diagrams first.");
  }
  const actualManifest = JSON.parse(await fs.readFile(manifestPath, "utf8"));
  if (JSON.stringify(actualManifest) !== JSON.stringify(expectedManifest)) {
    throw new Error("Diagram sources, configuration, or renderer version are stale.");
  }
  if (orphanImages.length > 0) {
    throw new Error(`Rendered diagrams without a source: ${orphanImages.join(", ")}`);
  }
} else {
  await Promise.all(orphanImages.map((name) => fs.rm(join(outputDirectory, name))));
}

const cli = checkOnly ? null : await installedCli();
for (const source of files) {
  const output = join(outputDirectory, `${basename(source, ".mmd")}.png`);
  if (checkOnly) {
    if (!existsSync(output)) throw new Error(`Missing rendered diagram: ${relative(root, output)}`);
  } else {
    await render(cli, source, output);
  }
}

if (!checkOnly) {
  await fs.writeFile(manifestPath, `${JSON.stringify(expectedManifest, null, 2)}\n`);
}
