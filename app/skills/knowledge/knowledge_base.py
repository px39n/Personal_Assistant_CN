"""知识库 RAG Skill — 基于上传文档的语义检索问答。"""

from typing import Any

from loguru import logger

from app.engine.vectorstore import vector_store
from app.skills.base import Skill, SkillCategory, SkillContext, SkillResult, skill


@skill(
    name="knowledge_search",
    description="从用户上传的个人知识库中检索相关信息。适用于用户询问已上传文档中的内容",
    category=SkillCategory.KNOWLEDGE,
    icon="📚",
    config_schema={
        "type": "object",
        "properties": {
            "default_top_k": {
                "type": "integer",
                "title": "默认检索数量",
                "description": "每次检索返回的默认文档块数量",
                "default": 3,
                "minimum": 1,
                "maximum": 10,
            },
        },
    },
    parameters_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "检索关键词或问题",
            },
            "top_k": {
                "type": "integer",
                "description": "返回最相关的结果数量",
                "default": 3,
            },
        },
        "required": ["query"],
    },
)
class KnowledgeSearchSkill(Skill):
    """从知识库中检索相关文档块"""

    async def execute(self, context: SkillContext, **kwargs: Any) -> SkillResult:
        query = kwargs.get("query", "")
        top_k = kwargs.get("top_k", 3)

        if not query:
            return SkillResult.fail("检索关键词不能为空")

        if vector_store.document_count == 0:
            return SkillResult(
                success=True,
                data=[],
                summary="知识库为空，尚未上传任何文档。请先通过 /api/documents 上传文档。",
            )

        try:
            results = await vector_store.search(query, top_k=top_k)

            if not results:
                return SkillResult(
                    success=True,
                    data=[],
                    summary=f"在知识库中未找到与 '{query}' 相关的内容",
                )

            # 构建检索结果摘要
            summary_parts = [f"从知识库中找到 {len(results)} 条相关内容:\n"]
            result_data = []

            for i, (chunk, score) in enumerate(results, 1):
                title = chunk.metadata.get("title", "未知文档")
                summary_parts.append(f"**[{i}] {title}** (相关度: {score:.2f})")
                summary_parts.append(f"{chunk.content[:300]}")
                summary_parts.append("")

                result_data.append({
                    "title": title,
                    "content": chunk.content,
                    "score": round(score, 3),
                    "doc_id": chunk.doc_id,
                    "chunk_index": chunk.metadata.get("chunk_index", 0),
                })

            return SkillResult(
                success=True,
                data=result_data,
                summary="\n".join(summary_parts),
                ui_card={
                    "type": "knowledge_results",
                    "query": query,
                    "results": result_data,
                },
            )

        except Exception as e:
            logger.error(f"知识库检索失败: {e}", exc_info=True)
            return SkillResult.fail(f"检索失败: {str(e)}")
