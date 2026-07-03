import { ResearchDetail } from "@/components/research-detail";

export default async function ResearchRunPage({ params }: { params: Promise<{ runId: string }> }) {
  const { runId } = await params;
  return <ResearchDetail runId={runId} />;
}
