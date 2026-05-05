"use client";

import { useCallback, useRef, useState } from "react";
import type { Conversation, Message, SkillResult } from "./types";

function generateId() {
  return Math.random().toString(36).slice(2, 10) + Date.now().toString(36);
}

export function useChatStore() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const abortRef = useRef<AbortController | null>(null);

  const addMessage = useCallback((msg: Message) => {
    setMessages((prev) => [...prev, msg]);
  }, []);

  const sendMessage = useCallback(
    async (text: string) => {
      if (!text.trim() || isStreaming) return;

      const userMsg: Message = {
        id: generateId(),
        role: "user",
        content: text,
        timestamp: Date.now(),
      };
      addMessage(userMsg);
      setIsStreaming(true);

      const controller = new AbortController();
      abortRef.current = controller;

      let assistantContent = "";
      let assistantId = generateId();
      let skillResults: SkillResult[] = [];
      let newConvId = conversationId;

      try {
        const response = await fetch("/api/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            message: text,
            conversation_id: conversationId,
            user_id: "web_user",
            stream: true,
          }),
          signal: controller.signal,
        });

        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }

        const reader = response.body?.getReader();
        if (!reader) throw new Error("No response body");

        const decoder = new TextDecoder();
        let buffer = "";
        let currentEventType = "";

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split("\n");
          buffer = lines.pop() || "";

          for (const line of lines) {
            if (line.startsWith("event: ")) {
              currentEventType = line.slice(7).trim();
            } else if (line.startsWith("data: ") && currentEventType) {
              const data = line.slice(6);

              switch (currentEventType) {
                case "metadata": {
                  try {
                    const meta = JSON.parse(data);
                    if (meta.conversation_id) {
                      newConvId = meta.conversation_id;
                      setConversationId(newConvId);
                    }
                  } catch {}
                  break;
                }
                case "status": {
                  setMessages((prev) => [
                    ...prev.filter((m) => m.role !== "status"),
                    {
                      id: generateId(),
                      role: "status",
                      content: data,
                      timestamp: Date.now(),
                    },
                  ]);
                  break;
                }
                case "message": {
                  assistantContent += data;
                  setMessages((prev) => {
                    const existing = prev.find((m) => m.id === assistantId);
                    if (existing) {
                      return prev.map((m) =>
                        m.id === assistantId
                          ? { ...m, content: assistantContent }
                          : m
                      );
                    }
                    return [
                      ...prev.filter((m) => m.role !== "status"),
                      {
                        id: assistantId,
                        role: "assistant",
                        content: assistantContent,
                        timestamp: Date.now(),
                      },
                    ];
                  });
                  break;
                }
                case "skill_result": {
                  try {
                    const sr = JSON.parse(data) as SkillResult;
                    skillResults.push(sr);
                  } catch {}
                  break;
                }
                case "error": {
                  setMessages((prev) => [
                    ...prev.filter((m) => m.role !== "status"),
                    {
                      id: generateId(),
                      role: "error",
                      content: data,
                      timestamp: Date.now(),
                    },
                  ]);
                  break;
                }
                case "done": {
                  // Attach skill results to the assistant message
                  if (skillResults.length > 0) {
                    setMessages((prev) =>
                      prev.map((m) =>
                        m.id === assistantId
                          ? { ...m, skillResults }
                          : m
                      )
                    );
                  }
                  // Remove lingering status messages
                  setMessages((prev) =>
                    prev.filter((m) => m.role !== "status")
                  );
                  break;
                }
              }
              currentEventType = "";
            }
          }
        }
      } catch (err) {
        if ((err as Error).name !== "AbortError") {
          setMessages((prev) => [
            ...prev.filter((m) => m.role !== "status"),
            {
              id: generateId(),
              role: "error",
              content: `连接错误: ${(err as Error).message}`,
              timestamp: Date.now(),
            },
          ]);
        }
      } finally {
        setIsStreaming(false);
        abortRef.current = null;

        // Update conversation list
        if (newConvId) {
          setConversations((prev) => {
            const title =
              text.length > 30 ? text.slice(0, 30) + "..." : text;
            const existing = prev.find((c) => c.id === newConvId);
            if (existing) {
              return prev.map((c) =>
                c.id === newConvId
                  ? { ...c, lastMessage: text, updatedAt: Date.now() }
                  : c
              );
            }
            return [
              { id: newConvId!, title, lastMessage: text, updatedAt: Date.now() },
              ...prev,
            ];
          });
        }
      }
    },
    [isStreaming, conversationId, addMessage]
  );

  const stopStreaming = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  const newConversation = useCallback(() => {
    setMessages([]);
    setConversationId(null);
  }, []);

  const switchConversation = useCallback(async (id: string) => {
    setConversationId(id);
    setMessages([]);
    try {
      const res = await fetch(`/api/conversations/${id}/history`);
      const data = await res.json();
      if (data.messages && Array.isArray(data.messages)) {
        const restored: Message[] = data.messages.map(
          (m: { role: string; content: string }, i: number) => ({
            id: `hist-${i}`,
            role: m.role === "user" ? "user" : "assistant",
            content: m.content,
            timestamp: Date.now() - (data.messages.length - i) * 1000,
          })
        );
        setMessages(restored);
      }
    } catch {
      // History not available
    }
  }, []);

  return {
    messages,
    isStreaming,
    conversationId,
    conversations,
    sendMessage,
    stopStreaming,
    newConversation,
    switchConversation,
  };
}
