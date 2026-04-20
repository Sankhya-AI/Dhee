import type { Metadata } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "Dhee Cognitive Debugger",
  description: "Inspectable operator console for Dhee belief state.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
