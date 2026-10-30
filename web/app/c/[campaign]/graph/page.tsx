import { GraphScreen } from "@/components/GraphScreen";
import { CAMPAIGN_KEYS } from "@/lib/campaigns";

export const dynamicParams = false;
export function generateStaticParams() {
  return CAMPAIGN_KEYS.map((campaign) => ({ campaign }));
}

/** A campaign's whole evidence graph; ?focus=<claim id> lights one finding's path. */
export default async function Page({ params }: { params: Promise<{ campaign: string }> }) {
  const { campaign } = await params;
  return <GraphScreen campaignKey={campaign} />;
}
