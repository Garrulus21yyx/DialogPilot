"""Composable, trace-first RAG evaluation primitives."""

from evaluation.rag_pipeline.contracts import (
    EvidenceSpan,
    PipelineConfig,
    QueryVariant,
    RagCase,
    RagDocument,
    StageTrace,
)
from evaluation.rag_pipeline.fusion import fuse_rankings
from evaluation.rag_pipeline.metrics import (
    evaluate_chunk_projection,
    evaluate_stage_trace,
)
from evaluation.rag_pipeline.query_metrics import evaluate_query_variants
from evaluation.rag_pipeline.selection import select_configuration

__all__ = [
    "EvidenceSpan",
    "PipelineConfig",
    "QueryVariant",
    "RagCase",
    "RagDocument",
    "StageTrace",
    "evaluate_chunk_projection",
    "evaluate_stage_trace",
    "evaluate_query_variants",
    "fuse_rankings",
    "select_configuration",
]
