import { TickerDetail } from "@/components/ticker-detail";

export default async function TickerPage({ params }: { params: Promise<{ symbol: string }> }) {
  const { symbol } = await params;
  return <TickerDetail symbol={symbol.toUpperCase()} />;
}
