"""本地查询、来源/父块回取及独立校验；不直接依赖SQLite SQL或Qdrant SDK。"""
import json
from pathlib import Path
from .common import read_json,write_json,rows,paths,digest,secrets
from .embedding import Cache,Counter,Service
from .storage import open_stores
from .contracts import FILTER_KEYS
from .validation import validate_release


def active_manifest(db,project):
    """SQLite为发布权威；JSON指针只是便于查阅的副本，意外中断后不依赖它读库。"""
    active=db.active()
    if not active:raise ValueError('no active local release; run migrate or build first')
    release=db.release(active['generation'])
    if release['status']!='ready':raise ValueError('active release is not ready')
    return active,json.loads(release['manifest']),project/'outputs'/active['generation']


def status(project):
    config=read_json(project/'config.json');db,vectors,lexical=open_stores(project,config)
    try:
        active=db.active()
        if not active:return {'status':'not_activated','backend':config['storage']}
        _,manifest,output=active_manifest(db,project)
        return {'status':'active','runtime':active,'backend':config['storage'],
            'points':vectors.count(manifest['collection']),
            'embedding':read_json(output/'embedding_summary.json')}
    finally:lexical.close();vectors.close();db.close()


def verify(project):
    config=read_json(project/'config.json');db,vectors,lexical=open_stores(project,config)
    try:
        _,manifest,output=active_manifest(db,project)
        return validate_release(db,vectors,lexical,manifest,output,Cache(manifest['profile_id'],project/'.cache/vectors'/manifest['profile_id']))
    finally:lexical.close();vectors.close();db.close()


def question_vector(project,manifest,text):
    """仍调用原Qwen接口；问题缓存与文档缓存分开，关键词查询不调用模型。"""
    counter=Counter(project)
    if counter.profile!=manifest['profile']['local_processor_profile']:raise ValueError('query tokenizer profile differs from stored vectors')
    rendered,tokens=counter.encode(text)
    if len(tokens)>8192:raise ValueError('query exceeds embedding input limit')
    description={'type':'text','token_ids':tokens,'expected_tokens':len(tokens),'rendered_hash':digest(rendered)}
    cache=Cache(manifest['profile_id'],project/'.cache/query_vectors'/manifest['profile_id']);key=digest(description)
    old=cache.get(key)
    if old:return old[0],True
    private=secrets()
    if private['embedding_base_url']!=manifest['profile']['service_url']:raise ValueError('query service URL changed; new vector profile required')
    service=Service(private,manifest['profile']['service_model']['id'])
    try:
        from .pipeline import verify_service_space
        verify_service_space(service,counter,manifest['profile_id'])
        values,usage=service.text_batch([tokens]);value,_=cache.put(key,values[0],usage)
        return value,False
    finally:service.close()


def query(project,text,limit=5,mode='hybrid',filters=None):
    """Dense与BM25先分别过滤，再按RRF融合；同来源多表示在最终展示时去重。"""
    filters=filters or {}
    if set(filters)-FILTER_KEYS:raise ValueError('unsupported filter field')
    if mode not in ('hybrid','dense','bm25'):raise ValueError('unknown retrieval mode')
    if not 1<=limit<=100:raise ValueError('limit must be between 1 and 100')
    config=read_json(project/'config.json');db,vectors,lexical=open_stores(project,config)
    try:
        active,manifest,output=active_manifest(db,project)
        members={r['record']['id']:r for r in db.members(active['generation'])}
        allowed={rid for rid,r in members.items() if all(r['payload'].get(k)==v for k,v in filters.items())}
        if not allowed:return {'generation':active['generation'],'results':[],'query_cache_hit':None}
        channels={};cache_hit=None;candidate_count=max(20,limit*4)
        if mode in ('dense','hybrid'):
            vector,cache_hit=question_vector(project,manifest,text)
            channels['dense']=vectors.search(manifest['collection'],vector,candidate_count,filters)
        if mode in ('bm25','hybrid'):
            lexical.load(active['generation']);channels['bm25']=lexical.search(text,candidate_count,allowed)
        scores={};ranks={}
        for name,hits in channels.items():
            for rank,hit in enumerate(hits,1):
                rid=hit['id']
                if rid not in allowed:raise ValueError('index returned disallowed or unknown source')
                scores[rid]=scores.get(rid,0)+1/(config.get('rrf_k',60)+rank)
                ranks.setdefault(rid,{})[name]={'rank':rank,'score':hit['score']}
        results=[];seen=set()
        for rid in sorted(scores,key=lambda r:(-scores[r],r)):
            row=members[rid];record=row['record'];source_id=record['source_id']
            if source_id in seen:continue
            seen.add(source_id);source=db.object(source_id);parent_id=row['payload'].get('parent_id')
            results.append({'representation_id':rid,'rrf_score':scores[rid],'channels':ranks[rid],
                'record':record,'payload':row['payload'],'source':source,
                'parent':db.object(parent_id) if parent_id else None,'document':db.document(record['document_id']),
                'related':db.related([x for x in (source_id,parent_id) if x])})
            if len(results)>=limit:break
        return {'generation':active['generation'],'mode':mode,'filters':filters,'query_cache_hit':cache_hit,'results':results}
    finally:lexical.close();vectors.close();db.close()


def smoke(project):
    """使用已存真实向量与原题名检查Local持久化、BM25过滤及父块回取，不算准确率。"""
    config=read_json(project/'config.json');db,vectors,lexical=open_stores(project,config)
    try:
        active,manifest,output=active_manifest(db,project);members=list(db.members(active['generation']))
        lexical.load(active['generation']);cache=Cache(manifest['profile_id'],project/'.cache/vectors'/manifest['profile_id']);checks=[]
        for planid in config['sample_planids']:
            row=next(r for r in members if r['payload']['planid']==planid and r['record']['source_type']=='text_chunk')
            vector,_=cache.get(row['record']['input_hash'])
            hits=vectors.search(manifest['collection'],vector,5,{'planid':planid})
            assert hits and hits[0]['score']>.999
            allowed={r['record']['id'] for r in members if r['payload']['planid']==planid}
            doc=db.document(row['record']['document_id'])
            words=lexical.search(doc['metadata']['title'],5,allowed)
            assert words and all(w['id'] in allowed and w['score']>0 for w in words)
            parent=db.object(row['payload']['parent_id']);assert parent['document_id']==doc['id']
            checks.append({'planid':planid,'dense_self_score':hits[0]['score'],'bm25_positive_filtered':len(words),'parent_id':parent['id'],'passed':True})
        complete=[r for r in members if r['record'].get('context_parent_id')]
        assert len(complete)==2
        for row in complete:
            parent=db.object(row['payload']['parent_id'])
            assert '确保' in ''.join(''.join(s['text'] for s in parent['source_segments']).split())
        result={'passed':True,'checks':checks,'supplemental_parent_contexts_checked':len(complete),'models_called':0,'retrieval_quality_evaluation':False}
        write_json(output/'storage_smoke.json',result);return result
    finally:lexical.close();vectors.close();db.close()


def probe(project):
    """少量真实模型请求确认向量化入口仍可用，不写生产向量集合。"""
    config=read_json(project/'config.json');counter=Counter(project);private=secrets();service=Service(private,config['model'])
    try:
        models=service.request('/models');profile=read_json(project/'当前批次.json')['profile_id']
        from .pipeline import verify_service_space
        from .probe import visual_protocol
        result=verify_service_space(service,counter,profile)
        pointer=read_json(project/'当前批次.json');manifest=read_json(project/pointer['run']/'manifest.json')
        result['visual_embedding']=visual_protocol(service,counter,Path(manifest['prepared_path']),config)
        result.update(model=config['model'],dimensions=4096,models_advertised=len(models['data']),http_calls=service.calls,passed=True)
        write_json(project/'接口探测记录.json',result);return result
    finally:service.close()
