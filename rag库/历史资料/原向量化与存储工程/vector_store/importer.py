"""把冻结来源导入有外键的PostgreSQL；原文对象和识别派生对象分开登记。"""
from psycopg.types.json import Jsonb
from .common import rows,digest,identity


def batched(values,size=500):
    batch=[]
    for value in values:
        batch.append(value)
        if len(batch)>=size:
            yield batch;batch=[]
    if batch:
        yield batch


def object_row(record,kind,document_id=None):
    return {'object_id':record['id'],'document_id':document_id if document_id is not None else record.get('document_id'),
            'kind':kind,'data':record,'data_hash':digest(record)}


def import_sources(db,prepared):
    docs=list(rows(prepared/'documents.jsonl'))
    db.immutable('document_versions',[{'document_id':d['id'],
        'document_version_id':identity('document_version',d['planid'],d['source_hash']),
        'planid':d['planid'],'source_hash':d['source_hash'],'snapshot_path':d['snapshot'],
        'data':d,'data_hash':digest(d)} for d in docs])
    db.immutable('source_objects',[object_row(d,'document',d['id']) for d in docs])
    # 基础单元与结构在主库保存为可执行引用的对象，不能只有一条JSONL路径。
    for table in ('units','structures','parents','chunks'):
        for batch in batched(rows(prepared/(table+'.jsonl'))):
            db.immutable('source_objects',[object_row(r,table.rstrip('s')) for r in batch])
            if table=='parents':
                db.immutable('parents',[{'parent_id':r['id'],'document_id':r['document_id'],
                    'source_start':r['start'],'source_end':r['end'],'data':r,'data_hash':digest(r)} for r in batch])
            elif table=='chunks':
                db.immutable('chunks',[{'chunk_id':r['id'],'parent_id':r['parent_id'],'document_id':r['document_id'],
                    'status':r['status'],'data':r,'data_hash':digest(r)} for r in batch])
    if (prepared/'parent_contexts.jsonl').exists():
        for batch in batched(rows(prepared/'parent_contexts.jsonl')):
            db.immutable('source_objects',[object_row(r,'parent_context') for r in batch])
            db.immutable('parents',[{'parent_id':r['id'],'document_id':r['document_id'],
                'source_start':r['start'],'source_end':r['end'],'data':r,'data_hash':digest(r)} for r in batch])
    for batch in batched(rows(prepared/'media_assets.jsonl')):
        db.immutable('media_assets',[{'asset_id':r['id'],'sha256':r['sha256'],'data':r,'data_hash':digest(r)} for r in batch])
        db.immutable('source_objects',[object_row(r,'media_asset') for r in batch])
    for batch in batched(rows(prepared/'media_sources.jsonl')):
        db.immutable('media_occurrences',[{'occurrence_id':r['id'],'document_id':r['document_id'],
            'asset_id':r.get('asset_id'),'data':r,'data_hash':digest(r)} for r in batch])
        db.immutable('source_objects',[object_row(r,'media_occurrence') for r in batch])
    for batch in batched(rows(prepared/'relations.jsonl')):
        records=[]
        for r in batch:
            source=r.get('from_chunk_id') or r.get('from_parent_id') or r.get('source_id')
            if not source:
                raise ValueError('relation lacks explicit source identity')
            target=r.get('target_id') if r['status']=='resolved' else None
            if r['status']=='resolved' and not target:
                raise ValueError('resolved relation lacks target')
            records.append({'relation_id':r['id'],'document_id':r['document_id'],'source_id':source,
                'target_id':target,'resolution_status':r['status'],'data':r,'data_hash':digest(r)})
        db.immutable('relations',records)
    return {d['id']:d for d in docs}


def import_visual(db,extractions,parents,chunks):
    for batch in batched(extractions):
        db.immutable('visual_extractions',[{'extraction_id':r['id'],'asset_id':r['asset_id'],
            'profile_id':r['profile_id'],'status':r['status'],'data':r,'data_hash':digest(r)} for r in batch])
    regions=[]
    for extraction in extractions:
        for region in extraction['regions']:
            record={'region_id':identity('visual_region',extraction['id'],region['index'],region['bbox']),
                'asset_id':extraction['asset_id'],'extraction_id':extraction['id'],
                'x0':region['bbox'][0],'y0':region['bbox'][1],'x1':region['bbox'][2],'y1':region['bbox'][3],
                'width':region['original_size'][0],'height':region['original_size'][1],
                'data':region,'data_hash':digest(region)}
            regions.append(record)
    for batch in batched(regions):
        db.immutable('visual_regions',batch)
    for batch in batched(parents):
        db.immutable('visual_parents',[{'parent_id':r['id'],'occurrence_id':r['occurrence_id'],
            'document_id':r['document_id'],'data':r,'data_hash':digest(r)} for r in batch])
        db.immutable('source_objects',[object_row(r,'visual_parent') for r in batch])
    for batch in batched(chunks):
        db.immutable('visual_chunks',[{'chunk_id':r['id'],'parent_id':r['parent_id'],
            'document_id':r['document_id'],'region_id':r['region_id'],'data':r,'data_hash':digest(r)} for r in batch])
        db.immutable('source_objects',[object_row(r,'visual_chunk') for r in batch])


def register_release_documents(db,generation,docs):
    with db.connection.transaction():
        with db.connection.cursor() as cursor:
            cursor.executemany('INSERT INTO release_documents(generation_id,document_id,metadata_snapshot) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING',
                [(generation,d['id'],Jsonb(d['metadata'])) for d in docs.values()])
