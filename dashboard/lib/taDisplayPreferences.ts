"use client";

import { useEffect, useState } from "react";

const STORAGE_KEY = "fce.showSecondaryTaRows.v1";
const EVENT_NAME = "fce:secondary-ta-rows";
const SECONDARY_MODULES = new Set(["derivatives", "indicators"]);

export function readSecondaryTaRows(): boolean {
  if (typeof window === "undefined") return false;
  return window.localStorage.getItem(STORAGE_KEY) === "true";
}

export function writeSecondaryTaRows(value: boolean): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(STORAGE_KEY, String(value));
  window.dispatchEvent(new CustomEvent(EVENT_NAME, { detail: value }));
}

export function useSecondaryTaRows(): boolean {
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    setVisible(readSecondaryTaRows());
    const onChange = (event: Event) => setVisible(Boolean((event as CustomEvent<boolean>).detail));
    window.addEventListener(EVENT_NAME, onChange);
    return () => window.removeEventListener(EVENT_NAME, onChange);
  }, []);
  return visible;
}

export function visibleTaRows<T extends { module: string }>(rows: T[], includeSecondary: boolean): T[] {
  return includeSecondary ? rows : rows.filter((row) => !SECONDARY_MODULES.has(row.module));
}
