"""原文完全重复与字符ngram/MinHash候选；任何近重复都不合并来源或共用输入。"""
from collections import defaultdict
import re
import unicodedata
from .common import digest,identity,write_rows,rows,read_json,write_json,file_hash


def comparison_view(text):
    # 这是比较视图，不用于原文引用或模型输入；原文从不被NFKC或去空白覆盖。
    return re.sub(r'\s+','',unicodedata.normalize('NFKC',text))


def grams(text,size):
    return {text[i:i+size] for i in range(max(1,len(text)-size+1))} if text else set()


def critical(text):
    return {'numbers':re.findall(r'\d+(?:\.\d+)?(?:%|％|万元|亿元|元|年|月|日|人|亩|公里|平方米|米)?',text),
            'negations':[x for x in ('不得','不予','禁止','除外','不超过','不少于','以上','以下') if x in text]}


def detect(chunks,destination,config,metadata=None):
    """记录候选和差异；限制单块候选数时明确报告截断，不能声称穷举近重复。"""
    from datasketch import MinHash,MinHashLSH
    exact=defaultdict(list);lsh=MinHashLSH(threshold=config['threshold'],num_perm=config['num_perm'])
    prior={};pairs=[];capped=0;origins={};metadata=metadata or {}
    for chunk in chunks:
        body=''.join(s['text'] for s in chunk['source_segments'])
        raw_hash=digest(body);exact[raw_hash].append(chunk['id'])
        document=metadata.get(chunk.get('document_id'),{})
        signals={**critical(comparison_view(body)),
            'source_area':document.get('area_name'),'source_period':document.get('fyp_name'),
            'required_context_hashes':[digest(s['text']) for s in chunk.get('context_segments',[])],
            'section_path':chunk.get('section_path',[])}
        origins[chunk['id']]={'document_id':chunk.get('document_id'),'parent_id':chunk.get('parent_id'),
            'source_ranges':[[s.get('start'),s.get('end')] for s in chunk['source_segments']],
            'comparison_signals':signals}
        text=comparison_view(body)
        if len(text)<40:
            continue
        shingles=grams(text,config['ngram'])
        sketch=MinHash(num_perm=config['num_perm'],seed=config['seed'])
        sketch.update_batch([s.encode('utf-8') for s in sorted(shingles)])
        candidates=sorted(lsh.query(sketch))
        if len(candidates)>config['max_candidates_per_chunk']:
            capped+=1
        for candidate in candidates[:config['max_candidates_per_chunk']]:
            previous=prior[candidate]
            if raw_hash==previous['raw_hash']:
                continue
            union=shingles|previous['shingles'];score=len(shingles&previous['shingles'])/len(union)
            if score<config['threshold']:
                continue
            different=signals!=previous['critical']
            left,right=sorted([chunk['id'],candidate])
            left_signals,right_signals=(signals,previous['critical']) if left==chunk['id'] else (previous['critical'],signals)
            pairs.append({'id':identity('dedup_pair',left,right),'left_id':left,'right_id':right,
                'method':'char_ngram_minhash_lsh_then_exact_jaccard','similarity':score,
                'status':'rejected_as_equivalent' if different else 'candidate',
                'critical_difference':different,'left_signals':left_signals,'right_signals':right_signals,
                'rule_version':'near-1','merge_sources':False,'reuse_embedding':False})
        lsh.insert(chunk['id'],sketch)
        prior[chunk['id']]={'raw_hash':raw_hash,'shingles':shingles,'critical':signals}
    groups=[{'id':identity('exact_group',key),'raw_content_hash':key,'member_ids':members,
        'type':'exact_body','member_origins':{member:origins[member] for member in members},
        'same_model_input_not_assumed':True} for key,members in sorted(exact.items()) if len(members)>1]
    write_rows(destination/'exact_groups.jsonl',groups)
    write_rows(destination/'near_pairs.jsonl',pairs)
    return {'exact_body_groups':len(groups),'exact_group_members':sum(len(g['member_ids']) for g in groups),
        'near_pairs':len(pairs),'candidate_lists_capped':capped,'candidate_limit':config['max_candidates_per_chunk'],
        'exhaustive_near_duplicate_detection':False,'sources_removed':0}


def prepare_dedup(prepared,config):
    """本地先执行必要去重，可在模型/数据库不可用时完成；构建时按哈希复用结果。"""
    manifest=read_json(prepared/'manifest.json')
    directory=prepared.parents[1]/'.cache/dedup'/manifest['prepared_id']
    directory.mkdir(parents=True,exist_ok=True)
    receipt=directory/'receipt.json'
    if receipt.exists():
        previous=read_json(receipt)
        if previous['config']!=config:
            raise ValueError('dedup configuration conflicts with prepared version')
        for name,checksum in previous['files'].items():
            if file_hash(directory/name)!=checksum:
                raise ValueError('dedup artifact changed')
        return directory,previous['summary']
    allowed={r['id'] for r in rows(prepared/'eligibility.jsonl') if r['status']=='included'}
    chunks=(c for c in rows(prepared/'chunks.jsonl') if c['id'] in allowed)
    metadata={d['id']:d['metadata'] for d in rows(prepared/'documents.jsonl')}
    summary=detect(chunks,directory,config,metadata)
    write_json(receipt,{'prepared_id':manifest['prepared_id'],'config':config,'summary':summary,
        'files':{name:file_hash(directory/name) for name in ('exact_groups.jsonl','near_pairs.jsonl')}})
    return directory,summary
