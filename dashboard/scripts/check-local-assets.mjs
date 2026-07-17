const origin = process.env.FCE_FRONTEND_ORIGIN ?? "http://127.0.0.1:8876";
const htmlResponse = await fetch(origin);

if (!htmlResponse.ok) {
  throw new Error(`Frontend returned ${htmlResponse.status}: ${origin}`);
}

const html = await htmlResponse.text();
const assetPaths = [...new Set(html.match(/\/_next\/static\/[^"' ]+\.(?:css|js)/g) ?? [])];

if (assetPaths.length === 0) {
  throw new Error(`No Next.js assets were found in ${origin}`);
}

const results = await Promise.all(
  assetPaths.map(async (path) => {
    const response = await fetch(new URL(path, origin));
    return { path, status: response.status };
  })
);
const failed = results.filter((result) => result.status < 200 || result.status >= 300);

if (failed.length > 0) {
  for (const result of failed) {
    console.error(`[asset failed] ${result.status} ${result.path}`);
  }
  process.exit(1);
}

console.log(`[asset check] ${assetPaths.length} Next.js CSS/JS assets returned 2xx.`);
