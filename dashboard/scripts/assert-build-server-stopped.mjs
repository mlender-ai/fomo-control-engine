import net from "node:net";

const host = process.env.FCE_FRONTEND_HOST ?? "127.0.0.1";
const port = Number(process.env.FCE_FRONTEND_PORT ?? "8876");
const distDir = process.env.FCE_NEXT_DIST_DIR ?? ".next";

function isPortOpen() {
  return new Promise((resolve) => {
    const socket = net.createConnection({ host, port });
    let settled = false;

    const finish = (open) => {
      if (settled) return;
      settled = true;
      socket.destroy();
      resolve(open);
    };

    socket.once("connect", () => finish(true));
    socket.once("error", () => finish(false));
    socket.setTimeout(300, () => finish(false));
  });
}

if (distDir === ".next" && (await isPortOpen())) {
  console.error(
    `[build blocked] http://${host}:${port} is still serving the previous Next build. ` +
      "Stop that server before building, then restart it with `npm run start:local`."
  );
  process.exit(1);
}
