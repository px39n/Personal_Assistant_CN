"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Bot, User, AlertCircle, Loader2, Wrench } from "lucide-react";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import type { Message } from "@/lib/types";
import { cn } from "@/lib/utils";

interface ChatMessageProps {
  message: Message;
}

export function ChatMessage({ message }: ChatMessageProps) {
  if (message.role === "status") {
    return (
      <div className="flex items-center justify-center gap-2 py-2 text-xs text-muted-foreground">
        <Loader2 className="h-3 w-3 animate-spin" />
        <span>{message.content}</span>
      </div>
    );
  }

  if (message.role === "error") {
    return (
      <div className="mx-auto max-w-md rounded-lg border border-destructive/50 bg-destructive/10 px-4 py-3 text-sm text-destructive">
        <div className="flex items-center gap-2">
          <AlertCircle className="h-4 w-4 shrink-0" />
          <span>{message.content}</span>
        </div>
      </div>
    );
  }

  const isUser = message.role === "user";

  return (
    <div
      className={cn(
        "flex gap-3 px-2",
        isUser ? "flex-row-reverse" : "flex-row"
      )}
    >
      <Avatar
        className={cn(
          "h-8 w-8 shrink-0",
          isUser
            ? "bg-primary text-primary-foreground"
            : "bg-indigo-600 text-white"
        )}
      >
        <AvatarFallback
          className={
            isUser
              ? "bg-primary text-primary-foreground"
              : "bg-indigo-600 text-white"
          }
        >
          {isUser ? <User className="h-4 w-4" /> : <Bot className="h-4 w-4" />}
        </AvatarFallback>
      </Avatar>

      <div
        className={cn(
          "max-w-[75%] space-y-2",
          isUser ? "items-end" : "items-start"
        )}
      >
        <div
          className={cn(
            "rounded-2xl px-4 py-2.5 text-sm leading-relaxed",
            isUser
              ? "bg-primary text-primary-foreground rounded-br-md whitespace-pre-wrap"
              : "bg-card border rounded-bl-md prose prose-sm prose-invert max-w-none prose-p:my-1 prose-pre:my-2 prose-ul:my-1 prose-ol:my-1 prose-li:my-0.5 prose-headings:my-2 prose-code:text-indigo-300 prose-pre:bg-black/40 prose-pre:rounded-lg"
          )}
        >
          {isUser ? (
            message.content
          ) : (
            <ReactMarkdown remarkPlugins={[remarkGfm]}>
              {message.content}
            </ReactMarkdown>
          )}
        </div>

        {message.skillResults && message.skillResults.length > 0 && (
          <div className="space-y-1.5">
            {message.skillResults.map((sr, i) => (
              <div
                key={i}
                className="flex items-start gap-2 rounded-lg border bg-card/50 px-3 py-2 text-xs"
              >
                <Wrench className="h-3.5 w-3.5 mt-0.5 shrink-0 text-indigo-400" />
                <div>
                  <span className="font-medium text-indigo-400">
                    {sr.skill}
                  </span>
                  <span className="ml-1.5">
                    {sr.success ? "✅" : "❌"}
                  </span>
                  {sr.success && sr.summary && (
                    <p className="mt-0.5 text-muted-foreground">
                      {sr.summary.slice(0, 200)}
                    </p>
                  )}
                  {!sr.success && sr.error && (
                    <p className="mt-0.5 text-destructive">{sr.error}</p>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
