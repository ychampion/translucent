import { CampaignList } from "@/components/CampaignList";

/** The server's campaigns, one ledger at a time: /campaigns/?ledger=<lid>. */
export default function Page() {
  return <CampaignList />;
}
