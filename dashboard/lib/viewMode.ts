"use client";

export type FceViewMode = "minimal" | "pro";

const STORAGE_KEY = "fce.viewMode.v1";

export function loadFceViewMode(): FceViewMode {
  if (typeof window === "undefined") return "minimal";
  try {
    const value = window.localStorage.getItem(STORAGE_KEY);
    return value === "pro" ? "pro" : "minimal";
  } catch {
    return "minimal";
  }
}

export function saveFceViewMode(mode: FceViewMode): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(STORAGE_KEY, mode);
  } catch {
    // localStorage 비활성 환경에서는 현재 세션 상태만 사용한다.
  }
}
