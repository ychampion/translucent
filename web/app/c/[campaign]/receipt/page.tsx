import { ReceiptScreen } from "@/components/ReceiptScreen";
import { CAMPAIGN_KEYS } from "@/lib/campaigns";

export const dynamicParams = false;
export function generateStaticParams() {
  return CAMPAIGN_KEYS.map((campaign) => ({ campaign }));
}

/** A printable receipt for one finding, chosen by ?id= like the finding page. */
export default async function Page({ params }: { params: Promise<{ campaign: string }> }) {
  const { campaign } = await params;
  return <ReceiptScreen campaignKey={campaign} />;
}
