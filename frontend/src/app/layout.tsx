import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "AI Video Studio | Personalized Video Generation",
  description:
    "Generate personalized, cinematic 15-30s videos from your photos with AI vision, Wan 2.2 motion synthesis, and ACE-Step music.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="h-full bg-slate-950 text-slate-100 antialiased">
      <body className="min-h-full flex flex-col">{children}</body>
    </html>
  );
}
