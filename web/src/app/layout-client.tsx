"use client";

import { MainSidebar } from "@/components/layout/main-sidebar";

export default function ClientLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="flex h-screen w-full bg-background overflow-hidden">
      <MainSidebar />
      <main className="flex-1 overflow-auto">{children}</main>
    </div>
  );
}
