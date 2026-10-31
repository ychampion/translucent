import type { Metadata, Viewport } from "next";
import { Shell } from "@/components/Shell";
import { REDUCED_MOTION_SCRIPT } from "@/lib/motion";
import { THEME_SCRIPT } from "@/lib/theme";
import "./globals.css";
import "./chat.css";
import "./pages.css";
import "./intervention.css";
import "./loop.css";

export const metadata: Metadata = {
  title: "Translucent",
  description: "Investigate failed tests, choose and run the next experiment, and judge whether a change helps.",
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
