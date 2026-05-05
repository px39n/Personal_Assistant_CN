"""测试内存向量存储和文档分块。"""

import pytest

from app.engine.vectorstore import MemoryVectorStore, chunk_text, _keyword_similarity


class TestChunkText:
    def test_basic_chunking(self):
        text = "段落一\n\n段落二\n\n段落三"
        chunks = chunk_text(text, chunk_size=100)
        assert len(chunks) >= 1
        assert "段落一" in chunks[0]

    def test_long_paragraph_split(self):
        text = "a" * 1000
        chunks = chunk_text(text, chunk_size=200, overlap=50)
        assert len(chunks) > 1
        for c in chunks:
            assert len(c) <= 200

    def test_empty_text(self):
        chunks = chunk_text("")
        assert chunks == []

    def test_single_short_paragraph(self):
        chunks = chunk_text("短文本", chunk_size=500)
        assert len(chunks) == 1
        assert chunks[0] == "短文本"

    def test_overlap_content(self):
        text = "a" * 300
        chunks = chunk_text(text, chunk_size=200, overlap=50)
        # 第二块应该包含第一块末尾的部分
        assert len(chunks) >= 2


class TestKeywordSimilarity:
    def test_identical(self):
        score = _keyword_similarity("你好世界", "你好世界")
        assert score > 0.9

    def test_partial_overlap(self):
        score = _keyword_similarity("Python 编程语言", "Python 数据分析")
        assert 0 < score < 1

    def test_no_overlap(self):
        score = _keyword_similarity("苹果香蕉", "汽车火车")
        assert score == 0.0

    def test_empty_strings(self):
        assert _keyword_similarity("", "") == 0.0
        assert _keyword_similarity("hello", "") == 0.0


class TestMemoryVectorStore:
    @pytest.fixture
    def store(self):
        s = MemoryVectorStore()
        s._use_embeddings = False  # 测试时不调用 embedding API
        return s

    @pytest.mark.asyncio
    async def test_add_document(self, store):
        doc = await store.add_document("测试文档", "这是一段测试内容\n\n这是第二段")
        assert doc.doc_id
        assert doc.title == "测试文档"
        assert len(doc.chunks) >= 1
        assert store.document_count == 1

    @pytest.mark.asyncio
    async def test_duplicate_document(self, store):
        await store.add_document("文档1", "相同内容")
        await store.add_document("文档2", "相同内容")
        assert store.document_count == 1  # 内容相同，不重复添加

    @pytest.mark.asyncio
    async def test_search_keyword(self, store):
        await store.add_document("Python教程", "Python 是一种编程语言，广泛用于数据分析和 AI 开发")
        await store.add_document("烹饪指南", "红烧肉需要五花肉、酱油、糖和料酒")

        results = await store.search("Python 编程")
        assert len(results) > 0
        assert results[0][0].metadata["title"] == "Python教程"

    @pytest.mark.asyncio
    async def test_search_empty_store(self, store):
        results = await store.search("任何查询")
        assert results == []

    @pytest.mark.asyncio
    async def test_remove_document(self, store):
        doc = await store.add_document("要删除的文档", "内容")
        assert store.document_count == 1
        assert store.remove_document(doc.doc_id) is True
        assert store.document_count == 0
        assert store.chunk_count == 0

    @pytest.mark.asyncio
    async def test_remove_nonexistent(self, store):
        assert store.remove_document("fake_id") is False

    @pytest.mark.asyncio
    async def test_list_documents(self, store):
        await store.add_document("文档A", "内容A")
        await store.add_document("文档B", "内容B，不同的内容")
        docs = store.list_documents()
        assert len(docs) == 2
        titles = {d["title"] for d in docs}
        assert titles == {"文档A", "文档B"}
