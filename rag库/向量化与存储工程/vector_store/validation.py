"""独立复读来源与两库成员；比较ID集合、payload、向量与真实来源，拒绝仅数总数。"""
from pathlib import Path
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


def validate_release(db,vectors,generation,prepared,cache,output):
    """全量scroll，不依赖抽样替代成员对账；失败时不授予ready或切换当前指针。"""
    release=db.connection.execute('SELECT * FROM index_releases WHERE generation_id=%s',(generation,)).fetchone()
    if not release:
        raise ValueError('unknown release')
    manifest=read_json(prepared/'manifest.json')
    source_check=validate_prepared(prepared,manifest)
    expected={str(r['point_id']):r for r in db.connection.execute('SELECT rr.*,er.input_hash,er.document_id,er.source_id,er.profile_id,er.data AS record_data FROM release_representations rr JOIN embedding_records er USING(representation_id) WHERE generation_id=%s',(generation,)).fetchall()}
    seen=set();issues=[];vector_checks=0
    planned={r['id']:r for r in rows(output/'representations.jsonl')}
    if {r['representation_id'] for r in expected.values()}!=set(planned):
        issues.append({'reason':'planned_representation_set_differs_from_postgres'})
    for row in expected.values():
        candidate=planned.get(row['representation_id'])
        if candidate is None or candidate['input_hash']!=row['input_hash'] or candidate['point_id']!=str(row['point_id']):
            issues.append({'reason':'planned_representation_mapping_mismatch','representation_id':row['representation_id']})
    documents={d['id']:d for d in rows(prepared/'documents.jsonl')}
    frozen=read_json(output/'manifest.json')
    media_path=Path(frozen['media_path'])
    visual_check=validate_visual_artifacts(prepared,media_path)
    frozen_sources={r['id']:r for name,folder in [('chunks',prepared),('media_sources',prepared),('visual_chunks',media_path)] for r in rows(folder/(name+'.jsonl'))}
    source_ids=list({r['source_id'] for r in expected.values()})
    source_objects={r['object_id']:r for r in db.connection.execute('SELECT object_id,document_id,data,data_hash FROM source_objects WHERE object_id=ANY(%s)',(source_ids,))}
    from .candidates import payload as expected_payload
    for row in expected.values():
        source=source_objects.get(row['source_id'])
        document=documents.get(row['document_id'])
        if not source or not document or source['document_id']!=row['document_id'] or digest(source['data'])!=source['data_hash'] or source['data']!=frozen_sources.get(row['source_id']):
            issues.append({'reason':'postgres_source_provenance_mismatch','representation_id':row['representation_id']})
        elif row['payload']!=expected_payload(row['record_data'],document,source['data'],generation):
            issues.append({'reason':'payload_differs_from_frozen_source_metadata','representation_id':row['representation_id']})
    # 原文父块、视觉父块和引用也独立比对冻结副本，防止“子块有point但父块回取错误”。
    from .importer import batched
    for table,folder,key in [('parents',prepared,'parent_id'),('visual_parents',media_path,'parent_id'),('relations',prepared,'relation_id'),('visual_extractions',media_path,'extraction_id')]:
        from psycopg import sql
        for batch in batched(rows(folder/(table+'.jsonl'))):
            expected_objects={r['id']:r for r in batch}
            query=sql.SQL('SELECT {},data,data_hash FROM {} WHERE {}=ANY(%s)').format(sql.Identifier(key),sql.Identifier(table),sql.Identifier(key))
            stored={r[key]:r for r in db.connection.execute(query,(list(expected_objects),))}
            if set(stored)!=set(expected_objects) or any(stored[k]['data']!=v or stored[k]['data_hash']!=digest(v) for k,v in expected_objects.items() if k in stored):
                issues.append({'reason':'postgres_frozen_object_mismatch','table':table})
    for name,checksum in frozen['media_hashes'].items():
        if file_hash(Path(frozen['media_path'])/name)!=checksum:
            issues.append({'reason':'visual_artifact_changed','file':name})
    for point in vectors.scan(release['collection']):
        key=str(point.id)
        if key in seen:
            issues.append({'point_id':key,'reason':'duplicate_point'})
        seen.add(key)
        row=expected.get(key)
        if row is None:
            issues.append({'point_id':key,'reason':'unexpected_point'})
            continue
        cached=cache.get(row['input_hash'])
        if cached is None:
            issues.append({'point_id':key,'reason':'missing_cache'})
            continue
        vector,meta=cached
        for error in compare_point(point,row['payload'],vector):
            issues.append({'point_id':key,'reason':error})
        if row['profile_id']!=release['profile_id'] or digest(row['payload'])!=row['payload_hash']:
            issues.append({'point_id':key,'reason':'profile_or_payload_hash'})
        vector_checks+=1
        if vector_checks%5000==0:
            print('validated vectors',vector_checks,flush=True)
    for key in set(expected)-seen:
        issues.append({'point_id':key,'reason':'missing_point'})
    unfinished=db.connection.execute("SELECT count(*) AS n FROM index_tasks WHERE generation_id=%s AND status!='ready'",(generation,)).fetchone()['n']
    prepared_ids={d['id'] for d in rows(prepared/'documents.jsonl')}
    released_ids={r['document_id'] for r in db.connection.execute('SELECT document_id FROM release_documents WHERE generation_id=%s',(generation,))}
    if released_ids!=prepared_ids:
        issues.append({'reason':'release_document_set_mismatch'})
    for member in db.connection.execute('SELECT document_id,metadata_snapshot FROM release_documents WHERE generation_id=%s',(generation,)):
        if member['document_id'] not in documents or member['metadata_snapshot']!=documents[member['document_id']]['metadata']:
            issues.append({'reason':'release_metadata_differs_from_frozen_document','document_id':member['document_id']})
    db.connection.commit()
    result={'generation_id':generation,'checked_at':utc_now(),'source':source_check,'visual':visual_check,
        'expected_points':len(expected),'actual_points':len(seen),'vectors_checked':vector_checks,
        'unfinished_tasks':unfinished,'issues':issues,
        'passed':bool(source_check['passed'] and visual_check['passed'] and not issues and not unfinished),
        'semantic_correctness_claimed':False}
    write_json(output/'validation.json',result)
    return result
