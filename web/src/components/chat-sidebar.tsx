"use client";

import { Plus, MessageSquare, Zap } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import type { Conversation, Skill } from "@/lib/types";
import { cn } from "@/lib/utils";

interface ChatSidebarProps {
  conversations: Conversation[];
  currentConversationId: string | null;
  skills: Skill[];
  onNewChat: () => void;
  onSelectConversation: (id: string) => void;
}

export function ChatSidebar({
  conversations,
  currentConversationId,
  skills,
  onNewChat,
  onSelectConversation,
}: ChatSidebarProps) {
  return (
    <div className="flex h-full w-64 flex-col border-r bg-card">
      {/* Header */}
      <div className="flex items-center justify-between p-4">
        <h2 className="text-sm font-semibold">对话</h2>
        <Button variant="ghost" size="icon" className="h-8 w-8" onClick={onNewChat}>
          <Plus className="h-4 w-4" />
        </Button>
      </div>

      <Separator />

      {/* Conversation List */}
      <ScrollArea className="flex-1">
        <div className="space-y-1 p-2">
          {conversations.length === 0 && (
            <p className="px-3 py-8 text-center text-xs text-muted-foreground">
              开始新的对话吧
            </p>
          )}
          {conversations.map((conv) => (
            <button
              key={conv.id}
              onClick={() => onSelectConversation(conv.id)}
              className={cn(
                "flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm transition-colors",
                conv.id === currentConversationId
                  ? "bg-accent text-accent-foreground"
                  : "hover:bg-accent/50 text-muted-foreground"
              )}
            >
              <MessageSquare className="h-4 w-4 shrink-0" />
              <span className="truncate">{conv.title}</span>
            </button>
          ))}
        </div>
      </ScrollArea>

      <Separator />

      {/* Skills */}
      <div className="p-4">
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <Zap className="h-3 w-3" />
          <span>{skills.length} 个技能已加载</span>
        </div>
        <div className="mt-2 flex flex-wrap gap-1">
          {skills.slice(0, 5).map((skill) => (
            <span
              key={skill.name}
              className="inline-flex items-center rounded-full border border-indigo-500/30 bg-indigo-500/10 px-2 py-0.5 text-[10px] text-indigo-400"
            >
              {skill.name}
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}
