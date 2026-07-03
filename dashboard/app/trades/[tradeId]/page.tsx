import { TradeDetailShell } from "@/components/trade-history-shell";

export default async function TradeDetailPage({ params }: { params: Promise<{ tradeId: string }> }) {
  const { tradeId } = await params;
  return <TradeDetailShell tradeId={tradeId} />;
}
