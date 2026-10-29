"use client";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { findingUrl, receiptHref } from "@/lib/share";
import { QrCode } from "./QrCode";
import "./Receipt.css";

/** "Share": a link to this finding, its QR code, and the printable receipt. */
export function Share({ campaignKey, findingId }: { campaignKey: string; findingId: string }) {
  const [open, setOpen] = useState(false);
  const [url, setUrl] = useState("");
  const [copied, setCopied] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => setUrl(findingUrl(campaignKey, findingId)), [campaignKey, findingId]);
  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", away); document.removeEventListener("keydown", esc); };
  }, [open]);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch {
      // Clipboard blocked (e.g. insecure context): the link stays selectable.
    }
  };

  return (
    <div className="share" ref={box}>
      <button type="button" className="btn btn-quiet" aria-expanded={open} onClick={() => setOpen((o) => !o)}>Share</button>
      {open && url && (
        <div className="share-pop" role="dialog" aria-label="Share this finding">
          <QrCode text={url} size={168} />
          <div className="share-side">
            <p className="share-title">Share this finding</p>
            <p className="share-lead">Anyone with the link sees the same evidence and checks.</p>
            <div className="share-link">
              <span className="share-url">{url}</span>
              <button type="button" className="cy-copy" onClick={copy}>{copied ? "Copied" : "Copy link"}</button>
            </div>
            <Link className="text-quiet share-receipt" href={receiptHref(campaignKey, findingId)}>Open the printable receipt</Link>
          </div>
        </div>
      )}
    </div>
  );
}
