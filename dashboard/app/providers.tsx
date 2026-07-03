"use client";

import Link from "next/link";
import { LinkProvider } from "@astryxdesign/core/Link";
import { Theme } from "@astryxdesign/core/theme";
import { fomoTerminalTheme } from "@/lib/theme/fomoTerminalTheme";

export function Providers({ children }: { children: React.ReactNode }) {
  return (
    <Theme theme={fomoTerminalTheme} mode="dark">
      <LinkProvider component={Link}>{children}</LinkProvider>
    </Theme>
  );
}
