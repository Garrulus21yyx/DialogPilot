"""
RAG 知识库 —— 基于 ChromaDB 的真实检索实现。

功能：
  1. 文档导入：将文本切片后存入 ChromaDB（自动生成 Embedding）
  2. 语义检索：根据 query 从知识库中检索最相关的文档片段
  3. 与 MCP 工具框架集成：作为 knowledge_search 工具的真实 handler

ChromaDB 在这里的角色：
  - memory/ 中用于存储对话记忆（情景记忆 + 用户画像）
  - 这里用于存储知识库文档（RAG 检索）
  两者是不同的 collection，互不干扰。
"""
import asyncio
import hashlib
import logging
from typing import Any, Dict, List, Optional

from core.chroma_client import create_chroma_client
from memory.context import TokenEstimator
from core.rag_policy import DEFAULT_RAG_RETRIEVAL_POLICY
from memory.hybrid_retrieval import HybridMemoryRetriever, MemoryDocument
from mcp.document_chunker import ChunkStrategy, DocumentChunk, DocumentChunker
from mcp.rank_fusion import fuse_rankings

logger = logging.getLogger(__name__)


class IncompatibleKnowledgeIndexError(RuntimeError):
    """Persisted chunks were produced by a different indexing contract."""


class KnowledgeBase:
    """
    基于 ChromaDB 的 RAG 知识库。

    ChromaDB 内置了 Embedding 模型（all-MiniLM-L6-v2），
    调用 add() 时自动生成向量，query() 时自动做语义匹配。
    不需要额外调用 Anthropic Embeddings API。
    """

    COLLECTION_NAME = "knowledge_base"
    DEFAULT_CHUNK_MAX_TOKENS = 512
    DEFAULT_CHUNK_OVERLAP_TOKENS = 64
    CHUNKING_VERSION = 4

    def __init__(
        self,
        chroma_host: str = "localhost",
        chroma_port: int = 8000,
        chroma_path: str = "./data/chroma",
        chroma_mode: str = "remote",
        load_default_docs: bool = True,
        collection_name: str = COLLECTION_NAME,
        chunk_max_tokens: int = DEFAULT_CHUNK_MAX_TOKENS,
        chunk_overlap_tokens: int = DEFAULT_CHUNK_OVERLAP_TOKENS,
        chunk_strategy: ChunkStrategy | str = ChunkStrategy.FIXED_TOKENS,
        retrieval_rrf_k: int = int(DEFAULT_RAG_RETRIEVAL_POLICY["rrf_k"]),
        retrieval_vector_weight: float = float(DEFAULT_RAG_RETRIEVAL_POLICY["vector_weight"]),
        retrieval_lexical_weight: float = float(DEFAULT_RAG_RETRIEVAL_POLICY["lexical_weight"]),
    ):
        """按显式部署模式连接 ChromaDB，不在两套物理存储间静默切换。"""
        chunk_max_tokens = int(chunk_max_tokens)
        chunk_overlap_tokens = int(chunk_overlap_tokens)
        if chunk_max_tokens < 32:
            raise ValueError("chunk_max_tokens must be at least 32")
        if chunk_overlap_tokens < 0 or chunk_overlap_tokens >= chunk_max_tokens:
            raise ValueError("chunk_overlap_tokens must be non-negative and smaller than chunk_max_tokens")
        self._client, self._chroma_backend = create_chroma_client(
            mode=chroma_mode, host=chroma_host, port=chroma_port, path=chroma_path,
        )
        self._use_server = self._chroma_backend.mode == "remote"
        self._hybrid_retriever = HybridMemoryRetriever(
            rrf_k=retrieval_rrf_k,
            vector_weight=retrieval_vector_weight,
            lexical_weight=retrieval_lexical_weight,
            recency_weight=0.0,
        )
        self._token_estimator = TokenEstimator()
        self._chunker = DocumentChunker(self._token_estimator)
        self._chunk_max_tokens = chunk_max_tokens
        self._chunk_overlap_tokens = chunk_overlap_tokens
        self._chunk_strategy = ChunkStrategy(chunk_strategy)
        logger.info("知识库 ChromaDB 模式: %s (%s)", self._chroma_backend.mode, self._chroma_backend.location)

        # 使用服务端时不传 embedding_function，让服务端处理
        # 本地模式时也不传，使用 ChromaDB 默认的（会触发模型下载）
        collection_metadata = {
            "description": "DialogPilot RAG 知识库",
            **{key: str(value) for key, value in self.index_contract.items()},
        }
        self._collection = self._client.get_or_create_collection(
            name=collection_name,
            metadata=collection_metadata,
        )

        existing_count = self._collection.count()
        if existing_count:
            self._validate_index_contract()
        # 空 collection 才能按当前合同导入；旧索引绝不静默混用。
        if load_default_docs and existing_count == 0:
            self._load_default_docs()

    @property
    def index_contract(self) -> Dict[str, Any]:
        """Return the authoritative chunking contract persisted on every chunk."""
        return {
            "chunking_version": self.CHUNKING_VERSION,
            "chunk_max_tokens": self._chunk_max_tokens,
            "chunk_overlap_tokens": self._chunk_overlap_tokens,
            "chunk_strategy": self._chunk_strategy.value,
        }

    def _validate_index_contract(self) -> None:
        """Reject a non-empty collection if any sampled chunk violates the contract."""
        collection_metadata = getattr(self._collection, "metadata", None)
        if isinstance(collection_metadata, dict):
            actual = {
                key: collection_metadata.get(key)
                for key in self.index_contract
            }
            expected = {key: str(value) for key, value in self.index_contract.items()}
            if actual == expected:
                return
        result = self._collection.get(include=["metadatas"])
        mismatches = []
        for index, metadata in enumerate(result.get("metadatas") or []):
            metadata = metadata if isinstance(metadata, dict) else {}
            actual = {key: metadata.get(key) for key in self.index_contract}
            if actual != self.index_contract:
                mismatches.append({"chunk": index, "actual": actual})
                if len(mismatches) >= 3:
                    break
        if mismatches:
            raise IncompatibleKnowledgeIndexError(
                "knowledge index contract mismatch; re-import the authoritative source "
                f"documents with expected={self.index_contract}, samples={mismatches}"
            )

    @property
    def storage_backend(self) -> Dict[str, str]:
        """返回实际连接的物理存储身份。"""
        return {
            **self._chroma_backend.to_dict(),
            "chunking_version": str(self.CHUNKING_VERSION),
            "chunk_max_tokens": str(self._chunk_max_tokens),
            "chunk_overlap_tokens": str(self._chunk_overlap_tokens),
            "chunk_strategy": self._chunk_strategy.value,
            "retrieval_rrf_k": str(self._hybrid_retriever.rrf_k),
            "retrieval_vector_weight": str(self._hybrid_retriever.vector_weight),
            "retrieval_lexical_weight": str(self._hybrid_retriever.lexical_weight),
            "index_contract_fingerprint": hashlib.sha256(
                repr(sorted(self.index_contract.items())).encode("utf-8")
            ).hexdigest(),
        }

    # ── 文档管理 ──────────────────────────────────────────────────────────────

    def add_documents(self, documents: List[Dict[str, str]]) -> int:
        """
        批量导入文档到知识库。

        documents 格式: [{"id": "可选稳定来源 ID", "title": "...", "content": "..."}, ...]
        长文档按结构边界和 Token 预算切片，并保留有界 overlap。
        """
        ids, docs, metas = [], [], []
        chunk_strategy = getattr(
            self, "_chunk_strategy", ChunkStrategy.STRUCTURE_AWARE,
        )

        for doc in documents:
            title   = doc.get("title", "")
            content = doc.get("content", "")
            source_id = str(doc.get("id") or "").strip() or hashlib.sha256(
                f"{title}\0{content}".encode("utf-8")
            ).hexdigest()[:24]
            chunks = self._chunk_spans(content)

            for i, chunk in enumerate(chunks):
                chunk_id = (
                    f"{source_id}::chunk-{i}"
                    if source_id
                    else hashlib.md5(f"{title}_{i}_{chunk.content[:50]}".encode()).hexdigest()
                )
                ids.append(chunk_id)
                docs.append(chunk.content)
                metas.append({
                    "chunk_id": chunk_id,
                    "chunking_version": self.CHUNKING_VERSION,
                    "chunk_max_tokens": self._chunk_max_tokens,
                    "chunk_overlap_tokens": self._chunk_overlap_tokens,
                    "chunk_strategy": chunk_strategy.value,
                    "source_start_char": chunk.start_char,
                    "source_end_char": chunk.end_char,
                    "document_id": source_id,
                    "title": title,
                    "chunk_index": i,
                    "total_chunks": len(chunks),
                })

        if ids:
            # ChromaDB 会自动生成 Embedding
            self._collection.add(ids=ids, documents=docs, metadatas=metas)
            logger.info(f"知识库导入 {len(ids)} 个文档片段")

        return len(ids)

    async def add_documents_async(self, documents: List[Dict[str, str]]) -> int:
        """异步导入文档；ChromaDB 客户端为同步实现，因此放入线程池执行。"""
        return await asyncio.to_thread(self.add_documents, documents)

    def search(
        self,
        query: str,
        top_k: int = 5,
        *,
        retrieval_policy: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """
        语义检索：根据 query 返回最相关的文档片段。

        ChromaDB 内部自动将 query 转为向量，与存储的文档向量做余弦相似度匹配。
        """
        query = str(query or "").strip()
        if not query or self._collection.count() == 0:
            return []
        return self.search_variants(
            [("raw", query, 1.0)], top_k=top_k, retrieval_policy=retrieval_policy,
        )

    def search_variants(
        self,
        variants: List[tuple[str, str, float]],
        *,
        top_k: int = 5,
        retrieval_policy: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Fuse query variants and BM25/vector rankings in one weighted-RRF space."""
        policy = dict(retrieval_policy or {})
        rrf_k = int(policy.get("rrf_k", self._hybrid_retriever.rrf_k))
        vector_weight = float(policy.get("vector_weight", self._hybrid_retriever.vector_weight))
        lexical_weight = float(policy.get("lexical_weight", self._hybrid_retriever.lexical_weight))
        candidate_k = max(int(policy.get("candidate_k", DEFAULT_RAG_RETRIEVAL_POLICY["candidate_k"])), int(top_k))
        normalized = []
        seen_kinds = set()
        for kind, text, weight in variants:
            kind, text, weight = str(kind), str(text).strip(), float(weight)
            if text and weight > 0 and kind not in seen_kinds:
                normalized.append((kind, text, weight))
                seen_kinds.add(kind)
        if not normalized or self._collection.count() == 0:
            return []
        corpus_results = self._collection.get(include=["documents", "metadatas"])

        def documents(result: Dict[str, Any], *, nested: bool) -> List[MemoryDocument]:
            ids = result.get("ids") or []
            texts = result.get("documents") or []
            metas = result.get("metadatas") or []
            if nested:
                ids = ids[0] if ids and isinstance(ids[0], list) else ids
                texts = texts[0] if texts and isinstance(texts[0], list) else texts
                metas = metas[0] if metas and isinstance(metas[0], list) else metas
            rows = []
            for index, chunk_id in enumerate(ids):
                meta = metas[index] if index < len(metas) and isinstance(metas[index], dict) else {}
                text = str(texts[index] if index < len(texts) else "")
                if text:
                    rows.append(MemoryDocument(
                        # chunk_id 是排序候选身份；父 document_id 只能在最终投影时使用。
                        memory_id=str(meta.get("chunk_id") or chunk_id),
                        content=text,
                    ))
            return rows

        corpus_documents = documents(corpus_results, nested=False)
        by_chunk = {item.memory_id: item for item in corpus_documents}
        rankings: Dict[str, List[str]] = {}
        weights: Dict[str, float] = {}
        ranks_by_chunk: Dict[str, Dict[str, int]] = {}
        for kind, text, query_weight in normalized:
            if lexical_weight > 0:
                scores = HybridMemoryRetriever._bm25_scores(text, corpus_documents)
                order = [
                    chunk_id for chunk_id, score in sorted(
                        scores.items(), key=lambda item: (-item[1], item[0]),
                    ) if score > 0
                ][:candidate_k]
                source = f"{kind}:bm25"
                rankings[source], weights[source] = order, query_weight * lexical_weight
                for rank, chunk_id in enumerate(order, 1):
                    ranks_by_chunk.setdefault(chunk_id, {})[source] = rank
            if vector_weight > 0:
                result = self._collection.query(
                    query_texts=[text],
                    n_results=min(candidate_k, self._collection.count()),
                )
                order = [item.memory_id for item in documents(result, nested=True)]
                source = f"{kind}:vector"
                rankings[source], weights[source] = order, query_weight * vector_weight
                for rank, chunk_id in enumerate(order, 1):
                    ranks_by_chunk.setdefault(chunk_id, {})[source] = rank
        active_rankings = {key: value for key, value in rankings.items() if weights.get(key, 0) > 0}
        if not active_rankings:
            return []
        fused_ids = fuse_rankings(
            active_rankings, weights=weights, rrf_k=rrf_k, top_k=candidate_k,
        )
        metadata_by_chunk = {}
        corpus_ids = corpus_results.get("ids") or []
        for index, meta in enumerate(corpus_results.get("metadatas") or []):
            if isinstance(meta, dict):
                stored_id = str(corpus_ids[index]) if index < len(corpus_ids) else ""
                metadata_by_chunk[str(meta.get("chunk_id") or stored_id)] = meta

        projected = []
        for chunk_id in fused_ids:
            document = by_chunk.get(chunk_id)
            if document is None:
                continue
            meta = metadata_by_chunk.get(chunk_id, {})
            document_id = str(meta.get("document_id") or chunk_id)
            source_ranks = ranks_by_chunk.get(chunk_id, {})
            score = sum(
                weights[source] / (rrf_k + rank)
                for source, rank in source_ranks.items()
            )
            projected.append({
                "document_id": document_id,
                "chunk_id": chunk_id,
                "title": meta.get("title", ""),
                "content": document.content,
                "score": round(score, 8),
                "chunk": meta.get("chunk_index", 0),
                "source_start_char": meta.get("source_start_char", 0),
                "source_end_char": meta.get("source_end_char", len(document.content)),
                "sources": list(source_ranks),
                "ranks": dict(source_ranks),
            })
            if len(projected) >= max(1, int(top_k)):
                break
        return projected

    async def search_async(
        self,
        query: str,
        top_k: int = 5,
        *,
        retrieval_policy: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """异步检索；ChromaDB 客户端为同步实现，因此放入线程池执行。"""
        return await asyncio.to_thread(
            self.search, query, top_k, retrieval_policy=retrieval_policy,
        )

    async def search_variants_async(
        self,
        variants: List[tuple[str, str, float]],
        *,
        top_k: int = 5,
        retrieval_policy: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        return await asyncio.to_thread(
            self.search_variants, variants, top_k=top_k, retrieval_policy=retrieval_policy,
        )

    @property
    def doc_count(self) -> int:
        """返回知识库当前持久化的文档片段数。"""
        return self._collection.count()

    async def doc_count_async(self) -> int:
        """异步获取文档片段数量。"""
        return await asyncio.to_thread(self._collection.count)

    # ── MCP 工具 handler ─────────────────────────────────────────────────────

    async def search_handler(self, params: Dict[str, Any], context: Any) -> List[Dict]:
        """
        作为 MCP 工具的 handler 注册。

        MCPToolManager.register(Tool(
            name="knowledge_search",
            handler=kb.search_handler,
            ...
        ))
        """
        query = params.get("query", "")
        top_k = params.get("top_k", 5)
        policy = dict((context or {}).get("retrieval_policy") or {})
        variants = params.get("query_variants")
        if isinstance(variants, list):
            parsed = [
                (str(item.get("kind")), str(item.get("query")), float(item.get("weight", 0)))
                for item in variants if isinstance(item, dict)
            ]
            return await self.search_variants_async(parsed, top_k=top_k, retrieval_policy=policy)
        return await self.search_async(query, top_k=top_k, retrieval_policy=policy)

    # ── 内部方法 ──────────────────────────────────────────────────────────────

    def _chunk_text(
        self,
        text: str,
        *,
        max_tokens: Optional[int] = None,
        overlap_tokens: Optional[int] = None,
    ) -> List[str]:
        """按 Token 上限切片，优先结构边界，并让相邻片段保留有界重叠。"""
        return [chunk.content for chunk in self._chunk_spans(
            text, max_tokens=max_tokens, overlap_tokens=overlap_tokens,
        )]

    def _chunk_spans(
        self,
        text: str,
        *,
        max_tokens: Optional[int] = None,
        overlap_tokens: Optional[int] = None,
    ) -> List[DocumentChunk]:
        """Return the production chunks with authoritative source offsets."""
        max_tokens = self._chunk_max_tokens if max_tokens is None else int(max_tokens)
        overlap_tokens = self._chunk_overlap_tokens if overlap_tokens is None else int(overlap_tokens)
        chunker = getattr(self, "_chunker", DocumentChunker(self._token_estimator))
        strategy = getattr(self, "_chunk_strategy", ChunkStrategy.STRUCTURE_AWARE)
        return chunker.split(
            text,
            max_tokens=max_tokens,
            overlap_tokens=overlap_tokens,
            strategy=strategy,
        )

    def _max_fitting_end(self, text: str, start: int, max_tokens: int) -> int:
        """二分查找从 start 起不超过 Token 上限的最长字符终点。"""
        return DocumentChunker(self._token_estimator).max_fitting_end(text, start, max_tokens)

    @staticmethod
    def _preferred_break(text: str, start: int, max_end: int) -> int:
        """在窗口后 40% 中优先选择段落、句末或空白边界。"""
        return DocumentChunker.preferred_break(text, start, max_end)

    def _overlap_start(self, text: str, start: int, end: int, overlap_tokens: int) -> int:
        """找到不超过 overlap 预算的最长后缀起点。"""
        return DocumentChunker(self._token_estimator).overlap_start(
            text, start, end, overlap_tokens,
        )

    def _load_default_docs(self) -> None:
        """导入默认知识库文档（客服场景常见问题）。"""
        default_docs = [
            {
                "title": "退款政策",
                "content": (
                    "退款政策说明。"
                    "用户在购买后 7 天内可以申请无理由退款。"
                    "退款申请提交后，系统会在 1-3 个工作日内审核。"
                    "审核通过后，款项将在 5-7 个工作日内退回原支付账户。"
                    "如果商品已发货，需要先完成退货流程才能退款。"
                    "退货运费由用户承担，除非是商品质量问题。"
                    "超过 7 天但未超过 30 天的订单，需要提供商品质量问题的证据才能退款。"
                ),
            },
            {
                "title": "订单查询",
                "content": (
                    "订单查询指南。"
                    "用户可以通过订单号查询订单状态。"
                    "订单状态包括：待支付、已支付、已发货、运输中、已签收、已完成。"
                    "如果订单显示已发货但超过 7 天未收到，可以联系客服申请查件。"
                    "物流信息通常在发货后 24 小时内更新。"
                    "如果订单显示异常，请提供订单号联系客服处理。"
                ),
            },
            {
                "title": "账户安全",
                "content": (
                    "账户安全说明。"
                    "建议用户定期修改密码，密码长度至少 8 位，包含字母和数字。"
                    "如果忘记密码，可以通过绑定的手机号或邮箱重置。"
                    "发现账户异常登录时，系统会自动锁定账户并发送通知。"
                    "用户可以在安全设置中开启两步验证，提高账户安全性。"
                    "不要将密码分享给他人，客服人员不会索要用户密码。"
                ),
            },
            {
                "title": "技术故障排查",
                "content": (
                    "常见技术问题排查。"
                    "应用崩溃：请尝试清除缓存后重启应用，如果问题持续请更新到最新版本。"
                    "登录失败 401 错误：表示认证失败，请检查用户名密码是否正确，或尝试重置密码。"
                    "页面加载慢：检查网络连接，尝试切换 WiFi 或移动数据。"
                    "支付失败：确认银行卡余额充足，检查是否开启了网上支付功能。"
                    "500 服务器错误：这是服务端问题，请稍后重试，如果持续出现请联系技术支持。"
                ),
            },
            {
                "title": "会员与积分",
                "content": (
                    "会员积分规则。"
                    "每消费 1 元累积 1 积分。"
                    "积分可以在下次购物时抵扣，100 积分 = 1 元。"
                    "会员等级分为：普通会员、银卡会员（累计消费 1000 元）、金卡会员（累计消费 5000 元）。"
                    "银卡会员享受 95 折优惠，金卡会员享受 9 折优惠。"
                    "积分有效期为 1 年，过期自动清零。"
                    "生日当月消费可获得双倍积分。"
                ),
            },
            {
                "title": "配送说明",
                "content": (
                    "配送服务说明。"
                    "标准配送：3-5 个工作日送达，免运费（订单满 99 元）。"
                    "加急配送：1-2 个工作日送达，运费 15 元。"
                    "同城配送：当日达或次日达，运费 10 元。"
                    "偏远地区可能需要额外 2-3 天。"
                    "配送时间为每天 9:00-18:00，节假日可能延迟。"
                    "如果需要修改收货地址，请在发货前联系客服。"
                ),
            },
        ]
        self.add_documents(default_docs)
        logger.info(f"已导入默认知识库: {len(default_docs)} 篇文档")
