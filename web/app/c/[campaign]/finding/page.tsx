import { FindingScreen } from "@/components/FindingScreen";
import { CAMPAIGN_KEYS } from "@/lib/campaigns";

export const dynamicParams = false;
export function generateStaticParams() {
  return CAMPAIGN_KEYS.map((campaign) => ({ campaign }));
}

/** One page per campaign; the finding is chosen by ?id=, so any run's findings (replay or live) open here. */
export default async function Page({ params }: { params: Promise<{ campaign: string }> }) {
  const { campaign } = await params;
  return <FindingScreen campaignKey={campaign} />;
}
