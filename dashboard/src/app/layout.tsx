import type { Metadata } from "next";
import { Inter, Roboto_Mono } from "next/font/google";
import "./globals.css";
import Sidebar from "@/components/Sidebar";

const inter = Inter({
  subsets: ["latin"],
  variable: "--font-inter",
  weight: ["400", "500", "600"],
});

const robotoMono = Roboto_Mono({
  subsets: ["latin"],
  variable: "--font-roboto-mono",
  weight: ["400", "500", "600"],
});

export const metadata: Metadata = {
  title: "Sentinel | Social Alpha",
  description: "Bloomberg Terminal for OSINT and Social Sentiment",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="dark">
      <body
        className={`${inter.variable} ${robotoMono.variable} font-sans antialiased bg-base text-text-primary`}
      >
        <div className="flex min-h-screen">
          <Sidebar />
          <main className="flex-1 ml-0 mb-14 md:ml-12 md:mb-0 min-h-screen">{children}</main>
        </div>
      </body>
    </html>
  );
}
