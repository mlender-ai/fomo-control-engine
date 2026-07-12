import { defineTheme } from "@astryxdesign/core/theme";
import { neutralTheme } from "@astryxdesign/theme-neutral/built";

export const fomoTerminalTheme = defineTheme({
  name: "fomo-terminal",
  extends: neutralTheme,
  radius: { base: 6, multiplier: 0.72 },
  typography: {
    scale: { base: 14, ratio: 1.15 },
    body: { family: "Inter", fallbacks: 'ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif' },
    heading: { family: "Inter", fallbacks: 'ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif', weight: "semibold" },
    code: { family: '"SF Mono"', fallbacks: 'Monaco, Consolas, "Liberation Mono", monospace' }
  },
  tokens: {
    "--color-accent": ["#00c805", "#00c805"],
    "--color-accent-muted": ["#00c80524", "#00c80524"],
    "--color-background-body": ["#000000", "#000000"],
    "--color-background-surface": ["#0b0b0b", "#0b0b0b"],
    "--color-background-card": ["#111111", "#111111"],
    "--color-background-muted": ["#181818", "#181818"],
    "--color-background-popover": ["#151515", "#151515"],
    "--color-border": ["#272727", "#272727"],
    "--color-border-emphasized": ["#3a3a3a", "#3a3a3a"],
    "--color-text-primary": ["#f5f5f5", "#f5f5f5"],
    "--color-text-secondary": ["#9b9b9b", "#9b9b9b"],
    "--color-text-disabled": ["#5b5b5b", "#5b5b5b"],
    "--color-text-accent": ["#00c805", "#00c805"],
    "--color-success": ["#00c805", "#00c805"],
    "--color-success-muted": ["#00c80524", "#00c80524"],
    "--color-error": ["#ff4d57", "#ff4d57"],
    "--color-error-muted": ["#ff4d5718", "#ff4d5718"],
    "--color-warning": ["#9b9b9b", "#9b9b9b"],
    "--color-warning-muted": ["#ffffff0b", "#ffffff0b"],
    "--color-background-blue": ["#121212", "#121212"],
    "--color-border-blue": ["#383838", "#383838"],
    "--color-text-blue": ["#b8b8b8", "#b8b8b8"],
    "--color-background-cyan": ["#121212", "#121212"],
    "--color-border-cyan": ["#383838", "#383838"],
    "--color-text-cyan": ["#b8b8b8", "#b8b8b8"],
    "--color-background-purple": ["#121212", "#121212"],
    "--color-border-purple": ["#383838", "#383838"],
    "--color-text-purple": ["#b8b8b8", "#b8b8b8"],
    "--color-background-green": ["#06240b", "#06240b"],
    "--color-border-green": ["#00c805", "#00c805"],
    "--color-text-green": ["#41e45a", "#41e45a"],
    "--color-background-red": ["#271014", "#271014"],
    "--color-border-red": ["#ff4d57", "#ff4d57"],
    "--color-text-red": ["#ff7a82", "#ff7a82"],
    "--shadow-low": "0 1px 0 rgba(255, 255, 255, 0.025)",
    "--shadow-med": "0 10px 30px rgba(0, 0, 0, 0.28)",
    "--radius-inner": "4px",
    "--radius-element": "6px",
    "--radius-container": "8px",
    "--radius-page": "8px"
  },
  components: {
    button: {
      base: { borderRadius: "6px", fontWeight: "650" },
      "size:sm": { minHeight: "30px" }
    },
    badge: {
      base: { borderRadius: "4px", fontWeight: "650" }
    },
    card: {
      base: { borderColor: "var(--color-border)", backgroundColor: "var(--color-background-card)" }
    }
  }
});
