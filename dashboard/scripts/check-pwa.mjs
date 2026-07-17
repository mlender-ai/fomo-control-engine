const origin = process.env.FCE_FRONTEND_ORIGIN ?? "http://127.0.0.1:8876";

const manifestResponse = await fetch(new URL("/manifest.webmanifest", origin));
if (!manifestResponse.ok) {
  throw new Error(`Manifest returned ${manifestResponse.status}`);
}

const manifest = await manifestResponse.json();
if (manifest.display !== "standalone") {
  throw new Error(`Manifest display must be standalone, got ${manifest.display}`);
}

const requiredSizes = new Set(["192x192", "512x512"]);
for (const icon of manifest.icons ?? []) {
  requiredSizes.delete(icon.sizes);
  const response = await fetch(new URL(icon.src, origin));
  if (!response.ok || !response.headers.get("content-type")?.startsWith("image/png")) {
    throw new Error(`PWA icon is unavailable: ${icon.src}`);
  }
}
if (requiredSizes.size > 0) {
  throw new Error(`Manifest is missing icons: ${[...requiredSizes].join(", ")}`);
}

const apiResponse = await fetch(new URL("/api/system/status", origin));
if (!apiResponse.ok) {
  throw new Error(`Same-origin API proxy returned ${apiResponse.status}`);
}

console.log("[pwa check] manifest, 192/512 icons, and same-origin API proxy are ready.");
