import { CampaignScreen } from "@/components/CampaignScreen";
import { CAMPAIGN_KEYS } from "@/lib/campaigns";

export const dynamicParams = false;
export function generateStaticParams() {
  return CAMPAIGN_KEYS.map((campaign) => ({ campaign }));
}

export default async function Page({ params }: { params: Promise<{ campaign: string }> }) {
  const { campaign } = await params;
  return <CampaignScreen campaignKey={campaign} />;
}
