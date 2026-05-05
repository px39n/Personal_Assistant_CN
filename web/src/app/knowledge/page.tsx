"use client";

import { useState, useEffect, useRef } from "react";
import { BookOpen, Upload, FileText, Trash2, Search, Loader2, Database, AlertCircle } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle, CardFooter } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Textarea } from "@/components/ui/textarea";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";

interface Document {
  doc_id: string;
  title: string;
  chunk_count: number;
  metadata: any;
  created_at?: string;
}

export default function KnowledgePage() {
  const [documents, setDocuments] = useState<Document[]>([]);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [stats, setStats] = useState({ count: 0, total_chunks: 0 });

  // Upload modal state
  const [isUploadOpen, setIsUploadOpen] = useState(false);
  const [uploadType, setUploadType] = useState<"file" | "text">("file");
  const [uploadTitle, setUploadTitle] = useState("");
  const [uploadText, setUploadText] = useState("");
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const fetchDocuments = async () => {
    try {
      const res = await fetch("/api/documents/");
      const data = await res.json();
      setDocuments(data.documents || []);
      setStats({ count: data.count || 0, total_chunks: data.total_chunks || 0 });
    } catch (err) {
      console.error("Failed to load documents:", err);
      setError("加载知识库文档失败，请检查后端服务是否正常运行。");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchDocuments();
  }, []);

  const handleUpload = async () => {
    if (uploadType === "file" && !uploadFile) {
      setError("请选择要上传的文件");
      return;
    }
    if (uploadType === "text" && !uploadText.trim()) {
      setError("请输入文档内容");
      return;
    }

    setUploading(true);
    setError(null);

    const formData = new FormData();
    if (uploadTitle) {
      formData.append("title", uploadTitle);
    }

    if (uploadType === "file" && uploadFile) {
      formData.append("file", uploadFile);
    } else if (uploadType === "text") {
      formData.append("text", uploadText);
    }

    try {
      const res = await fetch("/api/documents/upload", {
        method: "POST",
        body: formData,
      });
      
      const data = await res.json();
      if (data.error) {
        throw new Error(data.error);
      }
      
      await fetchDocuments();
      setIsUploadOpen(false);
      resetUploadForm();
    } catch (err: any) {
      setError(err.message || "上传文档失败");
    } finally {
      setUploading(false);
    }
  };

  const handleDelete = async (docId: string) => {
    setDeleting(docId);
    try {
      const res = await fetch(`/api/documents/${docId}`, {
        method: "DELETE",
      });
      const data = await res.json();
      if (data.error) {
        throw new Error(data.error);
      }
      await fetchDocuments();
    } catch (err: any) {
      setError(err.message || "删除文档失败");
    } finally {
      setDeleting(null);
    }
  };

  const resetUploadForm = () => {
    setUploadTitle("");
    setUploadText("");
    setUploadFile(null);
    if (fileInputRef.current) {
      fileInputRef.current.value = "";
    }
    setError(null);
  };

  const filteredDocs = documents.filter(
    (doc) => doc.title.toLowerCase().includes(searchQuery.toLowerCase())
  );

  return (
    <div className="flex h-full w-full flex-col">
      <header className="flex h-14 shrink-0 items-center justify-between border-b px-6">
        <div className="flex items-center gap-2">
          <BookOpen className="h-5 w-5 text-indigo-500" />
          <h1 className="font-semibold">知识库 (RAG)</h1>
        </div>
        <div className="flex items-center gap-4">
          <div className="text-sm text-muted-foreground flex items-center gap-2">
            <Database className="h-4 w-4 text-blue-500" />
            <span>共 {stats.count} 篇文档 ({stats.total_chunks} 个片段)</span>
          </div>
        </div>
      </header>

      <div className="flex-1 overflow-auto p-6 bg-muted/20">
        <div className="mx-auto max-w-5xl space-y-6">
          <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <h2 className="text-2xl font-bold tracking-tight">私人向量知识库</h2>
              <p className="text-muted-foreground mt-1">
                上传的文档将被自动切分并向量化。在对话时，AI 会根据问题自动检索相关内容进行回答。
              </p>
            </div>
            
            <div className="flex items-center gap-2 w-full sm:w-auto">
              <div className="relative flex-1 sm:w-64">
                <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
                <Input
                  placeholder="搜索文档标题..."
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  className="pl-9 bg-background"
                />
              </div>
              
              <Dialog open={isUploadOpen} onOpenChange={(open) => {
                setIsUploadOpen(open);
                if (!open) resetUploadForm();
              }}>
                <DialogTrigger asChild>
                  <Button className="shrink-0 bg-indigo-600 hover:bg-indigo-700 text-white">
                    <Upload className="h-4 w-4 mr-2" />
                    上传文档
                  </Button>
                </DialogTrigger>
                <DialogContent className="sm:max-w-[500px]">
                  <DialogHeader>
                    <DialogTitle>添加新文档到知识库</DialogTitle>
                    <DialogDescription>
                      支持上传 TXT/MD 等纯文本文件，或直接输入长文本。文档将立即被向量化。
                    </DialogDescription>
                  </DialogHeader>
                  
                  {error && (
                    <Alert variant="destructive" className="mt-2">
                      <AlertCircle className="h-4 w-4" />
                      <AlertTitle>错误</AlertTitle>
                      <AlertDescription>{error}</AlertDescription>
                    </Alert>
                  )}

                  <Tabs defaultValue="file" value={uploadType} onValueChange={(v) => setUploadType(v as "file" | "text")} className="mt-4">
                    <TabsList className="grid w-full grid-cols-2">
                      <TabsTrigger value="file">上传文件</TabsTrigger>
                      <TabsTrigger value="text">输入文本</TabsTrigger>
                    </TabsList>
                    
                    <div className="mt-4 space-y-4">
                      <div className="space-y-2">
                        <label className="text-sm font-medium">文档标题 (可选)</label>
                        <Input 
                          placeholder="如果为空，将使用文件名或默认标题" 
                          value={uploadTitle}
                          onChange={(e) => setUploadTitle(e.target.value)}
                        />
                      </div>

                      <TabsContent value="file" className="space-y-2 mt-0">
                        <label className="text-sm font-medium">选择文件</label>
                        <Input 
                          type="file" 
                          ref={fileInputRef}
                          onChange={(e) => setUploadFile(e.target.files?.[0] || null)}
                          accept=".txt,.md,.csv,.json,.py,.js,.ts"
                          className="cursor-pointer"
                        />
                        <p className="text-xs text-muted-foreground mt-1">目前主要支持纯文本类文件 (txt, md, 代码等)，请确保文件为 UTF-8 编码。</p>
                      </TabsContent>
                      
                      <TabsContent value="text" className="space-y-2 mt-0">
                        <label className="text-sm font-medium">文档内容</label>
                        <Textarea 
                          placeholder="在此粘贴文章内容、知识点或资料..." 
                          className="min-h-[200px]"
                          value={uploadText}
                          onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) => setUploadText(e.target.value)}
                        />
                      </TabsContent>
                    </div>
                  </Tabs>
                  
                  <DialogFooter className="mt-6">
                    <Button variant="outline" onClick={() => setIsUploadOpen(false)} disabled={uploading}>
                      取消
                    </Button>
                    <Button onClick={handleUpload} disabled={uploading} className="bg-indigo-600 hover:bg-indigo-700 text-white">
                      {uploading ? (
                        <>
                          <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                          正在处理...
                        </>
                      ) : (
                        "开始处理并添加"
                      )}
                    </Button>
                  </DialogFooter>
                </DialogContent>
              </Dialog>
            </div>
          </div>

          {loading ? (
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {[1, 2, 3].map((i) => (
                <Card key={i} className="animate-pulse">
                  <CardHeader className="h-20 bg-muted rounded-t-lg" />
                  <CardContent className="h-16" />
                </Card>
              ))}
            </div>
          ) : filteredDocs.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-16 text-center text-muted-foreground border-2 border-dashed rounded-lg bg-card/50">
              <Database className="h-10 w-10 mb-4 opacity-20" />
              <h3 className="text-lg font-medium mb-1 text-foreground">知识库是空的</h3>
              <p className="max-w-sm mb-6">
                上传您的个人笔记、文章、代码片段等资料，AI 助手在回答时会自动引用这些内容作为背景知识。
              </p>
              <Button onClick={() => setIsUploadOpen(true)} className="bg-indigo-600 hover:bg-indigo-700 text-white">
                <Upload className="h-4 w-4 mr-2" />
                上传第一篇文档
              </Button>
            </div>
          ) : (
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {filteredDocs.map((doc) => (
                <Card key={doc.doc_id} className="flex flex-col hover:border-indigo-500/50 transition-colors">
                  <CardHeader className="pb-3 flex flex-row items-start justify-between space-y-0">
                    <div className="space-y-1">
                      <CardTitle className="text-base font-medium line-clamp-1 flex items-center gap-2" title={doc.title}>
                        <FileText className="h-4 w-4 text-blue-500 shrink-0" />
                        {doc.title}
                      </CardTitle>
                      <CardDescription className="text-xs font-mono">
                        ID: {doc.doc_id.substring(0, 8)}...
                      </CardDescription>
                    </div>
                  </CardHeader>
                  <CardContent className="pb-3 flex-1">
                    <div className="flex items-center gap-4 text-sm text-muted-foreground">
                      <div className="flex items-center gap-1.5 bg-muted px-2 py-1 rounded-md">
                        <Database className="h-3.5 w-3.5" />
                        <span>{doc.chunk_count} 个分块</span>
                      </div>
                      {doc.metadata?.source && (
                        <div className="flex items-center gap-1.5">
                          <Badge variant="outline" className="text-xs font-normal">
                            {doc.metadata.source}
                          </Badge>
                        </div>
                      )}
                    </div>
                  </CardContent>
                  <CardFooter className="pt-3 border-t bg-muted/20 flex justify-between items-center">
                    <div className="text-xs text-muted-foreground">
                      {doc.created_at ? new Date(doc.created_at).toLocaleString() : "刚刚"}
                    </div>
                    <Button 
                      variant="ghost" 
                      size="sm" 
                      className="h-8 w-8 p-0 text-destructive hover:text-destructive hover:bg-destructive/10"
                      onClick={() => handleDelete(doc.doc_id)}
                      disabled={deleting === doc.doc_id}
                      title="删除文档"
                    >
                      {deleting === doc.doc_id ? (
                        <Loader2 className="h-4 w-4 animate-spin" />
                      ) : (
                        <Trash2 className="h-4 w-4" />
                      )}
                    </Button>
                  </CardFooter>
                </Card>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
