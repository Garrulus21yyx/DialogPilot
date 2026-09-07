"""Local full-input CrossEncoder implementing the existing knowledge port."""
from __future__ import annotations
import asyncio
import hashlib
import json
import logging
import math
import threading
from pathlib import Path

from application.knowledge_retrieval_text import build_child_retrieval_text
from mcp.result_reranker import candidates_from_items

logger = logging.getLogger(__name__)


class LocalKnowledgeReranker:
    def __init__(self, model_path, *, device='cpu', batch_size=4, max_tokens=8192):
        self.path = Path(model_path).expanduser().resolve(strict=True)
        if batch_size < 1 or not 1 <= max_tokens <= 8192:
            raise ValueError('invalid local reranker budget')
        files = ['config.json','model.safetensors','tokenizer.json','tokenizer_config.json','special_tokens_map.json','sentencepiece.bpe.model']
        hashes = {}
        for name in files:
            with (self.path/name).open('rb') as stream:
                hashes[name] = hashlib.file_digest(stream, 'sha256').hexdigest()
        self.device, self.batch_size, self.max_tokens = device, batch_size, max_tokens
        from importlib.metadata import version
        self.identity = {"runtime": {name: version(name) for name in ("torch", "transformers")},
            "batch_size":batch_size,'implementation':'local-knowledge-ce-v1','artifacts':hashes,
            'preprocessing':'child-retrieval-title-content-v1-no-truncation',
            'max_tokens':max_tokens,'dtype':'float16' if device.startswith('cuda') else 'float32',
            'tie_break':'input-order'}
        self.version = 'local-ce:' + hashlib.sha256(json.dumps(self.identity,sort_keys=True).encode()).hexdigest()
        self._lock = threading.Lock()
        self._tokenizer = self._model = None

    def _score(self, query, texts):
        import torch
        from transformers import AutoTokenizer, AutoModelForSequenceClassification
        with self._lock:
            if self._model is None:
                self._tokenizer = AutoTokenizer.from_pretrained(str(self.path),local_files_only=True)
                self._model = AutoModelForSequenceClassification.from_pretrained(str(self.path),local_files_only=True,
                    torch_dtype=torch.float16 if self.device.startswith('cuda') else torch.float32).to(self.device).eval()
            scores=[]
            for offset in range(0,len(texts),self.batch_size):
                batch=texts[offset:offset+self.batch_size]
                encoded=self._tokenizer([query]*len(batch),batch,padding=True,truncation=False,return_tensors='pt')
                if encoded['input_ids'].shape[1] > self.max_tokens:
                    raise ValueError('local reranker full input exceeds budget')
                with torch.inference_mode():
                    scores.extend(self._model(**{k:v.to(self.device) for k,v in encoded.items()}).logits.reshape(-1).float().cpu().tolist())
            return scores

    async def rerank(self, query, candidates):
        projected=candidates_from_items(candidates)
        ids=tuple(item.candidate_id for item in projected)
        if len(set(ids)) != len(ids):
            raise ValueError('duplicate reranker candidate ID')
        if not ids:
            return (), False
        try:
            texts=[build_child_retrieval_text(title=item.title or item.candidate_id, section_path=(), content=item.text) for item in projected]
            scores=await asyncio.to_thread(self._score,query,texts)
            if len(scores)!=len(ids) or any(not math.isfinite(value) for value in scores):
                raise ValueError('invalid local reranker scores')
            order=sorted(range(len(ids)),key=lambda i:(-scores[i],i))
            return tuple(ids[i] for i in order),False
        except Exception as exc:
            logger.warning('local knowledge reranker fallback: %s',type(exc).__name__)
            return ids,True
