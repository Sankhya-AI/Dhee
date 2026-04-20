import { BeliefsWorkspace } from "@/components/beliefs/beliefs-workspace";

export default async function BeliefDetailPage({
  params,
}: {
  params: Promise<{ beliefId: string }>;
}) {
  const { beliefId } = await params;
  return <BeliefsWorkspace selectedBeliefId={beliefId} />;
}
