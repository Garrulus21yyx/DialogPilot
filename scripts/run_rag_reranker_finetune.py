"""Bounded zero-provider-API Doc2Dial reranker adaptation pilot.

Official train only; document-disjoint query splits; BM25 candidates are frozen.
Unjudged negatives are weak supervision, never represented as verified false.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import time
from pathlib import Path

import torch
import transformers
from transformers import AutoModelForSequenceClassification, AutoTokenizer

from evaluation.local_bge_m3_retrieval_eval import bm25_matrix
from mcp.document_chunker import ChunkStrategy, DocumentChunker


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2))


def overlap(chunk, span):
    return chunk['start'] < span[1] and span[0] < chunk['end']


def coverage(chunk, spans):
    return [i for i, (start, end) in enumerate(spans)
            if chunk['start'] <= start and end <= chunk['end']]


def metrics(rows, orders):
    totals = dict(mrr5=0., ndcg5=0., complete5=0., candidate_complete=0.)
    for row, order in zip(rows, orders, strict=True):
        relevant = row['relevance']
        ranked = [relevant[i] for i in order[:5]]
        totals['mrr5'] += next((1 / (i + 1) for i, x in enumerate(ranked) if x), 0.)
        dcg = sum(bool(x) / math.log2(i + 2) for i, x in enumerate(ranked))
        ideal = sum(1 / math.log2(i + 2) for i in range(min(5, row['total_relevant'])))
        totals['ndcg5'] += dcg / ideal if ideal else 0
        seen = set().union(*(set(row['coverage'][i]) for i in order[:5]))
        all_seen = set().union(*(set(x) for x in row['coverage']))
        totals['complete5'] += len(seen) == row['span_count']
        totals['candidate_complete'] += len(all_seen) == row['span_count']
    return {**{k: v / len(rows) for k, v in totals.items()}, 'cases': len(rows)}


def passage_groups(chunks):
    """Join documents sharing an exact normalized passage, including title variants."""
    parents = {c['doc']: c['doc'] for c in chunks}
    def root(key):
        while parents[key] != key:
            parents[key] = parents[parents[key]]
            key = parents[key]
        return key
    owners = {}
    for chunk in chunks:
        body = ' '.join(chunk['text'].partition('\n')[2].lower().split())
        if not body:
            continue
        key = digest(body)
        other = owners.setdefault(key, chunk['doc'])
        left, right = sorted((root(other), root(chunk['doc'])))
        parents[right] = left
    return {key: root(key) for key in parents}


def audit_splits(chunks, rows):
    groups = {split: {r['group'] for r in values} for split, values in rows.items()}
    for left in groups:
        for right in groups:
            if left != right and groups[left] & groups[right]:
                raise ValueError('source group crosses split boundary')
    train_ids = {i for r in rows['train'] for i in r['positives'] + r['negatives']}
    normalize = lambda text: ' '.join(text.partition('\n')[2].lower().split())
    trained_bodies = {normalize(chunks[i]['text']) for i in train_ids}
    for split in ('dev', 'test'):
        for row in rows[split]:
            if any(normalize(chunks[i]['text']) in trained_bodies for i in row['positives']):
                raise ValueError('evaluation positive duplicates a training passage')
    return dict(group_intersections=0, exact_evaluation_positive_training_duplicates=0,
                training_pair_passages=len(train_ids), counts={k:len(v) for k,v in rows.items()})


def build(raw, tokenizer, limits, excluded_eval_docs=()):
    docs_raw = json.loads((raw / 'doc2dial_doc.json').read_text())['doc_data']
    dials = json.loads((raw / 'doc2dial_dial_train.json').read_text())['dial_data']
    docs = {key: value for domain in docs_raw.values() for key, value in domain.items()}
    chunks, source_hashes = [], {}
    for doc_id, doc in sorted(docs.items()):
        source_hashes[doc_id] = digest(' '.join(doc['doc_text'].lower().split()))
        for part in DocumentChunker().split(doc['doc_text'], max_tokens=256,
                overlap_tokens=32, strategy=ChunkStrategy.STRUCTURE_AWARE, source_type='text'):
            chunks.append(dict(doc=doc_id, start=part.start_char, end=part.end_char,
                               text=doc['title'] + '\n' + part.content))
    # Passage duplication can cross distinct documents with different titles.
    components = passage_groups(chunks)
    source_hashes = {doc: digest(components[doc]) for doc in source_hashes}
    excluded_groups = {source_hashes[doc] for doc in excluded_eval_docs if doc in source_hashes}
    # Grouping precedes selection and negative mining.
    groups = sorted(set(source_hashes.values()), key=lambda x: digest('split-71:' + x))
    assignments = {key: ('train' if i % 10 < 6 else 'dev' if i % 10 < 8 else 'test')
                   for i, key in enumerate(groups)}
    rows = {key: [] for key in limits}
    used_docs = set()
    dialogues = [(domain, doc_id, dial) for domain, domain_docs in dials.items()
                 for doc_id, values in domain_docs.items() for dial in values]
    dialogues.sort(key=lambda x: digest('case-71:' + x[2]['dial_id']))
    for domain, doc_id, dial in dialogues:
        group = source_hashes[doc_id]
        split = assignments[group]
        if split != 'train' and group in excluded_groups:
            continue
        if group in used_docs or len(rows[split]) >= limits[split]:
            continue
        turns = dial['turns']
        if len(turns) < 2 or turns[0]['role'] != 'user' or turns[1]['role'] != 'agent':
            continue
        query = turns[0]['utterance']
        spans = sorted({(int(docs[doc_id]['spans'][str(r['sp_id'])]['start_sp']),
                         int(docs[doc_id]['spans'][str(r['sp_id'])]['end_sp']))
                        for r in turns[1].get('references', [])})
        if not spans:
            continue
        eligible = [i for i, c in enumerate(chunks)
                    if (split != 'train' or assignments[source_hashes[c['doc']]] == 'train')]
        positives = [i for i in eligible if chunks[i]['doc'] == doc_id and coverage(chunks[i], spans)]
        if set().union(*(set(coverage(chunks[i], spans)) for i in positives)) != set(range(len(spans))):
            continue
        scores = bm25_matrix([query], [chunks[i]['text'] for i in eligible])[0]
        order = sorted(range(len(eligible)), key=lambda i: (-scores[i], eligible[i]))
        candidates = [eligible[i] for i in order[:20]]
        negatives = [i for i in (eligible[j] for j in order)
                     if not (chunks[i]['doc'] == doc_id and any(overlap(chunks[i], s) for s in spans))
                     and not any(chunks[p]['text'] == chunks[i]['text'] for p in positives)][:2]
        # Reject over-budget pairs instead of truncating evidence or changing pools.
        pairs = set(candidates + positives + negatives)
        if not negatives or any(len(tokenizer(query, chunks[i]['text'], truncation=False)['input_ids']) > 768
                                for i in pairs):
            continue
        row = dict(id=dial['dial_id'], doc=doc_id, group=group, domain=domain, query=query,
                   candidates=candidates, positives=positives, negatives=negatives,
                   relevance=[int(i in positives) for i in candidates],
                   coverage=[coverage(chunks[i], spans) if chunks[i]['doc'] == doc_id else [] for i in candidates],
                   total_relevant=len(positives), span_count=len(spans))
        rows[split].append(row)
        used_docs.add(group)
    if any(len(rows[k]) < limits[k] for k in limits):
        raise ValueError(f'insufficient eligible disjoint documents: { {k:len(v) for k,v in rows.items()} }')
    return chunks, rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--raw', type=Path, required=True)
    parser.add_argument('--model', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--train', type=int, default=80)
    parser.add_argument('--dev', type=int, default=20)
    parser.add_argument('--test', type=int, default=40)
    parser.add_argument('--exclude-eval', type=Path, help='Prior pilot data.json: exclude every observed document group from evaluation')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    random.seed(71)
    torch.manual_seed(71)
    torch.set_num_threads(4)
    tokenizer = AutoTokenizer.from_pretrained(args.model, local_files_only=True)
    excluded = []
    if args.exclude_eval:
        prior = json.loads(args.exclude_eval.read_text())
        excluded = [r['doc'] for split in prior['splits'].values() for r in split]
    chunks, rows = build(args.raw, tokenizer, dict(train=args.train, dev=args.dev, test=args.test), excluded)
    audit = audit_splits(chunks, rows)
    write(args.output / 'split_audit.json', audit)
    write(args.output / 'data.json', dict(chunks=chunks, splits=rows))
    manifest = dict(seed=71, api_calls=0, max_pair_tokens=768, chunk_tokens=256, overlap_tokens=32,
                    candidate_source='Python BM25; top20; no gold injection',
                    negatives='unjudged weak negatives; exclude gold overlap',
                    query='first user turn verbatim; no history or rewrite',
                    source='Doc2Dial v1.0.1 official train only; CC-BY-3.0',
                    split_grouping='connected components of exact normalized chunk bodies; titles excluded',
                    excluded_eval_documents=sorted(set(excluded)),
                    source_sha256={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in
                                   [args.raw/'doc2dial_doc.json', args.raw/'doc2dial_dial_train.json']},
                    data_sha256=hashlib.sha256((args.output/'data.json').read_bytes()).hexdigest(),
                    model_sha256=hashlib.sha256((args.model/'model.safetensors').read_bytes()).hexdigest(),
                    trainable='last two encoder layers plus classifier', learning_rate=1e-5, epochs=1)
    manifest.update(torch_version=torch.__version__, transformers_version=transformers.__version__,
                    dropout=False, loss='pairwise two-candidate cross entropy',
                    metric_relevance='binary: candidate fully contains at least one annotated span; nDCG ideal uses all gold-containing chunks',
                    script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    write(args.output / 'manifest.json', manifest)
    print('DATA', {k:len(v) for k,v in rows.items()}, flush=True)
    model = AutoModelForSequenceClassification.from_pretrained(args.model, local_files_only=True).cuda()
    for name, param in model.named_parameters():
        param.requires_grad_(name.startswith(('roberta.encoder.layer.22.', 'roberta.encoder.layer.23.', 'classifier.')))
    def encode(query, ids):
        return tokenizer([query]*len(ids), [chunks[i]['text'] for i in ids],
                         padding=True, truncation=False, return_tensors='pt').to('cuda')
    def evaluate(part):
        model.eval()
        orders=[]
        with torch.inference_mode(), torch.autocast('cuda', dtype=torch.float16):
            for row in rows[part]:
                scores=[]
                for start in range(0, len(row['candidates']), 2):
                    scores.extend(model(**encode(row['query'], row['candidates'][start:start+2])).logits.flatten().float().tolist())
                if not all(math.isfinite(s) for s in scores):
                    raise ValueError('nonfinite evaluation scores')
                orders.append(sorted(range(len(scores)), key=lambda i: (-scores[i], i)))
        return dict(metrics=metrics(rows[part], orders), orders=orders)
    results = {'baseline_dev': evaluate('dev')}
    # Freeze baseline test orders before training, but never use them for selection.
    baseline_test = evaluate('test')
    print('BASELINE DEV', results['baseline_dev']['metrics'], flush=True)
    optimizer = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad], lr=1e-5)
    scaler = torch.amp.GradScaler('cuda')
    model.eval()  # Deterministic frozen backbone; gradients still enabled in selected layers.
    losses=[]
    start=time.monotonic()
    for step, row in enumerate(rows['train']):
        optimizer.zero_grad(set_to_none=True)
        ids = [row['positives'][step % len(row['positives'])], row['negatives'][step % len(row['negatives'])]]
        with torch.autocast('cuda', dtype=torch.float16):
            logits=model(**encode(row['query'], ids)).logits.flatten().float()
            loss=torch.nn.functional.cross_entropy(logits[None], torch.tensor([0], device='cuda'))
        if not torch.isfinite(loss):
            raise ValueError('nonfinite training loss')
        scaler.scale(loss).backward()
        scaler.unscale_(optimizer)
        torch.nn.utils.clip_grad_norm_([p for p in model.parameters() if p.requires_grad], 1.)
        scaler.step(optimizer)
        scaler.update()
        losses.append(float(loss.detach()))
        if step % 10 == 0:
            print('TRAIN', step, losses[-1], flush=True)
    results['training'] = dict(steps=len(losses), seconds=time.monotonic()-start, losses=losses,
                               peak_cuda_bytes=torch.cuda.max_memory_allocated())
    results['adapted_dev']=evaluate('dev')
    results['selected_by_dev'] = results['adapted_dev']['metrics']['ndcg5'] > results['baseline_dev']['metrics']['ndcg5']
    results['baseline_test']=baseline_test
    results['adapted_test']=evaluate('test')
    model.save_pretrained(args.output/'checkpoint')
    tokenizer.save_pretrained(args.output/'checkpoint')
    write(args.output/'results.json', results)
    print('RESULT', {k:v['metrics'] for k,v in results.items() if isinstance(v,dict) and 'metrics' in v}, flush=True)


if __name__ == '__main__':
    main()
