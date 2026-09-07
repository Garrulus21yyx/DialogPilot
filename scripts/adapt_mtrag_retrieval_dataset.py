"""Adapt pinned official passages/qrels without treating passage relevance as answer spans."""
import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import zipfile

DOMAINS = ('clapnq', 'cloud', 'fiqa', 'govt')


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda: f.read(1024 * 1024), b''):
            h.update(b)
    return h.hexdigest()


def rows(path):
    with path.open() as f:
        return [json.loads(line) for line in f if line.strip()]


def emit(handle, value):
    handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True)+'\n')


def build(cache, corpora, lock, output, mode='lastturn'):
    manifest = json.loads(lock.read_text())
    for name, source in manifest['source_files'].items():
        if digest(cache/name) != source['sha256']:
            raise ValueError(f'locked source checksum mismatch: {name}')
    task_group = {}
    for group in manifest['groups']:
        for task in group['task_ids']:
            if task in task_group:
                raise ValueError('duplicate task in conversation split')
            task_group[task] = group
    refs = {r['task_id']:r for r in rows(cache/'reference.jsonl')}
    output.mkdir(parents=True, exist_ok=False)
    stats, source_files, total_cases, total_docs = {}, {}, 0, 0
    with (output/'corpus.jsonl').open('w') as corpus_out, (output/'cases.jsonl').open('w') as cases_out, (output/'official-queries.jsonl').open('w') as queries_out:
        for domain in DOMAINS:
            variants = {}
            for kind in ('lastturn','questions','rewrite'):
                data = rows(cache/f'{domain}-{kind}.jsonl')
                mapping = {r['_id']:r['text'] for r in data}
                if len(mapping) != len(data):
                    raise ValueError('duplicate query id')
                variants[kind] = mapping
            query_ids = set(variants[mode])
            if any(set(v) != query_ids for v in variants.values()):
                raise ValueError('query variant identity mismatch')
            qrels = {}
            with (cache/f'{domain}-qrels.tsv').open() as f:
                for r in csv.DictReader(f, delimiter='\t'):
                    qid, pid, score = r['query-id'], r['corpus-id'], int(r['score'])
                    if qid not in query_ids or score <= 0:
                        raise ValueError('unsupported qrel query or score')
                    key = (qid,pid)
                    if key in qrels:
                        raise ValueError('duplicate qrel')
                    qrels[key] = score
            required = {pid for _,pid in qrels}
            found, seen, empty_ids = {}, set(), []
            archive = corpora/f'{domain}.jsonl.zip'
            source_files[domain] = {'sha256':digest(archive), 'bytes':archive.stat().st_size,
                'url':f"https://raw.githubusercontent.com/IBM/mt-rag-benchmark/{manifest['revision']}/corpora/passage_level/{domain}.jsonl.zip"}
            with zipfile.ZipFile(archive) as z, z.open(f'{domain}.jsonl') as f:
                for line in f:
                    row = json.loads(line)
                    pid = row['_id']
                    if pid in seen:
                        raise ValueError('duplicate source passage ID')
                    seen.add(pid)
                    if not row['text'].strip():
                        empty_ids.append(pid)
                        continue
                    if pid in required:
                        found[pid] = row['text']
                    emit(corpus_out, {'id':f'mtrag:{domain}:{pid}', 'title':row.get('title',''), 'content':row['text'],
                        'metadata':{'domain':domain, 'official_passage_id':pid, 'source_url':row.get('url',''), 'source_granularity':'official_passage'}})
            if required - found.keys():
                raise ValueError(f'{domain}: missing qrel passage IDs')
            split_counts, answerability = Counter(), Counter()
            for qid in sorted(query_ids):
                group = task_group[qid]
                if group['domain'] != domain or refs[qid]['conversation_id'] != group['conversation_id']:
                    raise ValueError('task/conversation/domain mismatch')
                labels = refs[qid]['Answerability']
                if labels not in (['ANSWERABLE'], ['PARTIAL']):
                    raise ValueError('unsupported retrieval answerability')
                evidence = [{'document_id':f'mtrag:{domain}:{pid}', 'start_char':0, 'end_char':len(found[pid]),
                    'quote':'', 'relevance':score, 'granularity':'document'} for (query,pid),score in sorted(qrels.items()) if query == qid]
                if not evidence:
                    raise ValueError('retrieval query without positive qrels')
                emit(cases_out, {'id':qid,'group_id':'mtrag-'+group['conversation_id'], 'split':group['split'],
                    'query':variants[mode][qid], 'history':[], 'evidence':evidence,
                    'query_types':['mtrag',domain,mode,labels[0].lower()], 'answerable':True})
                emit(queries_out, {'task_id':qid,'conversation_id':group['conversation_id'],
                    'answerability':labels, 'queries':{k:v[qid] for k,v in variants.items()}})
                split_counts[group['split']] += 1
                answerability[labels[0]] += 1
            stats[domain] = {'source_rows':len(seen), 'passages':len(seen)-len(empty_ids), 'excluded_empty_ids':empty_ids, 'qrels':len(qrels), 'exact_qrel_matches':len(qrels),
                             'queries':len(query_ids), 'split_queries':dict(split_counts), 'answerability':dict(answerability)}
            total_docs += len(seen)-len(empty_ids)
            total_cases += len(query_ids)
    result = {'schema_version':1,'dataset_id':'mtrag-passage-retrieval-'+mode+'-v1',
        'document_count':total_docs,'case_count':total_cases,
        'corpus_sha256':digest(output/'corpus.jsonl'),'cases_sha256':digest(output/'cases.jsonl'),
        'source':{'revision':manifest['revision'],'split_lock_sha256':digest(lock), 'archives':source_files,
                  'query_mode':mode, 'stats':stats, 'inference_run':False, 'api_calls':0,
                  'label_scope':'Official passage relevance, encoded as document granularity. Not exact answer spans or evidence of full answerability; PARTIAL retained in query_types. No source re-chunking tested.',
                  'official_queries_sha256':digest(output/'official-queries.jsonl')}}
    (output/'manifest.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('cache','corpora','lock','output'):
        p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--mode',choices=('lastturn','questions','rewrite'),default='lastturn')
    a=p.parse_args()
    result=build(a.cache,a.corpora,a.lock,a.output,a.mode)
    print(json.dumps({'documents':result['document_count'],'cases':result['case_count'],'stats':result['source']['stats']},indent=2))
