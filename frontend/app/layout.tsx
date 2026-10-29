import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "HR Interview Scoring",
  description: "Internal HR interview scoring system",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
