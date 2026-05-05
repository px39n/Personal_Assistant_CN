"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Bot, MessageSquare, BookOpen, Zap, BrainCircuit } from "lucide-react";
import { cn } from "@/lib/utils";

export function MainSidebar() {
  const pathname = usePathname();

  const navItems = [
    { href: "/chat", label: "对话", icon: MessageSquare },
    { href: "/knowledge", label: "知识库", icon: BookOpen },
    { href: "/skills", label: "技能中心", icon: Zap },
    { href: "/memory", label: "个人记忆", icon: BrainCircuit },
  ];

  return (
    <div className="flex h-screen w-16 flex-col items-center border-r bg-card py-4 sm:w-64 sm:items-stretch sm:px-4">
      <div className="mb-8 flex items-center gap-3 justify-center sm:justify-start">
        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-indigo-600 shrink-0">
          <Bot className="h-5 w-5 text-white" />
        </div>
        <h1 className="hidden text-sm font-bold sm:block">Assistant CN</h1>
      </div>

      <nav className="flex flex-col gap-2 w-full">
        {navItems.map((item) => {
          const isActive = pathname.startsWith(item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              className={cn(
                "flex items-center gap-3 rounded-lg p-3 text-sm font-medium transition-colors",
                isActive
                  ? "bg-indigo-600/10 text-indigo-500"
                  : "text-muted-foreground hover:bg-accent hover:text-accent-foreground"
              )}
            >
              <item.icon className="h-5 w-5 shrink-0" />
              <span className="hidden sm:block">{item.label}</span>
            </Link>
          );
        })}
      </nav>
    </div>
  );
}
