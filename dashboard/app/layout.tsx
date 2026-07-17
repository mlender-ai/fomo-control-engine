import type { Metadata, Viewport } from "next";
import Script from "next/script";
import { TerminalShell } from "@/components/terminal";
import { Providers } from "./providers";
import "./globals.css";
import "./robinhood.css";

export const metadata: Metadata = {
  applicationName: "FOMO Control",
  title: "FOMO Control Engine",
  description: "Personal trading decision engine",
  appleWebApp: {
    capable: true,
    statusBarStyle: "black-translucent",
    title: "FOMO Control"
  },
  formatDetection: {
    telephone: false
  }
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  colorScheme: "dark",
  themeColor: "#000000"
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="ko" data-theme="dark">
      <head>
        <Script id="extension-error-guard" strategy="beforeInteractive">
          {`
            (function () {
              function isIgnoredExtensionError(value) {
                var message = String(value && (value.message || value.reason || value) || "");
                var stack = String(value && (value.stack || (value.error && value.error.stack)) || "");
                var filename = String(value && value.filename || "");
                var source = message + "\\n" + stack + "\\n" + filename;
                return (
                  source.indexOf("chrome-extension://nkbihfbeogaeaoehlefnkodbefgpgknn") !== -1 ||
                  message.indexOf("Failed to connect to MetaMask") !== -1 ||
                  message.indexOf("MetaMask extension not found") !== -1
                );
              }

              window.addEventListener("error", function (event) {
                if (!isIgnoredExtensionError(event)) return;
                event.preventDefault();
                event.stopImmediatePropagation();
              }, true);

              window.addEventListener("unhandledrejection", function (event) {
                if (!isIgnoredExtensionError(event.reason)) return;
                event.preventDefault();
                event.stopImmediatePropagation();
              }, true);
            })();
          `}
        </Script>
      </head>
      <body>
        <Providers>
          <TerminalShell>{children}</TerminalShell>
        </Providers>
      </body>
    </html>
  );
}
