export interface Message {
  id: string;
  role: "user" | "assistant" | "status" | "error";
  content: string;
  timestamp: number;
  skillResults?: SkillResult[];
}

export interface SkillResult {
  skill: string;
  success: boolean;
  summary?: string;
  error?: string;
}

export interface Conversation {
  id: string;
  title: string;
  lastMessage?: string;
  updatedAt: number;
}

export interface Skill {
  name: string;
  description: string;
  category: string;
  version: string;
  enabled: boolean;
}
