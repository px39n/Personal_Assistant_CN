"use client";

import { useEffect, useRef, useState } from "react";
import { Bot, Menu, PanelLeftClose } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { ChatMessage } from "@/components/chat-message";
import { ChatInput } from "@/components/chat-input";
import { ChatSidebar } from "@/components/chat-sidebar";
import { useChatStore } from "@/lib/chat-store";
import type { Skill } from "@/lib/types";
import { cn } from "@/lib/utils";

export default function Home() {
  const {
    messages,
    isStreaming,
    conversationId,
    conversations,
    sendMessage,
    stopStreaming,
    newConversation,
    switchConversation,
  } = useChatStore();

  const [skills, setSkills] = useState<Skill[]>([]);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const scrollRef = useRef<HTMLDivElement>(null);

  // Load skills on mount
  useEffect(() => {
    fetch("/api/skills")
      .then((r) => r.json())
      .then((data) => {
        if (data.skills) setSkills(data.skills);
      })
      .catch(() => {});
  }, []);

  // Auto-scroll to bottom
  useEffect(() => {
    const el = scrollRef.current;
    if (el) {
      el.scrollTop = el.scrollHeight;
    }
  }, [messages]);

  return (
    <div className="flex h-screen">
      {/* Sidebar */}
      <div
        className={cn(
          "hidden transition-all duration-300 md:block",
          sidebarOpen ? "w-64" : "w-0 overflow-hidden"
        )}
      >
        <ChatSidebar
          conversations={conversations}
          currentConversationId={conversationId}
          skills={skills}
          onNewChat={newConversation}
          onSelectConversation={switchConversation}
        />
      </div>

      {/* Main Chat Area */}
      <div className="flex flex-1 flex-col">
        {/* Header */}
        <header className="flex items-center gap-3 border-b px-4 py-3">
          <Button
            variant="ghost"
            size="icon"
            className="h-8 w-8"
            onClick={() => setSidebarOpen(!sidebarOpen)}
          >
            {sidebarOpen ? (
              <PanelLeftClose className="h-4 w-4" />
            ) : (
              <Menu className="h-4 w-4" />
            )}
          </Button>

          <div className="flex items-center gap-2">
            <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-indigo-600">
              <Bot className="h-4 w-4 text-white" />
            </div>
            <h1 className="text-sm font-semibold">Personal Assistant CN</h1>
          </div>

          <div className="ml-auto flex items-center gap-2">
            <div className="h-2 w-2 rounded-full bg-green-500 animate-pulse" />
            <span className="text-xs text-muted-foreground">
              {skills.length} 个技能
            </span>
          </div>
        </header>

        {/* Messages */}
        <ScrollArea className="flex-1">
          <div ref={scrollRef} className="mx-auto max-w-3xl space-y-4 p-4">
            {messages.length === 0 && (
              <div className="flex flex-col items-center justify-center py-20 text-center">
                <div className="mb-4 flex h-16 w-16 items-center justify-center rounded-2xl bg-indigo-600/10">
                  <Bot className="h-8 w-8 text-indigo-500" />
                </div>
                <h2 className="text-lg font-semibold">你好！</h2>
                <p className="mt-1 max-w-sm text-sm text-muted-foreground">
                  我是你的个人 AI 助手。试试问我问题，或者让我帮你搜索信息、管理日程。
                </p>
                <div className="mt-6 flex flex-wrap justify-center gap-2">
                  {[
                    "今天天气怎么样？",
                    "帮我搜索最新的科技新闻",
                    "用Python写一个快速排序",
                  ].map((prompt) => (
                    <button
                      key={prompt}
                      onClick={() => sendMessage(prompt)}
                      className="rounded-full border bg-card px-4 py-2 text-xs transition-colors hover:bg-accent"
                    >
                      {prompt}
                    </button>
                  ))}
                </div>
              </div>
            )}
            {messages.map((msg) => (
              <ChatMessage key={msg.id} message={msg} />
            ))}
            {isStreaming &&
              !messages.some(
                (m) => m.role === "status" || m.role === "assistant"
              ) && (
                <div className="flex items-center gap-2 px-2 text-muted-foreground">
                  <div className="flex gap-1">
                    <span className="h-2 w-2 animate-bounce rounded-full bg-muted-foreground [animation-delay:0ms]" />
                    <span className="h-2 w-2 animate-bounce rounded-full bg-muted-foreground [animation-delay:150ms]" />
                    <span className="h-2 w-2 animate-bounce rounded-full bg-muted-foreground [animation-delay:300ms]" />
                  </div>
                </div>
              )}
          </div>
        </ScrollArea>

        {/* Input */}
        <ChatInput
          onSend={sendMessage}
          onStop={stopStreaming}
          isStreaming={isStreaming}
        />
      </div>
    </div>
  );
}
