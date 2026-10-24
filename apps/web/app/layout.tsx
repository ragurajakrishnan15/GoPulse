import { Suspense } from "react";
import type { Metadata, Viewport } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import { SiteHeader } from "@/components/SiteHeader";
import "./globals.css";

const geistSans = Geist({ variable: "--font-geist-sans", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });

export const metadata: Metadata = {
  title: { default: "GoPulse - AI product analytics demo", template: "%s | GoPulse" },
  description: "Synthetic-data demo of an AI product data platform: activation, retention, agent reliability, pipeline health and data quality.",
};

export const viewport: Viewport = { width: "device-width", initialScale: 1 };

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className={`${geistSans.variable} ${geistMono.variable}`}>
      <body className="min-h-screen antialiased">
        <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded-md focus:bg-accent focus:px-3 focus:py-2 focus:text-accentfg">
          Skip to main content
        </a>
        <Suspense fallback={<div className="h-24 border-b border-line bg-surface" />}>
          <SiteHeader />
        </Suspense>
        <main id="main" className="mx-auto min-h-[85vh] max-w-7xl px-4 py-6">{children}</main>
        <footer className="mx-auto max-w-7xl px-4 pb-10 text-xs text-muted">
          <p>
            GoPulse is an independent portfolio project built on <strong>synthetic data</strong>. It is not affiliated with any company and uses no
            proprietary or customer data. Cost figures are estimates from documented assumptions, not vendor billing.
          </p>
          <p className="mt-2">
            Privacy: no cookies, no analytics. If you run the demo pipeline, a salted one-way hash of your network address is kept for up to one hour
            to enforce rate limits; no raw IP address is stored.
          </p>
        </footer>
      </body>
    </html>
  );
}
