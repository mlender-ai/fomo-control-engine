import type { MetadataRoute } from "next";

export default function manifest(): MetadataRoute.Manifest {
  return {
    id: "/",
    name: "FOMO Control Engine",
    short_name: "FOMO Control",
    description: "Read-only position intelligence and paper engine monitoring",
    start_url: "/?source=pwa",
    scope: "/",
    display: "standalone",
    background_color: "#000000",
    theme_color: "#000000",
    orientation: "any",
    lang: "ko-KR",
    categories: ["finance", "productivity"],
    icons: [
      {
        src: "/pwa/icon-192",
        sizes: "192x192",
        type: "image/png",
        purpose: "maskable"
      },
      {
        src: "/pwa/icon-512",
        sizes: "512x512",
        type: "image/png",
        purpose: "maskable"
      }
    ]
  };
}
