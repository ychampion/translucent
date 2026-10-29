"use client";
import QRCode from "qrcode";
import { useEffect, useState } from "react";

/** A QR code for a link, drawn in the browser as SVG (ink on white, so it scans from a screen, a projector or paper). */
export function QrCode({ text, size = 160, className }: { text: string; size?: number; className?: string }) {
  const [src, setSrc] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    QRCode.toString(text, { type: "svg", margin: 0, errorCorrectionLevel: "M", color: { dark: "#1c1f22", light: "#ffffff" } })
      .then((svg) => { if (live) setSrc(`data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`); })
      .catch(() => { if (live) setSrc(null); });
    return () => { live = false; };
  }, [text]);
  return (
    <div className={`qr ${className ?? ""}`} style={{ width: size, height: size }}>
      {/* eslint-disable-next-line @next/next/no-img-element */}
      {src && <img src={src} width={size} height={size} alt={`QR code for ${text}`} />}
    </div>
  );
}
