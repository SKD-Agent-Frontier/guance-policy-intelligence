"""在不改动父子块的前提下，把超长检索输入分成接口能接受的完整覆盖片段。

片段下标属于retrieval_text视图，不能冒充Markdown原文位置。原文位置始终从
原始子块的source_segments取得；每个向量仍指向该子块及其完整父块。
"""
import re
from .common import digest,identity,point_id


def visual_retrieval_text(doc,chunk):
    """统一组装OCR检索视图，供构建与独立覆盖校验使用；不修正识别文字。"""
    context='\n'.join(s['text'] for s in chunk['context_segments'])
    return f"识别文本（非Markdown原句）：\n篇名：{doc['metadata'].get('title')}\nplanid：{doc['planid']}\n{context}\n{chunk['text']}"


def text_descriptions(text,counter,limit,encoded=None):
    """返回(description, fragment)列表，完整输入中的指令和特殊token均计入。

    未超长时保持原输入/缓存身份。超长时优先在换行或句末结束片段；若单行或
    单句本身超长，则按Unicode码点细分。每段都重新真实计数，禁止截断丢字。
    CRLF不从中间切开。此处只分向量输入，不声称分片仍是独立完整表格。
    """
    rendered,tokens=encoded if encoded is not None else counter.encode(text)
    def description(rendered,tokens):
        return {'type':'text','token_ids':tokens,'expected_tokens':len(tokens),'rendered_hash':digest(rendered)}
    if len(tokens)<=limit:
        return [(description(rendered,tokens),None)]
    if len(counter.encode('')[1])>=limit:
        raise ValueError('instruction template leaves no space for text')
    pieces=[];start=0
    while start<len(text):
        # 对实际编码计数作有界二分；缓存的safe_end确实通过计数，不依赖估算通过。
        low=start+1;high=min(len(text),start+limit*4);safe_end=None;safe_encoded=None
        while low<=high:
            end=(low+high)//2
            candidate=counter.encode(text[start:end])
            if len(candidate[1])<=limit:
                safe_end=end;safe_encoded=candidate;low=end+1
            else:high=end-1
        if safe_end is None:
            raise ValueError('one Unicode codepoint exceeds model input budget')
        end=safe_end
        if end<len(text):
            tail=start+int((end-start)*.65)
            boundaries=list(re.finditer(r'\n|[。！？；]',text[tail:end]))
            if boundaries:end=tail+boundaries[-1].end()
            if text[end-1:end]=='\r' and text[end:end+1]=='\n':end-=1
            if end<=start:raise ValueError('no progress while splitting text input')
        current=safe_encoded if end==safe_end else counter.encode(text[start:end])
        # BPE在边界变化时不保证严格单调；偏好的自然边界也必须重新通过硬上限。
        if len(current[1])>limit:
            end=safe_end;current=safe_encoded
            if text[end-1:end]=='\r' and text[end:end+1]=='\n':
                end-=1;current=counter.encode(text[start:end])
        if len(current[1])>limit:raise ValueError('split input still exceeds model budget')
        pieces.append((description(*current),{'start':start,'end':end,
            'offset_unit':'unicode_codepoint_in_retrieval_text','text_hash':digest(text[start:end]),
            'retrieval_text_hash':digest(text)}))
        start=end
    for index,(_,fragment) in enumerate(pieces):
        fragment.update(index=index,count=len(pieces))
    return pieces


def attach_fragment(record,fragment):
    """同一子块内即使两段文字相同，也以不同范围保留两个来源表示，计算可复用。"""
    if fragment is not None:
        record={**record,'embedding_fragment':fragment}
        record['id']=identity('representation_fragment',record['id'],fragment)
        record['point_id']=point_id(record['id'])
    return record
