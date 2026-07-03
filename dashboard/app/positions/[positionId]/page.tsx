import { PositionDetailShell } from "@/components/live-position-cockpit";

export default async function PositionDetailPage({ params }: { params: Promise<{ positionId: string }> }) {
  const { positionId } = await params;
  return <PositionDetailShell positionId={positionId} />;
}
