"""独立复读来源与三存储成员；逐项比较ID、payload、向量与来源，拒绝仅数总数。"""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from itertools import islice
import numpy as np
from .common import rows,read_json,file_hash,digest,validate_segments,write_json,utc_now,long_path


def validate_prepared(prepared,manifest):
    errors=[];checked=0
    for name,expected in manifest['frozen_files'].items():
        if file_hash(prepared/name)!=expected:
            errors.append('frozen file changed: '+name)
    docs={d['id']:d for d in rows(prepared/'documents.jsonl')}
    sources={}
    for did,doc in docs.items():
        raw=long_path(prepared/doc['snapshot']).read_bytes()
        if digest(raw)!=doc['source_hash']:
            errors.append('snapshot changed: '+doc['planid'])
        sources[did]=raw.decode('utf-8')
    cursors={did:0 for did in docs}
    for unit in rows(prepared/'units.jsonl'):
        did=unit['document_id']
        if unit['start']!=cursors[did] or sources[did][unit['start']:unit['end']]!=unit['text']:
            errors.append('unit gap/overlap/text mismatch: '+unit['id'])
        cursors[did]=unit['end']
    for did,cursor in cursors.items():
        if cursor!=len(sources[did]):
            errors.append('unit coverage incomplete: '+did)
    parents={p['id']:p for p in rows(prepared/'parents.jsonl')}
    if (prepared/'parent_contexts.jsonl').exists():
        for context in rows(prepared/'parent_contexts.jsonl'):
            validate_segments(sources[context['document_id']],context['source_segments'])
            checked+=len(context['source_segments'])
            for pid in context['member_parent_ids']:
                p=parents.get(pid)
                if not p or p['document_id']!=context['document_id'] or not context['start']<=p['start']<p['end']<=context['end']:
                    errors.append('supplemental parent membership invalid: '+context['id'])
    for table in ('parents','chunks'):
        for item in rows(prepared/(table+'.jsonl')):
            segments=item['source_segments']+item.get('context_segments',[])
            validate_segments(sources[item['document_id']],segments)
            checked+=len(segments)
            if table=='chunks':
                parent=parents.get(item['parent_id'])
                if not parent or parent['document_id']!=item['document_id'] or any(not parent['start']<=s['start']<s['end']<=parent['end'] for s in item['source_segments']):
                    errors.append('parent/source membership invalid: '+item['id'])
    for asset in rows(prepared/'media_assets.jsonl'):
        path=prepared/'media'/asset['sha256']/Path(asset['paths'][0]).name
        if file_hash(path)!=asset['sha256']:
            errors.append('media snapshot changed: '+asset['id'])
    return {'passed':not errors,'errors':errors,'source_segments_checked':checked,'documents':len(docs)}


def compare_point(point,payload,vector):
    """可独立单测的逐点检查：Qdrant的Cosine归一化只允许float32数值误差。"""
    errors=[]
    if point.payload!=payload:
        errors.append('payload_mismatch')
    stored=point.vector.get('dense') if isinstance(point.vector,dict) else None
    if stored is None:
        errors.append('dense_missing')
    else:
        array=np.asarray(stored,dtype=np.float32)
        if array.shape!=(4096,) or not np.isfinite(array).all() or not np.allclose(array,vector,rtol=2e-5,atol=2e-6):
            errors.append('vector_mismatch')
    return errors


def validate_visual_artifacts(prepared,media_path):
    """重新核对原图区域与转录范围；哈希一致只证明未变化，不能替代结构检查。"""
    assets={a['id']:a for a in rows(prepared/'media_assets.jsonl')}
    occurrences={o['id']:o for o in rows(prepared/'media_sources.jsonl')}
    extractions={e['id']:e for e in rows(media_path/'media_extractions.jsonl')}
    parents={p['id']:p for p in rows(media_path/'visual_parents.jsonl')}
    errors=[];checked=0
    for extraction in extractions.values():
        asset=assets.get(extraction['asset_id'])
        if not asset or digest(extraction['raw_output'])!=extraction['raw_output_hash']:
            errors.append('visual extraction source/output mismatch');continue
        for region in extraction['regions']:
            x0,y0,x1,y1=region['bbox']
            if not 0<=x0<x1<=asset['width'] or not 0<=y0<y1<=asset['height'] or region['original_size']!=[asset['width'],asset['height']]:
                errors.append('visual region outside original image')
    for parent in parents.values():
        occurrence=occurrences.get(parent['occurrence_id'])
        extraction=extractions.get(parent['extraction_id'])
        if not occurrence or not extraction or occurrence['document_id']!=parent['document_id'] or occurrence.get('asset_id')!=parent['asset_id'] or extraction['asset_id']!=parent['asset_id']:
            errors.append('visual parent provenance mismatch');continue
        if parent['transcript']!='\n\n'.join(r['text'] for r in extraction['regions']) or digest(parent['transcript'])!=parent['transcript_hash']:
            errors.append('visual parent transcript mismatch')
    for chunk in rows(media_path/'visual_chunks.jsonl'):
        checked+=1;parent=parents.get(chunk['parent_id'])
        if not parent or any(chunk[k]!=parent[k] for k in ('document_id','occurrence_id','asset_id','extraction_id')):
            errors.append('visual chunk parent/source mismatch');continue
        extraction=extractions.get(chunk['extraction_id'])
        region=next((r for r in extraction['regions'] if r['index']==chunk['region_index']),None) if extraction else None
        start,end=chunk['derived_start'],chunk['derived_end']
        if not region or chunk['text']!=region['text'] or chunk['bbox']!=region['bbox'] or not 0<=start<end<=len(parent['transcript']) or parent['transcript'][start:end]!=chunk['text']:
            errors.append('visual chunk range/region mismatch')
        if region and ((chunk['status']=='ready')!=(region['status']=='recognized')):
            errors.append('visual uncertainty status mismatch')
        for segment in chunk['context_segments']:
            if not 0<=segment['start']<=segment['end']<=len(parent['transcript']) or parent['transcript'][segment['start']:segment['end']]!=segment['text']:
                errors.append('visual context range mismatch')
    return {'passed':not errors,'errors':errors,'extractions':len(extractions),'parents':len(parents),'chunks_checked':checked}


def validate_release(db,vectors,lexical,manifest,output,cache):
    """核对冻结来源、SQLite成员、每条本地向量及BM25有序成员；不只核对总数。"""
    from .coverage import validate_coverage
    prepared=Path(manifest['prepared_path']);media=Path(manifest['media_path']);generation=manifest['generation_id']
    source=validate_prepared(prepared,read_json(prepared/'manifest.json'))
    visual=validate_visual_artifacts(prepared,media);coverage=validate_coverage(prepared,output,media)
    expected={r['record']['point_id']:r for r in db.members(generation)}
    planned={r['point_id']:r for r in rows(output/'representations.jsonl')}
    owners=db.object_owners()
    issues=[];seen=set();checked=0
    if set(expected)!=set(planned):issues.append('SQLite/planned member set differs')
    def cached(point):
        row=expected.get(str(point.id))
        return cache.get(row['record']['input_hash']) if row else None
    # 数万小文件只做有限并发预读，每次最多128条，不把全部向量/任务常驻内存。
    # SDK扫描、SQLite访问和结果汇总都留在主线程，不放宽数据库的线程约束。
    with ThreadPoolExecutor(max_workers=8) as executor:
        stream=iter(vectors.scan(manifest['collection']));progress=0
        while batch:=list(islice(stream,128)):
            for point,entry in zip(batch,executor.map(cached,batch)):
                pid=str(point.id);seen.add(pid);row=expected.get(pid)
                if row is None:issues.append('unplanned vector: '+pid);continue
                r=row['record']
                if r!=planned.get(pid):issues.append('SQLite representation differs from frozen plan: '+pid)
                if entry is None:issues.append('missing input cache: '+pid);continue
                errors=compare_point(point,row['payload'],entry[0])
                if errors:issues.append({'id':pid,'errors':errors})
                if owners.get(r['source_id'])!=r['document_id']:issues.append('source identity mismatch: '+pid)
                parent=row['payload'].get('parent_id')
                if parent and owners.get(parent)!=r['document_id']:issues.append('parent identity mismatch: '+pid)
                checked+=1
            if checked//5000>progress:
                progress=checked//5000;print(f'local vectors verified: {checked}/{len(expected)}',flush=True)
    if seen!=set(expected):issues.append('local vector member set differs')
    lexical_documents=[(row['record']['id'],row['text']) for row in db.members(generation) if row['text'] is not None]
    lexical_check=lexical.validate(generation,lexical_documents)
    if db.unfinished(generation):issues.append('unfinished embedding tasks')
    integrity=db.integrity()
    result={'generation_id':generation,'passed':not issues and source['passed'] and visual['passed'] and coverage['passed'] and integrity,
        'issues':issues,'source':source,'visual':visual,'coverage':coverage,'sqlite_integrity':integrity,
        'vectors_checked':checked,'lexical':lexical_check,'unfinished_tasks':db.unfinished(generation),'at':utc_now()}
    write_json(output/'validation.json',result)
    return result
