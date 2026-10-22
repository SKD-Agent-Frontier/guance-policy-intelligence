"""单worker构建：冻结→真实OCR→去重→输入/缓存→双库→全量对账→事务发布。

网络调用与两库不是一个事务；每个唯一输入有持久任务和原子向量缓存。模型成功
但缓存尚未封存时断电，恢复可能重复调用；不承诺跨外部服务exactly-once。
"""
from collections import Counter as Counts,defaultdict
from pathlib import Path
import traceback
import numpy as np
from psycopg.types.json import Jsonb
from .common import SHARED,paths,read_json,rows,write_json,write_rows,digest,identity,file_hash,utc_now,secrets,redact
from .prepare import prepare
from .embedding import Counter,Service,Cache,embedding_profile
from .candidates import process_media,make_candidates,payload,messages_from_description
from .dedup import prepare_dedup
from .importer import import_sources,import_visual,register_release_documents
from .storage import Database,Vectors
from .validation import validate_prepared,validate_release


def verify_service_space(service,counter,profile_id):
    """服务不暴露权重revision，记录未知；固定探针防止明显换模型后误用旧缓存。"""
    _,tokens=counter.encode('政策检索向量空间校验。')
    vectors,usage=service.text_batch([tokens])
    path=SHARED/'.cache/service-canaries'/(profile_id+'.json')
    vector=vectors[0]
    existed=path.exists()
    if existed:
        old=read_json(path)
        reference=np.asarray(old['vector'],dtype=np.float32)
        if float(np.dot(reference,vector))<.99999:
            raise ValueError('service vector space changed; new remote profile required')
    else:
        write_json(path,{'profile_id':profile_id,'vector':vector.tolist(),'token_count':len(tokens),'usage':usage})
    return {'token_count':len(tokens),'usage':usage,'canary_compared':existed,'first_probe_recorded':not existed,
        'remote_weight_revision_verified':False}


def embed_inputs(db,vectors,generation,collection,candidates,cache,service,counter,docs,config,output,fault_after=None):
    grouped=defaultdict(list)
    for candidate in candidates:
        grouped[candidate['record']['input_hash']].append(candidate)
    for members in grouped.values():
        first=members[0]['description']
        if any(c['description']!=first for c in members):
            raise ValueError('input hash collision')
        for c in members:
            record=c['record']
            c['payload']=payload(record,docs[record['document_id']],c['source'],generation)
            db.register_representation(generation,record,c['description'],c['payload'])
    statistics={'unique_inputs':len(grouped),'representations':len(candidates),'cache_hits':0,
        'new_input_vectors':0,'embedding_requests':0,'failed_inputs':0}
    pending=[];pending_tokens=0;failures=[];points=[];completed=0

    def store_result(input_hash,vector,metadata,cached):
        nonlocal completed,points
        db.immutable('embedding_cache',[{'profile_id':cache.profile_id,'input_hash':input_hash,
            'checksum':metadata['checksum'],'path':metadata['path'],'dimensions':metadata['dimensions'],
            'data':metadata,'data_hash':digest(metadata)}])
        for member in grouped[input_hash]:
            points.append((member['record'],vector,member['payload']))
        if len(points)>=128:
            vectors.upsert(collection,points);points=[]
        # 缓存和point均可按相同身份重放。Qdrant写入成功但任务未ready时，中断不增ID。
        db.task(generation,input_hash,'ready')
        statistics['cache_hits' if cached else 'new_input_vectors']+=1
        completed+=1
        if completed%100==0:
            write_json(output/'progress.json',{**statistics,'completed_unique_inputs':completed,'at':utc_now()})
            print(f'embedded/cache {completed}/{len(grouped)}, failed={len(failures)}',flush=True)
        if fault_after and completed>=fault_after:
            raise InterruptedError('requested fault injection after cache checkpoint')

    def flush():
        nonlocal pending,pending_tokens
        if not pending:
            return
        batch=list(pending);pending=[];pending_tokens=0
        for key,description in batch:
            db.task(generation,key,'running')
        try:
            values,usage=service.text_batch([description['token_ids'] for _,description in batch])
            statistics['embedding_requests']+=1
        except Exception as error:
            for key,_ in batch:
                db.task(generation,key,'failed',str(error)[:600])
                failures.append({'input_hash':key,'reason':str(error)[:600]})
            return
        for (key,_),vector in zip(batch,values):
            vector,metadata=cache.put(key,vector,usage)
            store_result(key,vector,metadata,False)

    for key,members in grouped.items():
        description=members[0]['description']
        cached=cache.get(key)
        if cached:
            vector,metadata=cached
            store_result(key,vector,metadata,True)
            continue
        if description['type']=='text':
            if pending and (len(pending)>=config['batch_size'] or pending_tokens+description['expected_tokens']>config['batch_token_limit']):
                flush()
            pending.append((key,description));pending_tokens+=description['expected_tokens']
        else:
            flush();db.task(generation,key,'running')
            try:
                messages=messages_from_description(description,counter)
                vector,usage=service.vision_embedding(messages,description['expected_tokens'])
                statistics['embedding_requests']+=1
            except Exception as error:
                db.task(generation,key,'failed',str(error)[:600])
                failures.append({'input_hash':key,'reason':str(error)[:600]})
                continue
            vector,metadata=cache.put(key,vector,usage)
            store_result(key,vector,metadata,False)
    flush()
    if points:
        vectors.upsert(collection,points)
    statistics['failed_inputs']=len(failures)
    statistics['service_http_attempts_including_probes_and_retries']=service.calls
    write_rows(output/'embedding_failures.jsonl',failures)
    write_json(output/'embedding_summary.json',statistics)
    return statistics


def build(project,sample=False,allow_partial=False,fault_after=None):
    config,upstream,_=paths(project)
    prepared,manifest=prepare(project,sample)
    check=validate_prepared(prepared,manifest)
    if not check['passed']:
        raise ValueError('prepared source validation failed')
    counter=Counter(upstream)
    private=secrets();service=Service(private,config['model'],config['dimensions'])
    db=None;vectors=None
    try:
        models=service.request('/models')
        visual_model=next((m for m in models['data'] if m['id']==config['vision_model']),None)
        if visual_model is None:
            raise ValueError('requested visual model not advertised by service')
        config={**config,'vision_service_url':private['embedding_base_url'],'vision_model_advertisement':visual_model}
        profile_id,profile=embedding_profile(config,counter,models,private['embedding_base_url'])
        # 在长时间OCR/向量调用前确认两库可用，并固定发布revision。不能等到发布末尾
        # 才读取最新revision，否则旧构建可能覆盖构建期间别人刚作出的发布选择。
        db=Database(private,config['postgres_schema']);vectors=Vectors(private)
        vectors.client.get_collections()
        db.immutable('embedding_profiles',[{'profile_id':profile_id,'data':profile,'data_hash':digest(profile)}])
        initial_runtime=db.connection.execute('SELECT revision FROM rag_runtime WHERE knowledge_base_id=%s',(config['knowledge_base_id'],)).fetchone()
        expected_runtime_revision=initial_runtime['revision'] if initial_runtime else 0
        db.connection.commit()
        probe=verify_service_space(service,counter,profile_id)
        from .probe import visual_protocol
        probe['visual']=visual_protocol(service,counter,prepared,config)
        write_json(Path(project)/'.cache/latest-probe.json',probe)
        # 每次prepare身份对应唯一media工作目录；OCR仍按实际资源与profile全局共享。
        media_dir=Path(project)/'.cache/media-work'/digest({'prepared':manifest['prepared_id'],'ocr_model':config['vision_model']})[:24]
        media_dir.mkdir(parents=True,exist_ok=True)
        extractions,visual_parents,visual_chunks,media_summary=process_media(prepared,media_dir,config,service)
        # cache命中数是运行统计，不能参与内容身份，否则恢复会无故生成新generation。
        media_files=('media_extractions.jsonl','visual_parents.jsonl','visual_chunks.jsonl','media_failures.jsonl','media_excluded.jsonl')
        media_hashes={name:file_hash(media_dir/name) for name in media_files}
        # 部分OCR重试成功会产生新的内容版本，不能回写旧release引用的识别文件。
        frozen_media=Path(project)/'outputs'/('media_'+digest(media_hashes)[:24])
        frozen_media.mkdir(exist_ok=True)
        from .common import atomic_bytes
        for name,checksum in media_hashes.items():
            target=frozen_media/name
            if target.exists() and file_hash(target)!=checksum:
                raise ValueError('immutable visual artifact conflict')
            if not target.exists():
                atomic_bytes(target,(media_dir/name).read_bytes())
        media_dir=frozen_media
        frozen={'prepared_id':manifest['prepared_id'],'prepared_path':str(prepared),'profile_id':profile_id,
            'profile':profile,'media_hashes':media_hashes,'media_path':str(media_dir),
            'allow_partial':allow_partial,'scope':config['scope']}
        generation=identity('generation',frozen)
        collection=config['collection_prefix']+'_'+digest(generation)[:20]
        output=Path(project)/'outputs'/generation;output.mkdir(exist_ok=True)
        write_json(output/'manifest.json',{**frozen,'generation_id':generation,'collection':collection})
        write_json(output/'service_probe.json',probe)
        write_json(output/'media_summary.json',media_summary)
        docs={d['id']:d for d in rows(prepared/'documents.jsonl')}
        candidates,excluded=make_candidates(prepared,output,config,counter,profile_id,docs,visual_chunks,extractions)
        write_rows(output/'lexical_members.jsonl',({'representation_id':c['record']['id'],'source_id':c['record']['source_id'],
            'source_type':c['record']['source_type'],'input_hash':c['record']['input_hash']} for c in candidates if c['record']['representation_type']=='text'))
        dedup_dir,dedup=prepare_dedup(prepared,config['dedup'])
        for name in ('exact_groups.jsonl','near_pairs.jsonl'):
            atomic_bytes(output/name,(dedup_dir/name).read_bytes())
        write_json(output/'dedup_summary.json',dedup)
        with db.worker_lock(generation):
            previous=db.begin_release(generation,profile_id,collection,frozen)
            cache=Cache(profile_id)
            if previous=='ready':
                verification=validate_release(db,vectors,generation,prepared,cache,output)
                if not verification['passed']:
                    raise ValueError('previous ready release no longer validates')
                return output,verification
            if previous=='validating':
                with db.connection.transaction():
                    db.connection.execute("UPDATE index_releases SET status='building' WHERE generation_id=%s",(generation,))
            docs=import_sources(db,prepared)
            import_visual(db,extractions,visual_parents,visual_chunks)
            register_release_documents(db,generation,docs)
            with db.connection.transaction():
                with db.connection.cursor() as cursor:
                    exclusions=[(generation,r['id'],r['reason'],Jsonb(r)) for r in rows(prepared/'eligibility.jsonl') if r['status']=='excluded']
                    exclusions += [(generation,r['source_id'],r['reason'],Jsonb(r)) for r in excluded]
                    exclusions += [(generation,r['occurrence_id'],r['reason'],Jsonb(r)) for r in rows(media_dir/'media_excluded.jsonl')]
                    cursor.executemany('INSERT INTO release_exclusions(generation_id,source_id,reason,data) VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING',exclusions)
                    warnings=[(generation,r['id'],r['quality_warning'],Jsonb(r)) for r in rows(prepared/'eligibility.jsonl') if r.get('quality_warning')]
                    cursor.executemany('INSERT INTO release_warnings(generation_id,source_id,reason,data) VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING',warnings)
                for filename,table,key in [('near_pairs.jsonl','dedup_pairs','pair_id'),('exact_groups.jsonl','dedup_groups','group_id')]:
                    from psycopg import sql
                    query=sql.SQL('INSERT INTO {}(generation_id,{},data) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING').format(sql.Identifier(table),sql.Identifier(key))
                    with db.connection.cursor() as cursor:
                        cursor.executemany(query,[(generation,r['id'],Jsonb(r)) for r in rows(output/filename)])
            vectors.create(collection)
            statistics=embed_inputs(db,vectors,generation,collection,candidates,cache,service,counter,docs,config,output,fault_after)
            if statistics['failed_inputs']:
                raise RuntimeError('embedding failures remain; release is not activated')
            incomplete_media=media_summary['recognition_failures'] or media_summary['uncertain_visual_chunks'] or any(r['reason']!='blank_image_has_no_policy_content' for r in excluded)
            if incomplete_media and not allow_partial:
                raise RuntimeError('media/representation gaps remain; use explicit --allow-partial only after inspecting reports')
            with db.connection.transaction():
                db.connection.execute("UPDATE index_releases SET status='validating' WHERE generation_id=%s",(generation,))
            result=validate_release(db,vectors,generation,prepared,cache,output)
            if not result['passed']:
                raise ValueError('release reconciliation failed')
            with db.connection.transaction():
                db.connection.execute("UPDATE index_releases SET status='ready',validated_at=now(),counts=%s WHERE generation_id=%s",(Jsonb(statistics),generation))
            revision=db.activate(config['knowledge_base_id'],generation,expected_runtime_revision,'validated initial build or update')
            pointer={'generation_id':generation,'run':str(output.relative_to(project)),'collection':collection,
                'postgres_schema':config['postgres_schema'],'profile_id':profile_id,'revision':revision,
                'status':'active','partial':bool(incomplete_media),'statistics':statistics,'activated_at':utc_now()}
            write_json(Path(project)/'当前批次.json',pointer)
            return output,result
    except Exception:
        if db is not None and 'generation' in locals():
            db.connection.rollback()
            with db.connection.transaction():
                db.connection.execute("UPDATE index_releases SET status='failed' WHERE generation_id=%s AND status!='ready'",(generation,))
        raise
    finally:
        if vectors is not None:vectors.close()
        if db is not None:db.close()
        service.close()
