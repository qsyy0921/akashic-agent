import { existsSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";
import { tmpdir } from "node:os";

const projectRoot = dirname(dirname(fileURLToPath(import.meta.url)));
const tailwind = join(projectRoot, "node_modules", "tailwindcss", "lib", "cli.js");
const input = resolve(projectRoot, "frontend/dashboard/src/design/plugin-preset.css");
const output = resolve(projectRoot, "frontend/dashboard/public/sdk/preset.css");
const configuredPluginHome = process.env.AKASHIC_PLUGIN_HOME;
if (configuredPluginHome !== undefined && !configuredPluginHome.trim()) {
  throw new Error("AKASHIC_PLUGIN_HOME 不能为空");
}
const sourceRoots = [
  resolve(projectRoot, "frontend/dashboard/src"),
  resolve(projectRoot, "plugins"),
  ...(configuredPluginHome
    ? [resolve(configuredPluginHome, "cache")]
    : []),
].filter(existsSync);

function sourceFiles(root) {
  return readdirSync(root, { withFileTypes: true }).flatMap((entry) => {
    const path = join(root, entry.name);
    if (entry.isDirectory()) return sourceFiles(path);
    return /\.(ts|tsx)$/.test(entry.name) ? [path] : [];
  });
}

if (!existsSync(tailwind)) {
  throw new Error(`找不到 Tailwind CLI: ${tailwind}`);
}

mkdirSync(dirname(output), { recursive: true });
const contentFile = join(tmpdir(), `akasic-plugin-preset-${process.pid}.tsx`);
writeFileSync(contentFile, sourceRoots.flatMap(sourceFiles).map((path) => readFileSync(path, "utf8")).join("\n"));
try {
  const args = [
    "-i", input,
    "-o", output,
    "-c", resolve(projectRoot, "frontend/dashboard/tailwind.config.ts"),
    "--content", contentFile,
  ];
  const result = spawnSync(process.execPath, [tailwind, ...args], {
    cwd: projectRoot,
    stdio: "inherit",
    shell: false,
  });
  if (result.status !== 0) {
    process.exit(result.status ?? 1);
  }
} finally {
  rmSync(contentFile, { force: true });
}
