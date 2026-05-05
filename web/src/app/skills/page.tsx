"use client";

import { useState, useEffect } from "react";
import { Zap, Activity, Info, ShieldCheck, PowerOff } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle, CardFooter } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import { Input } from "@/components/ui/input";
import type { Skill } from "@/lib/types";

export default function SkillsPage() {
  const [skills, setSkills] = useState<Skill[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState("");

  useEffect(() => {
    fetch("/api/skills")
      .then((res) => res.json())
      .then((data) => {
        setSkills(data.skills || []);
      })
      .catch((err) => console.error("Failed to load skills:", err))
      .finally(() => setLoading(false));
  }, []);

  const filteredSkills = skills.filter(
    (skill) =>
      skill.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
      skill.description.toLowerCase().includes(searchQuery.toLowerCase()) ||
      skill.category.toLowerCase().includes(searchQuery.toLowerCase())
  );

  const categories = Array.from(new Set(skills.map((s) => s.category)));

  return (
    <div className="flex h-full w-full flex-col">
      <header className="flex h-14 shrink-0 items-center justify-between border-b px-6">
        <div className="flex items-center gap-2">
          <Zap className="h-5 w-5 text-indigo-500" />
          <h1 className="font-semibold">技能中心 (Skills)</h1>
        </div>
        <div className="flex items-center gap-4">
          <div className="text-sm text-muted-foreground flex items-center gap-2">
            <Activity className="h-4 w-4 text-green-500" />
            <span>已加载 {skills.length} 个核心技能</span>
          </div>
        </div>
      </header>

      <div className="flex-1 overflow-auto p-6 bg-muted/20">
        <div className="mx-auto max-w-5xl space-y-6">
          <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <h2 className="text-2xl font-bold tracking-tight">Agent 技能引擎</h2>
              <p className="text-muted-foreground mt-1">
                查看和管理 AI 助手当前具备的功能模块。模型会根据您的对话上下文自动调度这些技能。
              </p>
            </div>
            <div className="w-full sm:w-72">
              <Input
                placeholder="搜索技能名称或描述..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="bg-background"
              />
            </div>
          </div>

          {loading ? (
            <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
              {[1, 2, 3, 4, 5].map((i) => (
                <Card key={i} className="animate-pulse">
                  <CardHeader className="h-24 bg-muted rounded-t-lg" />
                  <CardContent className="h-20" />
                </Card>
              ))}
            </div>
          ) : (
            <div className="space-y-8">
              {categories.map((category) => {
                const categorySkills = filteredSkills.filter((s) => s.category === category);
                if (categorySkills.length === 0) return null;

                return (
                  <div key={category} className="space-y-4">
                    <h3 className="text-lg font-semibold flex items-center gap-2 capitalize">
                      <Badge variant="outline" className="bg-indigo-500/10 text-indigo-500 hover:bg-indigo-500/20">
                        {category}
                      </Badge>
                      <span className="text-muted-foreground text-sm font-normal">
                        ({categorySkills.length})
                      </span>
                    </h3>
                    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                      {categorySkills.map((skill) => (
                        <Card key={skill.name} className="flex flex-col overflow-hidden transition-all hover:shadow-md border-border/50">
                          <CardHeader className="pb-3">
                            <div className="flex items-start justify-between">
                              <CardTitle className="text-base font-medium font-mono">
                                {skill.name}
                              </CardTitle>
                              <Switch checked={skill.enabled} disabled />
                            </div>
                            <CardDescription className="text-xs mt-1">
                              v{skill.version}
                            </CardDescription>
                          </CardHeader>
                          <CardContent className="flex-1 text-sm text-muted-foreground leading-relaxed">
                            {skill.description}
                          </CardContent>
                          <CardFooter className="bg-muted/30 pt-3 pb-3 border-t text-xs flex justify-between">
                            <div className="flex items-center gap-1.5 text-muted-foreground">
                              <ShieldCheck className="h-3.5 w-3.5" />
                              <span>系统内置</span>
                            </div>
                            <Badge variant="secondary" className="font-mono text-[10px]">
                              @auto-route
                            </Badge>
                          </CardFooter>
                        </Card>
                      ))}
                    </div>
                  </div>
                );
              })}

              {filteredSkills.length === 0 && (
                <div className="flex flex-col items-center justify-center py-12 text-center text-muted-foreground border-2 border-dashed rounded-lg">
                  <Info className="h-8 w-8 mb-3 opacity-50" />
                  <p>没有找到匹配的技能</p>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
