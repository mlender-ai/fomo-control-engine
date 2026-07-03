import { defineTheme } from "@astryxdesign/core/theme";
import { neutralTheme } from "@astryxdesign/theme-neutral/built";

export const fomoTerminalTheme = defineTheme({
  name: "fomo-terminal",
  extends: neutralTheme,
  radius: { base: 4, multiplier: 0.55 },
  typography: {
    scale: { base: 13, ratio: 1.16 },
    body: { family: "Inter", fallbacks: 'ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif' },
    heading: { family: "Inter", fallbacks: 'ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif', weight: "semibold" },
    code: { family: '"SF Mono"', fallbacks: 'Monaco, Consolas, "Liberation Mono", monospace' }
  },
  tokens: {
    "--color-accent": ["#a66700", "#f0b840"],
    "--color-accent-muted": ["#f0b84033", "#f0b84028"],
    "--color-background-body": ["#0b0d10", "#0b0d10"],
    "--color-background-surface": ["#111419", "#111419"],
    "--color-background-card": ["#151922", "#151922"],
    "--color-background-muted": ["#1a1f29", "#1a1f29"],
    "--color-background-popover": ["#171c25", "#171c25"],
    "--color-border": ["#2a303c", "#2a303c"],
    "--color-border-emphasized": ["#3c4656", "#3c4656"],
    "--color-text-primary": ["#edf0f4", "#edf0f4"],
    "--color-text-secondary": ["#a6afbd", "#a6afbd"],
    "--color-text-disabled": ["#5e6877", "#5e6877"],
    "--color-text-accent": ["#f0b840", "#f0b840"],
    "--color-success": ["#58b678", "#58b678"],
    "--color-success-muted": ["#58b6782c", "#58b6782c"],
    "--color-error": ["#df6f70", "#df6f70"],
    "--color-error-muted": ["#df6f702e", "#df6f702e"],
    "--color-warning": ["#f0b840", "#f0b840"],
    "--color-warning-muted": ["#f0b8402b", "#f0b8402b"],
    "--color-background-blue": ["#15314a", "#15314a"],
    "--color-border-blue": ["#3889bd", "#3889bd"],
    "--color-text-blue": ["#9fd7ff", "#9fd7ff"],
    "--color-background-cyan": ["#0f3640", "#0f3640"],
    "--color-border-cyan": ["#35a9c4", "#35a9c4"],
    "--color-text-cyan": ["#a3ecf5", "#a3ecf5"],
    "--color-background-purple": ["#28213d", "#28213d"],
    "--color-border-purple": ["#8b78d7", "#8b78d7"],
    "--color-text-purple": ["#c4b8ff", "#c4b8ff"],
    "--color-background-green": ["#123321", "#123321"],
    "--color-border-green": ["#56b577", "#56b577"],
    "--color-text-green": ["#a8ebba", "#a8ebba"],
    "--color-background-red": ["#3a1c21", "#3a1c21"],
    "--color-border-red": ["#df6f70", "#df6f70"],
    "--color-text-red": ["#ffb7b9", "#ffb7b9"],
    "--shadow-low": "0 1px 0 rgba(255, 255, 255, 0.03), 0 10px 24px rgba(0, 0, 0, 0.2)",
    "--shadow-med": "0 1px 0 rgba(255, 255, 255, 0.04), 0 18px 36px rgba(0, 0, 0, 0.28)",
    "--radius-inner": "3px",
    "--radius-element": "5px",
    "--radius-container": "6px",
    "--radius-page": "8px"
  },
  components: {
    button: {
      base: { borderRadius: "5px", fontWeight: "650" },
      "size:sm": { minHeight: "26px" }
    },
    badge: {
      base: { borderRadius: "4px", fontWeight: "650" }
    },
    card: {
      base: { borderColor: "var(--color-border)", backgroundColor: "var(--color-background-card)" }
    }
  }
});
