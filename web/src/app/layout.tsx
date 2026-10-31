import type { Metadata } from "next";
import { Inter } from "next/font/google";

import "./globals.css";

import { Providers } from "./providers";

// Downloaded at build time and self hosted; the browser never calls Google.
const inter = Inter({ subsets: ["latin"], variable: "--font-inter", display: "swap" });

export const metadata: Metadata = {
  title: "Harvester",
  description: "Discord 频道采集器",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="zh-CN" className={inter.variable}>
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
