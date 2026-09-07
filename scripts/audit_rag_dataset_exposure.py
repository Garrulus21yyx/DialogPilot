"""Inventory local exposure; absence is never an attestation of unseen data."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evaluation.doc2dial_heldout_dataset import _load_member, DOC2DIAL_ARCHIVE_SHA256


def sha(data):
    return hashlib.sha256(data).hexdigest()


def query_key(text):
    return sha(' '.join(text.split()).casefold().encode())


def inspect(value, groups, queries, identities, articles):
    if isinstance(value, dict):
        groups.update(x for x in value.get('excluded_group_ids', []) if isinstance(x, str))
        for key, item in value.items():
            if isinstance(item, str):
                if key == 'group_id':
                    groups.add(item)
                if key in ('query', 'question', 'resolved_query'):
                    queries.add(query_key(item))
                if key in ('id', 'case_id', 'task_id', 'conversation_id'):
                    identities.add(item)
                if key == 'document_id':
                    articles.add(item.removeprefix('wixqa-').removeprefix('wix-'))
            inspect(item, groups, queries, identities, articles)
    elif isinstance(value, list):
        for item in value:
            inspect(item, groups, queries, identities, articles)


def audit(roots, lock, cache, archive):
    if any(not root.is_dir() for root in roots):
        raise ValueError('every declared scan root must exist')
    groups, queries, identities, articles = set(), set(), set(), set()
    files, errors = [], []
    paths = {p for root in roots for p in root.rglob('*')}
    for p in sorted(paths):
        if not p.is_file() or not any(k in p.name for k in ('cases', 'queries', 'predictions', 'consumption-audit')):
            continue
        if not p.name.endswith(('.json', '.jsonl', '.json.gz', '.jsonl.gz')):
            continue
        try:
            raw = p.read_bytes()
            content = (gzip.decompress(raw) if p.suffix == '.gz' else raw).decode()
            rows = [json.loads(x) for x in content.splitlines() if x.strip()] if '.jsonl' in p.name else json.loads(content)
            local = [set() for _ in range(4)]
            inspect(rows, *local)
            for target, incoming in zip((groups, queries, identities, articles), local):
                target.update(incoming)
            files.append({'path':str(p), 'sha256':sha(raw), 'groups':sorted(local[0]),
                          'query_hashes':sorted(local[1]), 'identities':sorted(local[2]),
                          'document_ids':sorted(local[3])})
        except (ValueError, OSError, UnicodeError) as exc:
            errors.append({'path':str(p), 'error':str(exc)})
    mtrag = json.loads((lock/'mtrag-split.json').read_text())
    wix = json.loads((lock/'wixqa-test-lock.json').read_text())
    checks = []
    for manifest in (mtrag, wix):
        for name, spec in manifest['source_files'].items():
            p = cache/name
            checks.append({'path':str(p), 'expected':spec['sha256'],
                           'matches':p.is_file() and sha(p.read_bytes()) == spec['sha256']})
    if not all(x['matches'] for x in checks):
        raise ValueError('locked raw source missing or checksum mismatch')
    if sha(archive.read_bytes()) != DOC2DIAL_ARCHIVE_SHA256:
        raise ValueError('Doc2Dial archive mismatch')
    dials = _load_member(archive, 'doc2dial_dial_test.json')['dial_data']
    official = {f"doc2dial-{c['dial_id']}" for domain in dials.values() for cs in domain.values() for c in cs}
    mt_queries = {}
    for domain in ('clapnq', 'cloud', 'fiqa', 'govt'):
        for mode in ('lastturn', 'questions', 'rewrite'):
            for line in (cache/f'{domain}-{mode}.jsonl').read_text().splitlines():
                row = json.loads(line)
                mt_queries.setdefault(str(row['_id']), set()).add(query_key(row['text']))
    mt = []
    for group in mtrag['groups']:
        direct = group['conversation_id'] in identities or bool(set(group['task_ids']) & identities)
        query_match = any(mt_queries.get(t, set()) & queries for t in group['task_ids'])
        mt.append({**group, 'identity_match':direct, 'query_match':query_match})
    wr = {}
    for name in ('wix-expertwritten', 'wix-simulated'):
        rows = [json.loads(x) for x in (cache/(name+'.jsonl')).read_text().splitlines()]
        matches = []
        for i, row in enumerate(rows):
            q = query_key(row['question']) in queries
            a = bool({str(x) for x in row['article_ids']} & articles)
            matches.append({'row_index':i, 'query_match':q, 'article_match':a})
        wr[name] = {'rows':len(rows), 'query_matches':sum(x['query_match'] for x in matches),
                    'article_matches':sum(x['article_match'] for x in matches), 'matches':matches}
    return {'scope':'Local case/query/prediction JSON and gzip inventory; conservative exposure, NOT execution or global freshness proof. Reports, opaque snapshots, renamed queries, remote runs and deleted files may be absent.',
            'api_calls':0, 'scan_roots':[str(root) for root in roots], 'files':files, 'parse_errors':errors, 'source_checks':checks,
            'doc2dial':{'official_test_groups':len(official), 'locally_present_test_groups':len(official & groups),
                        'not_observed_test_groups':len(official-groups), 'not_observed_ids':sorted(official-groups)},
            'mtrag':{'groups':mt, 'total_groups':len(mt), 'identity_matched_groups':sum(x['identity_match'] for x in mt),
                     'query_matched_groups':sum(x['query_match'] for x in mt),
                     'locked_heldout_groups':sum(x['split']=='heldout' for x in mt)}, 'wixqa':wr,
            'freshness_attested':False}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, required=True, action='append')
    for field in ('lock', 'cache', 'archive', 'output'):
        p.add_argument('--'+field, type=Path, required=True)
    a = p.parse_args()
    result = audit(a.root, a.lock, a.cache, a.archive)
    a.output.mkdir(parents=True, exist_ok=False)
    with gzip.open(a.output/'inventory.json.gz', 'wt') as out:
        json.dump(result, out, ensure_ascii=False, sort_keys=True)
    summary = {k:v for k,v in result.items() if k not in ('files','source_checks')}
    summary['file_count'] = len(result['files'])
    summary['source_checks_passed'] = len(result['source_checks'])
    summary['mtrag'].pop('groups')
    summary['doc2dial'].pop('not_observed_ids')
    for value in summary['wixqa'].values():
        value.pop('matches')
    (a.output/'report.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(summary, ensure_ascii=False, indent=2))
