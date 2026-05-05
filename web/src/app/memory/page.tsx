"use client";

import { useState, useEffect } from "react";
import { BrainCircuit, Save, Plus, Trash2, Edit2, Loader2, AlertCircle } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle, CardFooter } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";

interface MemoryItem {
  key: string;
  value: string;
}

export default function MemoryPage() {
  const userId = "default_user";
  
  const [memories, setMemories] = useState<MemoryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Edit/Add modal state
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [isEditing, setIsEditing] = useState(false);
  const [editKey, setEditKey] = useState("");
  const [editValue, setEditValue] = useState("");

  const fetchMemory = async () => {
    try {
      const res = await fetch(`/api/memory/${userId}`);
      const data = await res.json();
      
      if (data.memory) {
        // Convert object to array of {key, value} for UI
        const memoryArray = Object.entries(data.memory).map(([key, value]) => ({
          key,
          value: String(value),
        }));
        setMemories(memoryArray);
      }
    } catch (err) {
      console.error("Failed to load memory:", err);
      setError("加载个人记忆失败，请检查后端服务。");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchMemory();
  }, []);

  const handleSave = async () => {
    if (!editKey.trim()) {
      setError("键名不能为空");
      return;
    }

    setSaving(true);
    setError(null);

    try {
      const res = await fetch("/api/memory/update", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          user_id: userId,
          key: editKey.trim(),
          value: editValue.trim(),
        }),
      });
      
      const data = await res.json();
      if (!data.success) {
        throw new Error(data.error || "保存失败");
      }
      
      await fetchMemory();
      setIsModalOpen(false);
      resetForm();
    } catch (err: any) {
      setError(err.message || "保存记忆失败");
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (key: string) => {
    setDeleting(key);
    try {
      const res = await fetch(`/api/memory/${userId}/${encodeURIComponent(key)}`, {
        method: "DELETE",
      });
      const data = await res.json();
      if (!data.success) {
        throw new Error(data.error || "删除失败");
      }
      await fetchMemory();
    } catch (err: any) {
      setError(err.message || "删除记忆失败");
    } finally {
      setDeleting(null);
    }
  };

  const openEdit = (item: MemoryItem) => {
    setIsEditing(true);
    setEditKey(item.key);
    setEditValue(item.value);
    setIsModalOpen(true);
  };

  const openAdd = () => {
    setIsEditing(false);
    resetForm();
    setIsModalOpen(true);
  };

  const resetForm = () => {
    setEditKey("");
    setEditValue("");
    setError(null);
  };

  // Predefined common preference keys to suggest
  const commonKeys = ["name", "location", "language", "dietary_preference", "coding_language"];

  return (
    <div className="flex h-full w-full flex-col">
      <header className="flex h-14 shrink-0 items-center justify-between border-b px-6">
        <div className="flex items-center gap-2">
          <BrainCircuit className="h-5 w-5 text-indigo-500" />
          <h1 className="font-semibold">个人记忆与偏好 (Memory)</h1>
        </div>
      </header>

      <div className="flex-1 overflow-auto p-6 bg-muted/20">
        <div className="mx-auto max-w-4xl space-y-6">
          <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <h2 className="text-2xl font-bold tracking-tight">全局上下文</h2>
              <p className="text-muted-foreground mt-1">
                在这里管理的偏好和记忆会对所有会话生效。AI 在回答时会优先参考这些设定。
              </p>
            </div>
            
            <Dialog open={isModalOpen} onOpenChange={(open: boolean) => {
              setIsModalOpen(open);
              if (!open) resetForm();
            }}>
              <DialogTrigger asChild>
                <Button onClick={openAdd} className="bg-indigo-600 hover:bg-indigo-700 text-white">
                  <Plus className="h-4 w-4 mr-2" />
                  添加偏好
                </Button>
              </DialogTrigger>
              <DialogContent className="sm:max-w-[425px]">
                <DialogHeader>
                  <DialogTitle>{isEditing ? "编辑记忆" : "添加新记忆"}</DialogTitle>
                  <DialogDescription>
                    设置键值对形式的偏好。例如：键为 "name"，值为 "张三"。
                  </DialogDescription>
                </DialogHeader>
                
                {error && (
                  <Alert variant="destructive" className="mt-2">
                    <AlertCircle className="h-4 w-4" />
                    <AlertTitle>错误</AlertTitle>
                    <AlertDescription>{error}</AlertDescription>
                  </Alert>
                )}

                <div className="grid gap-4 py-4">
                  <div className="space-y-2">
                    <label className="text-sm font-medium">键名 (Key)</label>
                    <Input 
                      placeholder="例如: location" 
                      value={editKey}
                      onChange={(e: React.ChangeEvent<HTMLInputElement>) => setEditKey(e.target.value)}
                      disabled={isEditing}
                    />
                    {!isEditing && (
                      <div className="flex flex-wrap gap-2 mt-2">
                        {commonKeys.map(k => (
                          <Badge 
                            key={k} 
                            variant="secondary" 
                            className="cursor-pointer hover:bg-indigo-100 hover:text-indigo-700"
                            onClick={() => setEditKey(k)}
                          >
                            {k}
                          </Badge>
                        ))}
                      </div>
                    )}
                  </div>
                  
                  <div className="space-y-2">
                    <label className="text-sm font-medium">内容 (Value)</label>
                    <Textarea 
                      placeholder="例如: 北京市朝阳区" 
                      className="min-h-[100px]"
                      value={editValue}
                      onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) => setEditValue(e.target.value)}
                    />
                  </div>
                </div>
                
                <DialogFooter>
                  <Button variant="outline" onClick={() => setIsModalOpen(false)} disabled={saving}>
                    取消
                  </Button>
                  <Button onClick={handleSave} disabled={saving} className="bg-indigo-600 hover:bg-indigo-700 text-white">
                    {saving ? (
                      <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                    ) : (
                      <Save className="mr-2 h-4 w-4" />
                    )}
                    保存
                  </Button>
                </DialogFooter>
              </DialogContent>
            </Dialog>
          </div>

          {loading ? (
            <div className="grid gap-4 sm:grid-cols-2">
              {[1, 2, 3, 4].map((i) => (
                <Card key={i} className="animate-pulse">
                  <CardHeader className="h-16 bg-muted/50 rounded-t-lg" />
                  <CardContent className="h-10" />
                </Card>
              ))}
            </div>
          ) : memories.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-16 text-center text-muted-foreground border-2 border-dashed rounded-lg bg-card/50">
              <BrainCircuit className="h-10 w-10 mb-4 opacity-20" />
              <h3 className="text-lg font-medium mb-1 text-foreground">没有找到任何记忆</h3>
              <p className="max-w-sm mb-6">
                您可以告诉 AI 您的称呼、所在地、饮食禁忌等，或者手动在此处添加。
              </p>
              <Button onClick={openAdd} variant="outline">
                <Plus className="h-4 w-4 mr-2" />
                手动添加
              </Button>
            </div>
          ) : (
            <div className="grid gap-4 sm:grid-cols-2">
              {memories.map((item) => (
                <Card key={item.key} className="flex flex-col overflow-hidden hover:border-indigo-500/30 transition-colors">
                  <CardHeader className="pb-3 bg-muted/30">
                    <div className="flex items-center justify-between">
                      <CardTitle className="text-sm font-mono font-medium text-indigo-500 flex items-center gap-2">
                        {item.key}
                      </CardTitle>
                      <div className="flex gap-1">
                        <Button 
                          variant="ghost" 
                          size="icon" 
                          className="h-7 w-7" 
                          onClick={() => openEdit(item)}
                        >
                          <Edit2 className="h-3.5 w-3.5" />
                        </Button>
                        <Button 
                          variant="ghost" 
                          size="icon" 
                          className="h-7 w-7 text-destructive hover:bg-destructive/10" 
                          onClick={() => handleDelete(item.key)}
                          disabled={deleting === item.key}
                        >
                          {deleting === item.key ? (
                            <Loader2 className="h-3.5 w-3.5 animate-spin" />
                          ) : (
                            <Trash2 className="h-3.5 w-3.5" />
                          )}
                        </Button>
                      </div>
                    </div>
                  </CardHeader>
                  <CardContent className="pt-4 flex-1">
                    <div className="text-sm whitespace-pre-wrap break-words text-foreground/90">
                      {item.value}
                    </div>
                  </CardContent>
                </Card>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
