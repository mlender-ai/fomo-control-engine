import type { Metadata } from "next";
import { TerminalShell } from "@/components/terminal";
import { Providers } from "./providers";
import "./globals.css";

export const metadata: Metadata = {
  title: "FOMO Control Engine",
  description: "Personal trading decision engine"
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="ko" data-theme="dark">
      <body>
        <Providers>
          <TerminalShell>{children}</TerminalShell>
        </Providers>
      </body>
    </html>
  );
}
