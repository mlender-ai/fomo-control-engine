# Astryx Integration

## Packages

The dashboard uses Astryx v0.1.x:

```bash
cd dashboard
npm install @astryxdesign/core @astryxdesign/theme-neutral
npm install -D @astryxdesign/cli
```

Package script:

```bash
npm run astryx -- component --list --detail brief
```

## CSS

`dashboard/app/globals.css` imports Astryx reset, core CSS, and neutral theme before Tailwind:

```css
@import "@astryxdesign/core/reset.css";
@import "@astryxdesign/core/astryx.css";
@import "@astryxdesign/theme-neutral/theme.css";

@tailwind base;
@tailwind components;
@tailwind utilities;
```

The project stays on Tailwind v3, so the Tailwind v4 bridge is intentionally not imported.

## Theme Provider

`dashboard/app/providers.tsx` wraps the app with:

- `Theme` from `@astryxdesign/core/theme`
- `LinkProvider` from `@astryxdesign/core/Link`
- local `fomoTerminalTheme`

`dashboard/lib/theme/fomoTerminalTheme.ts` extends Astryx neutral theme and defines the terminal color/radius/typography token overrides.

## Components In Use

- `AppShell`
- `TopNav`
- `SideNav`, `SideNavSection`, `SideNavItem`
- `CommandPalette`
- `Button`
- `Badge`
- `StatusDot`
- `Kbd`
- `Table`

Local wrappers live in `dashboard/components/terminal/` so page code stays product-specific and can change without scattering Astryx API details across every screen.

## License Boundary

Astryx is MIT licensed. The implementation uses published npm packages and documented component APIs. Reference projects are used for product ideas only; no Bloomberg, FinceptTerminal, TradingAgents, Vibe-Trading, or AutoHedge source code or branded UI is copied into this dashboard.
