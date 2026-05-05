"""内存向量存储 — 文档分块 + 向量检索。MVP 阶段纯内存，后续迁移到 pgvector。"""

import hashlib
import math
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from loguru import logger

from app.engine.llm import LLMClient, get_llm_client


@dataclass
class DocumentChunk:
    """文档块"""
    chunk_id: str
    doc_id: str
    content: str
    metadata: dict = field(default_factory=dict)  # 来源、页码等
    embedding: list[float] = field(default_factory=list)


@dataclass
class Document:
    """文档"""
    doc_id: str
    title: str
    content: str
    chunks: list[DocumentChunk] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


def chunk_text(
    text: str,
    chunk_size: int = 500,
    overlap: int = 50,
) -> list[str]:
    """
    将文本分块。按段落优先切分，超长段落按 chunk_size 切分。

    Args:
        text: 原始文本
        chunk_size: 每块最大字符数
        overlap: 块间重叠字符数
    """
    # 先按段落拆分
    paragraphs = re.split(r"\n{2,}", text.strip())

    chunks = []
    current_chunk = ""

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        # 如果当前段落加上现有内容不超限，合并
        if len(current_chunk) + len(para) + 1 <= chunk_size:
            current_chunk = f"{current_chunk}\n{para}" if current_chunk else para
        else:
            # 先保存现有块
            if current_chunk:
                chunks.append(current_chunk)

            # 如果单段落超长，按字符数切分
            if len(para) > chunk_size:
                for i in range(0, len(para), chunk_size - overlap):
                    chunks.append(para[i:i + chunk_size])
                current_chunk = ""
            else:
                current_chunk = para

    if current_chunk:
        chunks.append(current_chunk)

    return chunks


def _simple_tokenize(text: str) -> list[str]:
    """简单分词（中英文混合）"""
    # 英文按空格，中文按字
    tokens = []
    for word in re.findall(r"[\u4e00-\u9fff]|[a-zA-Z0-9]+", text.lower()):
        tokens.append(word)
    return tokens


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """余弦相似度"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _keyword_similarity(text1: str, text2: str) -> float:
    """基于关键词重叠的简单相似度（BM25 简化版，作为 embedding 的回退方案）"""
    tokens1 = set(_simple_tokenize(text1))
    tokens2 = set(_simple_tokenize(text2))
    if not tokens1 or not tokens2:
        return 0.0
    intersection = tokens1 & tokens2
    return len(intersection) / math.sqrt(len(tokens1) * len(tokens2))


class MemoryVectorStore:
    """
    内存向量存储。

    支持两种检索模式:
    1. 向量检索（需要 embedding API）— 语义匹配
    2. 关键词检索（回退方案）— 词频匹配
    """

    def __init__(self):
        self._documents: dict[str, Document] = {}  # doc_id -> Document
        self._chunks: list[DocumentChunk] = []      # 所有块的平坦列表
        self._use_embeddings: bool = True           # 是否使用 embedding API

    async def add_document(
        self,
        title: str,
        content: str,
        metadata: Optional[dict] = None,
        chunk_size: int = 500,
        overlap: int = 50,
    ) -> Document:
        """添加文档：分块 → 向量化 → 存储"""
        doc_id = hashlib.md5(content.encode()).hexdigest()[:12]

        if doc_id in self._documents:
            logger.info(f"文档已存在: {title} ({doc_id})")
            return self._documents[doc_id]

        # 分块
        text_chunks = chunk_text(content, chunk_size, overlap)
        logger.info(f"文档 '{title}' 分成 {len(text_chunks)} 块")

        # 创建 DocumentChunk
        chunks = []
        for i, text in enumerate(text_chunks):
            chunk = DocumentChunk(
                chunk_id=f"{doc_id}_{i}",
                doc_id=doc_id,
                content=text,
                metadata={**(metadata or {}), "title": title, "chunk_index": i},
            )
            chunks.append(chunk)

        # 尝试向量化
        if self._use_embeddings:
            try:
                embeddings = await self._get_embeddings([c.content for c in chunks])
                for chunk, emb in zip(chunks, embeddings):
                    chunk.embedding = emb
                logger.info(f"文档 '{title}' 向量化完成")
            except Exception as e:
                logger.warning(f"向量化失败，回退到关键词检索: {e}")
                self._use_embeddings = False

        doc = Document(
            doc_id=doc_id,
            title=title,
            content=content,
            chunks=chunks,
            metadata=metadata or {},
        )
        self._documents[doc_id] = doc
        self._chunks.extend(chunks)

        return doc

    async def search(
        self,
        query: str,
        top_k: int = 5,
        min_score: float = 0.1,
    ) -> list[tuple[DocumentChunk, float]]:
        """检索最相关的文档块"""
        if not self._chunks:
            return []

        results = []

        if self._use_embeddings and self._chunks[0].embedding:
            # 向量检索
            try:
                query_embedding = (await self._get_embeddings([query]))[0]
                for chunk in self._chunks:
                    if chunk.embedding:
                        score = _cosine_similarity(query_embedding, chunk.embedding)
                        if score >= min_score:
                            results.append((chunk, score))
            except Exception as e:
                logger.warning(f"向量检索失败，回退到关键词: {e}")
                results = self._keyword_search(query, min_score)
        else:
            # 关键词检索
            results = self._keyword_search(query, min_score)

        # 按分数排序
        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_k]

    def _keyword_search(
        self, query: str, min_score: float
    ) -> list[tuple[DocumentChunk, float]]:
        """关键词回退检索"""
        results = []
        for chunk in self._chunks:
            score = _keyword_similarity(query, chunk.content)
            if score >= min_score:
                results.append((chunk, score))
        return results

    async def _get_embeddings(self, texts: list[str]) -> list[list[float]]:
        """通过 OpenAI 兼容 API 获取向量"""
        client = get_llm_client()
        response = await client._client.embeddings.create(
            input=texts,
            model="text-embedding-3-small",  # 通用 embedding 模型
        )
        return [item.embedding for item in response.data]

    def remove_document(self, doc_id: str) -> bool:
        """删除文档"""
        if doc_id not in self._documents:
            return False
        doc = self._documents.pop(doc_id)
        chunk_ids = {c.chunk_id for c in doc.chunks}
        self._chunks = [c for c in self._chunks if c.chunk_id not in chunk_ids]
        return True

    def list_documents(self) -> list[dict]:
        """列出所有文档"""
        return [
            {
                "doc_id": doc.doc_id,
                "title": doc.title,
                "chunks": len(doc.chunks),
                "metadata": doc.metadata,
            }
            for doc in self._documents.values()
        ]

    @property
    def document_count(self) -> int:
        return len(self._documents)

    @property
    def chunk_count(self) -> int:
        return len(self._chunks)


# 全局单例
vector_store = MemoryVectorStore()
