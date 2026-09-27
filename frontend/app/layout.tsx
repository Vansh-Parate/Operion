import type { Metadata } from "next";
import "./globals.css";
export const metadata: Metadata = {
  title: "Operion | Incident Console",
  description:
    "Evidence-grounded Kubernetes incident investigation, runbook retrieval, and human-gated remediation. Interactive controlled benchmark demo.",
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
