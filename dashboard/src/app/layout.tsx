import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";
import { NeuralSidebar } from "@/components/layout/neural-sidebar";
import { TopBar } from "@/components/layout/top-bar";
import { InspectorWrapper } from "@/components/memory-inspector/inspector-panel";

const geistSans = Geist({ variable: "--font-geist-sans", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });

export const metadata: Metadata = {
  title: "Engram — Neural Memory",
  description: "Living neural memory visualizer",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="dark">
      <body className={`${geistSans.variable} ${geistMono.variable} antialiased`}>
        <div className="flex h-screen overflow-hidden" style={{ backgroundColor: '#050510' }}>
          <NeuralSidebar />
          <div className="flex flex-1 flex-col overflow-hidden">
            <TopBar />
            <main className="flex-1 overflow-y-auto" style={{ backgroundColor: '#050510' }}>
              {children}
            </main>
          </div>
          <InspectorWrapper />
        </div>
      </body>
    </html>
  );
}
