"""核对每个原始子块都有向量，以及超长输入片段连续覆盖完整检索视图。"""
from collections import defaultdict
from .common import rows,read_json,digest
from .text_inputs import visual_retrieval_text


def validate_coverage(prepared,output,media_path):
    """不将point总数当作覆盖证明：按原生子块ID、视觉子块ID、图片出现逐项核对。

    旧批次仍按其冻结规则报告覆盖；仅完整覆盖模式把缺块作为发布错误。
    超长片段必须无缺口、无重叠且逐段哈希等于检索视图切片，不能只验总长度。
    """
    config=read_json(prepared/'manifest.json')['specification']['config']
    complete=config.get('coverage_policy')=='all_existing_content'
    docs={d['id']:d for d in rows(prepared/'documents.jsonl')}
    native={c['id']:c for c in rows(prepared/'chunks.jsonl')}
    visual={c['id']:c for c in rows(media_path/'visual_chunks.jsonl')}
    assets={a['id']:a for a in rows(prepared/'media_assets.jsonl')}
    images={o['id'] for o in rows(prepared/'media_sources.jsonl')
        if o.get('asset_id') in assets and assets[o['asset_id']]['status']=='registered'}
    groups=defaultdict(list);image_ids=set();errors=[]
    context_parents={cid:p['id'] for p in rows(prepared/'parent_contexts.jsonl') for cid in p['member_chunk_ids']} if (prepared/'parent_contexts.jsonl').exists() else {}
    for record in rows(output/'representations.jsonl'):
        if record['token_count']>config['max_input_tokens']:
            errors.append('representation input over limit: '+record['id'])
        if record['source_type']=='image_occurrence':image_ids.add(record['source_id'])
        if record['representation_type']=='text':groups[record['source_id']].append(record)
        if record['source_type']=='text_chunk' and record.get('context_parent_id')!=context_parents.get(record['source_id']):
            errors.append('supplemental parent mapping mismatch: '+record['id'])
    split=0;fragments=0;counter=None
    for sid,records in groups.items():
        source=native.get(sid) or visual.get(sid)
        if source is None:
            errors.append('unknown text source: '+sid);continue
        text=source['retrieval_text'] if sid in native else visual_retrieval_text(docs[source['document_id']],source)
        parts=[r for r in records if r.get('embedding_fragment')]
        if not parts:
            if len(records)!=1:errors.append('duplicate unsplit source: '+sid)
            continue
        if counter is None:
            from pathlib import Path
            from .embedding import Counter
            counter=Counter(Path(read_json(prepared/'manifest.json')['upstream_project']))
        split+=1;fragments+=len(parts);cursor=0
        for index,record in enumerate(sorted(parts,key=lambda r:r['embedding_fragment']['index'])):
            part=record['embedding_fragment'];start,end=part['start'],part['end']
            if not (len(parts)==len(records)==part['count'] and index==part['index']
                and cursor==start<end<=len(text) and part['retrieval_text_hash']==digest(text)
                and part['text_hash']==digest(text[start:end])
                and part['offset_unit']=='unicode_codepoint_in_retrieval_text'):
                errors.append('fragment gap/overlap/content mismatch: '+record['id'])
            # 独立重编码超长分片，证明实际向量输入与记录的片段一致，而非仅有范围。
            rendered,tokens=counter.encode(text[start:end])
            expected={'type':'text','token_ids':tokens,'expected_tokens':len(tokens),'rendered_hash':digest(rendered)}
            if record['input_hash']!=digest(expected) or record['token_count']!=len(tokens):
                errors.append('fragment token input differs from source: '+record['id'])
            cursor=end
        if cursor!=len(text):errors.append('incomplete retrieval text: '+sid)
    native_missing=sorted(set(native)-set(groups));visual_missing=sorted(set(visual)-set(groups))
    image_missing=sorted(images-image_ids)
    if complete:
        if native_missing:errors.append('native text chunks missing')
        if visual_missing:errors.append('visual text chunks missing')
        if image_missing:errors.append('registered original images missing')
    return {'policy':config.get('coverage_policy','strict'),'native_chunks':len(native),
        'native_covered':len(native)-len(native_missing),'native_missing':native_missing,
        'native_text_representations':sum(len(groups[sid]) for sid in native),
        'visual_chunks':len(visual),'visual_covered':len(visual)-len(visual_missing),'visual_missing':visual_missing,
        'original_image_occurrences':len(images),'original_images_covered':len(images)-len(image_missing),
        'original_images_missing':image_missing,'split_text_sources':split,'input_fragments':fragments,
        'supplemental_parent_context_links':len(context_parents),
        'errors':errors,'passed':not errors}
