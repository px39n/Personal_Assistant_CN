"""文档管理 API — 上传文档到知识库、列出文档、删除文档。"""

from fastapi import APIRouter, File, Form, UploadFile
from loguru import logger

from app.engine.vectorstore import vector_store

router = APIRouter(prefix="/api/documents", tags=["documents"])


@router.post("/upload")
async def upload_document(
    file: UploadFile = File(None),
    text: str = Form(None),
    title: str = Form(None),
):
    """
    上传文档到知识库。

    支持两种方式:
    - 上传文件（txt/md）
    - 直接提交文本
    """
    content = ""

    if file:
        raw = await file.read()
        # 尝试多种编码
        for encoding in ("utf-8", "gbk", "gb2312", "latin-1"):
            try:
                content = raw.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        if not content:
            return {"error": "无法解码文件，请使用 UTF-8 编码"}

        doc_title = title or file.filename or "未命名文档"
    elif text:
        content = text
        doc_title = title or "文本文档"
    else:
        return {"error": "请提供文件或文本内容"}

    if not content.strip():
        return {"error": "文档内容为空"}

    try:
        doc = await vector_store.add_document(
            title=doc_title,
            content=content,
            metadata={"source": "upload"},
        )

        return {
            "success": True,
            "doc_id": doc.doc_id,
            "title": doc.title,
            "chunks": len(doc.chunks),
            "total_documents": vector_store.document_count,
        }
    except Exception as e:
        logger.error(f"文档上传失败: {e}", exc_info=True)
        return {"error": f"文档处理失败: {str(e)}"}


@router.get("/")
async def list_documents():
    """列出所有已上传的文档"""
    return {
        "count": vector_store.document_count,
        "total_chunks": vector_store.chunk_count,
        "documents": vector_store.list_documents(),
    }


@router.delete("/{doc_id}")
async def delete_document(doc_id: str):
    """删除指定文档"""
    if vector_store.remove_document(doc_id):
        return {"success": True, "message": f"文档 {doc_id} 已删除"}
    return {"error": f"文档 {doc_id} 不存在"}
