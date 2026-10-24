"""在全量OCR之前验证文本与真实图像Embedding协议，避免先跑490图后才发现不兼容。"""
from pathlib import Path
from .common import rows,write_json,file_hash
from .media import vision_input


def visual_protocol(service,counter,prepared,config):
    asset=next((a for a in rows(prepared/'media_assets.jsonl') if a['status']=='registered'),None)
    if not asset:
        return {'status':'not_applicable_no_readable_images'}
    path=prepared/'media'/asset['sha256']/Path(asset['paths'][0]).name
    messages,description,_=vision_input(path,[0,0,asset['width'],asset['height']],
        '文档图片向量输入校验。',counter,config)
    vector,usage=service.vision_embedding(messages,description['expected_tokens'])
    result={'status':'passed','asset_hash':asset['sha256'],'input_description':description,
        'dimensions':len(vector),'usage':usage,'pixel_input_sent':True,
        'api_contract':'messages + image_url; add_generation_prompt=true'}
    return result
