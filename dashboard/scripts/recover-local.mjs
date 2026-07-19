import { closeSync, openSync } from "node:fs";
import { connect } from "node:net";
import { spawn, spawnSync } from "node:child_process";

const PORT = 8876;
const BASE_URL = `http://127.0.0.1:${PORT}`;
const LOG_PATH = "/tmp/fce-dashboard.log";
const START_TIMEOUT_MS = 45_000;

function listenerPids() {
  const result = spawnSync("lsof", ["-tiTCP:8876", "-sTCP:LISTEN"], { encoding: "utf8" });
  if (result.status !== 0 && !result.stdout.trim()) return [];
  return [...new Set(result.stdout.split(/\s+/).filter(Boolean).map(Number).filter(Number.isInteger))];
}

function commandFor(pid) {
  const result = spawnSync("ps", ["-p", String(pid), "-o", "command="], { encoding: "utf8" });
  return result.stdout.trim();
}

function isFceNextServer(command) {
  return /next-server|next\s+start|npm\s+run\s+start:(local|mobile)/i.test(command);
}

function portIsOpen() {
  return new Promise((resolve) => {
    const socket = connect({ host: "127.0.0.1", port: PORT });
    socket.setTimeout(500);
    socket.once("connect", () => {
      socket.destroy();
      resolve(true);
    });
    const closed = () => {
      socket.destroy();
      resolve(false);
    };
    socket.once("timeout", closed);
    socket.once("error", closed);
  });
}

async function waitUntil(predicate, timeoutMs, label) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await predicate()) return;
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(`${label} timeout (${timeoutMs}ms)`);
}

function run(command, args) {
  const result = spawnSync(command, args, { cwd: process.cwd(), stdio: "inherit" });
  if (result.status !== 0) throw new Error(`${command} ${args.join(" ")} failed (${result.status ?? "signal"})`);
}

async function main() {
  const pids = listenerPids();
  for (const pid of pids) {
    const command = commandFor(pid);
    if (!isFceNextServer(command)) {
      throw new Error(`port ${PORT} is owned by a non-FCE process; refusing to stop pid ${pid}: ${command || "unknown"}`);
    }
  }
  for (const pid of pids) process.kill(pid, "SIGTERM");
  if (pids.length) await waitUntil(async () => !(await portIsOpen()), 10_000, "server shutdown");

  run("npm", ["run", "build"]);

  const log = openSync(LOG_PATH, "a");
  const server = spawn("npm", ["run", "start:local"], {
    cwd: process.cwd(),
    detached: true,
    stdio: ["ignore", log, log]
  });
  closeSync(log);
  server.unref();

  try {
    await waitUntil(async () => {
      try {
        const response = await fetch(`${BASE_URL}/`, { redirect: "manual" });
        return response.status >= 200 && response.status < 400;
      } catch {
        return false;
      }
    }, START_TIMEOUT_MS, "dashboard startup");
    run("npm", ["run", "check:local-assets"]);
  } catch (error) {
    try {
      process.kill(-server.pid, "SIGTERM");
    } catch {
      // The child may have already exited. The original failure remains authoritative.
    }
    throw error;
  }

  console.log(`FCE dashboard recovered at ${BASE_URL} (pid ${server.pid}, log ${LOG_PATH})`);
}

main().catch((error) => {
  console.error(`FCE dashboard recovery failed: ${error instanceof Error ? error.message : String(error)}`);
  process.exitCode = 1;
});
