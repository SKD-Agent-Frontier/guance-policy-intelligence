"""将来源对象转成独立表示；共享实际输入只省计算，不合并任何政策来源。"""
from collections import Counter as Counts
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from .common import SHARED,rows,read_json,write_json,write_rows,digest,identity,point_id,atomic_bytes
from .media import recognize,derive_visual,vision_input,recognition_profile,image_data
from .text_inputs import text_descriptions,attach_fragment,visual_retrieval_text


def process_media(prepared,destination,config,service):
    assets={a['id']:a for a in rows(prepared/'media_assets.jsonl')}
    occurrences=list(rows(prepared/'media_sources.jsonl'))
    processed=[];failures=[];cache_hits=0
    def one(asset):
        path=prepared/'media'/asset['sha256']/Path(asset['paths'][0]).name
        if asset['status']!='registered':
            return None,False,{'asset_id':asset['id'],'stage':'recognition','reason':'unreadable_asset'}
        try:
            result,hit=recognize(asset,path,service,config)
            return {**result,'asset_id':asset['id']},hit,None
        except Exception as error:
            return None,False,{'asset_id':asset['id'],'stage':'recognition','reason':str(error)[:600],
                'raw_attempt_artifact':getattr(error,'artifact',None)}
    workers=config.get('vision_concurrency',1)
    if workers not in (1,2):
        raise ValueError('vision concurrency must be 1 or 2')
    # 仍为一个构建worker；只有远端视觉请求有限并发。map按输入顺序产出，
    # 不按完成先后重排识别记录，否则恢复会改变内容清单与generation身份。
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for number,(result,hit,failure) in enumerate(pool.map(one,assets.values()),1):
            if failure:failures.append(failure)
            else:processed.append(result);cache_hits+=int(hit)
            if number%10==0:
                write_rows(destination/'media_extractions.jsonl',processed)
                write_rows(destination/'media_failures.jsonl',failures)
                print(f'media {number}/{len(assets)}, failures={len(failures)}, cached={cache_hits}',flush=True)
    write_rows(destination/'media_extractions.jsonl',processed)
    write_rows(destination/'media_failures.jsonl',failures)
    by_asset={r['asset_id']:r for r in processed}
    parents=[];chunks=[];excluded=[]
    for occurrence in occurrences:
        asset=assets.get(occurrence.get('asset_id'))
        if not asset:
            excluded.append({'occurrence_id':occurrence['id'],'reason':occurrence['status']})
            continue
        extraction=by_asset.get(asset['id'])
        if not extraction:
            excluded.append({'occurrence_id':occurrence['id'],'reason':'recognition_failed'})
            continue
        parent,children=derive_visual(asset,occurrence,extraction)
        parents.append(parent);chunks.extend(children)
    write_rows(destination/'visual_parents.jsonl',parents)
    write_rows(destination/'visual_chunks.jsonl',chunks)
    write_rows(destination/'media_excluded.jsonl',excluded)
    summary={'assets':len(assets),'recognized_assets':len(processed),'ocr_cache_hits':cache_hits,
        'recognition_failures':len(failures),'visual_parents':len(parents),'visual_chunks':len(chunks),
        'uncertain_visual_chunks':sum(c['status']!='ready' for c in chunks),
        'missing_or_unprocessed_occurrences':len(excluded)}
    write_json(destination/'media_summary.json',summary)
    return processed,parents,chunks,summary


def representation(doc,source,source_type,kind,description,profile_id,occurrence_id=None):
    input_hash=digest(description)
    version=identity('document_version',doc['planid'],doc['source_hash'])
    rid=identity('representation',version,source['id'],source_type,occurrence_id,kind,input_hash,profile_id)
    return {'id':rid,'document_id':doc['id'],'document_version_id':version,'source_id':source['id'],
        'source_type':source_type,'representation_type':kind,'occurrence_id':occurrence_id,
        'profile_id':profile_id,'input_hash':input_hash,'point_id':point_id(rid),
        'token_count':description['expected_tokens']}


def payload(record,doc,source,generation):
    metadata=doc['metadata']
    result={'generation_id':generation,'representation_id':record['id'],
        'document_version_id':record['document_version_id'],'planid':doc['planid'],
        'source_type':record['source_type'],'representation_type':record['representation_type'],
        'source_hash':doc['source_hash'],'embedding_profile_id':record['profile_id'],
        'input_hash':record['input_hash'],'title':metadata.get('title'),
        'area_code':metadata.get('area_level'),'area_name':metadata.get('area_name'),
        'admin_level':metadata.get('admin_level'),'period_key':metadata.get('fyp_name'),
        'document_type':metadata.get('plantype_label'),'report_year':metadata.get('report_year'),
        'section_path':source.get('section_path',[]),'evidence_label':source.get('evidence_label','Markdown原文')}
    if record['source_type']=='text_chunk':
        result.update(chunk_id=source['id'],parent_id=source['parent_id'])
    elif record['source_type']=='visual_chunk':
        result.update(visual_chunk_id=source['id'],parent_id=source['parent_id'],
            asset_id=source['asset_id'],occurrence_id=source['occurrence_id'],
            extraction_id=source['extraction_id'],region_id=source['region_id'],bbox=source['bbox'])
    else:
        result.update(occurrence_id=source['id'],asset_id=source['asset_id'],
            evidence_label='原图；文字及数字请核对图像')
    if record.get('embedding_fragment'):
        result['embedding_fragment']=record['embedding_fragment']
    if record.get('context_parent_id'):
        result['original_parent_id']=source['parent_id']
        result['parent_id']=record['context_parent_id']
    # 新补录块保留原始pending事实。提示不阻止检索，也不把未核实文字改写为已确认。
    if record['source_type'] in ('text_chunk','visual_chunk') and source.get('status')!='ready':
        result['source_status']=source.get('status')
        result['source_warnings']=source.get('reasons',[]) if record['source_type']=='text_chunk' else ['uncertain_recognized_text']
    return result


def make_candidates(prepared,destination,config,counter,profile_id,docs,visual_chunks,extractions):
    candidates=[];excluded=[]
    complete=config.get('coverage_policy')=='all_existing_content'
    context_parents={cid:p['id'] for p in rows(prepared/'parent_contexts.jsonl') for cid in p['member_chunk_ids']} if (prepared/'parent_contexts.jsonl').exists() else {}
    def add_text(doc,chunk,source_type,text,occurrence_id=None,encoded=None):
        # 接口硬上限仅约束每次输入，不再作为丢弃原始子块的理由。
        for description,fragment in text_descriptions(text,counter,config['max_input_tokens'],encoded):
            record=representation(doc,chunk,source_type,'text',description,profile_id,occurrence_id)
            if source_type=='text_chunk' and chunk['id'] in context_parents:
                record['context_parent_id']=context_parents[chunk['id']]
                record['id']=identity('representation_context',record['id'],record['context_parent_id'])
                record['point_id']=point_id(record['id'])
            candidates.append({'record':attach_fragment(record,fragment),'description':description,'source':chunk})
    allowed={r['id'] for r in rows(prepared/'eligibility.jsonl') if r['status']=='included'}
    for chunk in rows(prepared/'chunks.jsonl'):
        if chunk['id'] not in allowed:
            continue
        rendered,tokens=counter.encode(chunk['retrieval_text'])
        if len(tokens)!=chunk['embedding_tokens'] or digest(rendered)!=chunk['embedding_input_hash']:
            raise ValueError('upstream tokenizer/input fingerprint mismatch')
        add_text(docs[chunk['document_id']],chunk,'text_chunk',chunk['retrieval_text'],encoded=(rendered,tokens))
    for chunk in visual_chunks:
        if chunk['status']!='ready' and not complete:
            excluded.append({'source_id':chunk['id'],'reason':'uncertain_recognized_text'})
            continue
        doc=docs[chunk['document_id']]
        text=visual_retrieval_text(doc,chunk)
        rendered,tokens=counter.encode(text)
        if len(tokens)>config['max_input_tokens'] and not complete:
            excluded.append({'source_id':chunk['id'],'reason':'complete_visual_unit_over_limit'})
            continue
        add_text(doc,chunk,'visual_chunk',text,chunk['occurrence_id'],(rendered,tokens))
    assets={a['id']:a for a in rows(prepared/'media_assets.jsonl')}
    recognized={e['asset_id']:e for e in extractions}
    # 整图向量保留视觉信息；表格/图表/公式再选有上下文的区域图文表示，不机械遍历所有组合。
    image_jobs=[]
    for occurrence in rows(prepared/'media_sources.jsonl'):
        asset=assets.get(occurrence.get('asset_id'))
        if not asset or asset['status']!='registered':
            continue
        extraction=recognized.get(asset['id'])
        if not complete and (not extraction or extraction['image_type'] in ('blank','unreadable')):
            reason='recognition_unavailable_for_image' if not extraction else ('blank_image_has_no_policy_content' if extraction['image_type']=='blank' else 'unreadable_image_not_evidence')
            excluded.append({'source_id':occurrence['id'],'reason':reason})
            continue
        path=prepared/'media'/asset['sha256']/Path(asset['paths'][0]).name
        image_jobs.append((docs[occurrence['document_id']],occurrence,'image_occurrence',
            [0,0,asset['width'],asset['height']],'',path,occurrence['id']))
    for chunk in visual_chunks:
        if (chunk['status']=='ready' or complete) and chunk['kind'] in ('table','chart','formula'):
            asset=assets[chunk['asset_id']]
            path=prepared/'media'/asset['sha256']/Path(asset['paths'][0]).name
            context='\n'.join(s['text'] for s in chunk['context_segments'])
            image_jobs.append((docs[chunk['document_id']],chunk,'visual_chunk',chunk['bbox'],context,path,chunk['occurrence_id']))
    for doc,source,source_type,bbox,text,path,occ_id in image_jobs:
        try:
            _,description,raw=vision_input(path,bbox,text,counter,config)
            derived=SHARED/'.cache/derived_images'/(description['derived_image_hash']+'.png')
            if not derived.exists():
                atomic_bytes(derived,raw)
            record=representation(doc,source,source_type,description['type'],description,profile_id,occ_id)
            candidates.append({'record':record,'description':description,'source':source})
        except Exception as error:
            excluded.append({'source_id':source['id'],'reason':'visual_input_invalid:'+str(error)[:300]})
    write_rows(destination/'representations.jsonl',(c['record'] for c in candidates))
    write_rows(destination/'representation_excluded.jsonl',excluded)
    return candidates,excluded


def messages_from_description(description,counter):
    path=SHARED/'.cache/derived_images'/(description['derived_image_hash']+'.png')
    raw=path.read_bytes()
    if digest(raw)!=description['derived_image_hash']:
        raise ValueError('derived image cache changed')
    content=[{'type':'image_url','image_url':{'url':image_data(raw)}}]
    if description['text']:
        content.append({'type':'text','text':description['text']})
    return [{'role':'system','content':[{'type':'text','text':counter.instruction}]},
            {'role':'user','content':content}]
