"""将冻结来源导入SQLite接口；所有父子/引用目标先落库，再登记外键关系。"""
from .common import rows,identity


def import_sources(db,prepared,media):
    docs=list(rows(prepared/'documents.jsonl'));db.put_documents(docs)
    for name,kind in [('units','unit'),('structures','structure'),('parents','parent'),('chunks','chunk'),
                      ('media_assets','media_asset'),('media_sources','media_occurrence'),('parent_contexts','parent_context')]:
        if (prepared/(name+'.jsonl')).exists():db.put_objects(kind,rows(prepared/(name+'.jsonl')))
    db.put_objects('relation',rows(prepared/'relations.jsonl'))
    for name,kind in [('media_extractions','visual_extraction'),('visual_parents','visual_parent'),('visual_chunks','visual_chunk')]:
        db.put_objects(kind,rows(media/(name+'.jsonl')))
    regions=[]
    for e in rows(media/'media_extractions.jsonl'):
        for r in e['regions']:
            regions.append({'id':identity('visual_region',e['id'],r['index'],r['bbox']),
                'asset_id':e['asset_id'],'extraction_id':e['id'],'region':r})
    db.put_objects('visual_region',regions)
    db.register_links(rows(prepared/'chunks.jsonl'),rows(prepared/'relations.jsonl'))
    db.register_links(rows(media/'visual_chunks.jsonl'),[])
    return {d['id']:d for d in docs}
