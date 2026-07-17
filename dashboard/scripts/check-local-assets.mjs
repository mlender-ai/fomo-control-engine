const origin = process.env.FCE_FRONTEND_ORIGIN ?? "http://127.0.0.1:8876";
const productRoutes = [
  "/",
  "/scout",
  "/review",
  "/engine",
  "/journal",
  "/markets",
  "/performance",
  "/positions",
  "/research",
  "/settings",
  "/shadow",
  "/trades",
  "/validation",
  "/calibration"
];
const assetPaths = new Set();

for (const route of productRoutes) {
  const pageUrl = new URL(route, origin);
  const htmlResponse = await fetch(pageUrl);

  if (!htmlResponse.ok) {
    throw new Error(`Frontend returned ${htmlResponse.status}: ${pageUrl}`);
  }

  const html = await htmlResponse.text();
  for (const path of html.match(/\/_next\/static\/[^"' ]+\.(?:css|js)/g) ?? []) {
    assetPaths.add(path);
  }
}

if (assetPaths.size === 0) {
  throw new Error(`No Next.js assets were found across ${productRoutes.length} product routes`);
}

const results = await Promise.all(
  [...assetPaths].map(async (path) => {
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

console.log(
  `[asset check] ${productRoutes.length} routes and ${assetPaths.size} Next.js CSS/JS assets returned 2xx.`
);
