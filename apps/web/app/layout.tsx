import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Switchboard",
  description: "The AI Operator Platform for Regulated Launches",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
