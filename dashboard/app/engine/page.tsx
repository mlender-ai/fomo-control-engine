import { Suspense } from "react";
import { EngineTradingShell } from "@/components/engine-trading-shell";

export default function EngineTradingPage() {
  return <Suspense fallback={null}><EngineTradingShell /></Suspense>;
}
