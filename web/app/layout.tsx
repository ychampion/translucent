import type { Metadata, Viewport } from "next";
import { Shell, THEME_SCRIPT } from "@/components/Shell";
import { REDUCED_MOTION_SCRIPT } from "@/lib/motion";
import "./globals.css";
import "./pages.css";
import "./intervention.css";

export const metadata: Metadata = {
  title: "Translucent",
  description: "Autonomous materials research you can check, frame by frame.",
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: dark)", color: "#161718" },
    { media: "(prefers-color-scheme: light)", color: "#fbfbfa" },
  ],
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_SCRIPT + REDUCED_MOTION_SCRIPT }} />
      </head>
      <body>
        <Shell>{children}</Shell>
      </body>
    </html>
  );
}
