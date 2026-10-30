import { InterventionScreen } from "@/components/InterventionScreen";
import { INTERVENTION_KEYS } from "@/lib/intervention";

export const dynamicParams = false;
export function generateStaticParams() {
  return INTERVENTION_KEYS.map((fixture) => ({ fixture }));
}

/** An intervention campaign: the decision, the reveal, and held-out scores. ?trial=Exp81&split=loco|prequential|lofo */
export default async function Page({ params }: { params: Promise<{ fixture: string }> }) {
  const { fixture } = await params;
  return <InterventionScreen fixture={fixture} />;
}
