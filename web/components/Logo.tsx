import { useId } from "react";

/** The mark: a lens over a test signal. The pulse is faint outside the lens and sharp through it. */
export function LogoMark({ size = 18, className = "" }: { size?: number; className?: string }) {
  const clip = useId();
  const trace = "M1 15h6.2V8.5h5.2V15h3.4v-3.2H23";
  return (
    <svg className={`logo-mark ${className}`} width={size} height={size} viewBox="0 0 24 24" aria-hidden>
      <defs><clipPath id={clip}><circle cx="12" cy="12" r="8.4" /></clipPath></defs>
      <path className="lm-faint" d={trace} />
      <circle className="lm-glass" cx="12" cy="12" r="8.4" />
      <path className="lm-sharp" d={trace} clipPath={`url(#${clip})`} />
      <circle className="lm-ring" cx="12" cy="12" r="8.4" />
    </svg>
  );
}
