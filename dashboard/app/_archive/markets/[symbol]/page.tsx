import { TickerDetail } from "@/components/ticker-detail";

export default async function MarketTickerPage({ params }: { params: Promise<{ symbol: string }> }) {
  const { symbol } = await params;
  return <TickerDetail symbol={symbol.toUpperCase()} />;
}
