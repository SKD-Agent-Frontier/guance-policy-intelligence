"""真实模型API、显式token输入、视觉预处理与有校验和的float32缓存。

实测此服务字符串输入会多计一个token，且向量不同；文本只提交本地固定模板的
token ID列表。每批核对服务prompt_tokens总数，差异即失败，不截断或伪造成功。
"""
import base64
import importlib.util
import json
import math
import os
from threading import Lock
from importlib.metadata import version
from pathlib import Path
import time
import numpy as np
import httpx
from .common import SHARED,canonical,digest,identity,read_json,write_json,atomic_bytes,redact,long_path,utc_now

VISION_OPTIONS={'add_generation_prompt':True,'continue_final_message':False}


class Counter:
    """复用既有tokenizer实现，按已固定7份文件哈希核验；不下载模型权重。"""
    def __init__(self, upstream):
        # 只加载本地固定文件；禁止计数时隐式联网下载。这里只编码检索输入，
        # 不清洗原文，不改动原MD的Unicode位置；也不加载Embedding模型权重。
        from transformers import AutoTokenizer
        profile=read_json(upstream/'model_profile.json')
        self.directory=upstream/'.cache/qwen-tokenizer'
        for name,expected in profile['files'].items():
            if digest((self.directory/name).read_bytes())!=expected:
                raise ValueError('tokenizer checksum mismatch: '+name)
        self.tokenizer=AutoTokenizer.from_pretrained(self.directory,local_files_only=True,trust_remote_code=False)
        self.tokenizer.chat_template=(self.directory/'chat_template.jinja').read_text(encoding='utf-8')
        self.instruction=read_json(upstream/'config.json')['instruction']
        self.profile=profile

    def encode(self,text):
        conversation=[{'role':'system','content':[{'type':'text','text':self.instruction}]},
                      {'role':'user','content':[{'type':'text','text':text}]}]
        rendered=self.tokenizer.apply_chat_template(conversation,tokenize=False,add_generation_prompt=True)
        tokens=self.tokenizer.encode(rendered,add_special_tokens=False,truncation=False)
        return rendered,tokens


def normalize(vector,dimensions=4096):
    """严格检验条数外的每条向量：拒绝非数值、错误维度、NaN/Inf及零范数。"""
    if isinstance(vector,str):
        raw=base64.b64decode(vector,validate=True)
        if len(raw)!=4*dimensions:
            raise ValueError('base64 vector byte count mismatch')
        values=np.frombuffer(raw,dtype='<f4').copy()
    else:
        values=np.asarray(vector,dtype=np.float32)
    if values.shape!=(dimensions,) or not np.isfinite(values).all():
        raise ValueError('invalid vector dimensions or finite values')
    norm=float(np.linalg.norm(values.astype(np.float64)))
    if not math.isfinite(norm) or norm<=1e-12:
        raise ValueError('zero or invalid vector norm')
    return (values/norm).astype('<f4')


class Service:
    def __init__(self,private,model,dimensions=4096,audit=True):
        self.private=private
        self.model=model
        self.dimensions=dimensions
        self.client=httpx.Client(base_url=private['embedding_base_url'].rstrip('/'),
            headers={'Authorization':'Bearer '+private['embedding_api_key']},
            timeout=httpx.Timeout(120,connect=15),trust_env=False)
        self.calls=0
        self.audit=audit
        self._journal_lock=Lock()

    def journal(self,path,body,attempt,status,usage=None,error=None):
        if not self.audit:
            return
        row={'at':utc_now(),'path':path,'model':(body or {}).get('model'),
            'request_hash':digest(body) if body is not None else None,
            'attempt':attempt+1,'status':status,'usage':usage,
            'error':redact(error,self.private) if error else None}
        file=long_path(SHARED/'.cache/service_calls.jsonl');file.parent.mkdir(parents=True,exist_ok=True)
        with self._journal_lock:
            with file.open('a',encoding='utf-8',newline='\n') as stream:
                stream.write(canonical(row)+'\n');stream.flush();os.fsync(stream.fileno())

    def close(self):
        self.client.close()

    def request(self,path,body=None):
        """仅对连接/限流/5xx有界重试；4xx错误不会按成功降级，原始错误脱敏。"""
        for attempt in range(4):
            try:
                with self._journal_lock:self.calls+=1
                response=self.client.get(path) if body is None else self.client.post(path,json=body,
                    timeout=httpx.Timeout(600,connect=15) if path=='/chat/completions' else self.client.timeout)
                try:usage=response.json().get('usage')
                except (ValueError,AttributeError):usage=None
                self.journal(path,body,attempt,response.status_code,usage)
                if response.status_code==429 or response.status_code>=500:
                    if attempt<3:
                        time.sleep(min(2**attempt,8))
                        continue
                response.raise_for_status()
                return response.json()
            except (httpx.ConnectError,httpx.TimeoutException) as error:
                self.journal(path,body,attempt,'connection_error',error=type(error).__name__)
                if attempt==3:
                    raise RuntimeError(redact(type(error).__name__,self.private)) from None
                time.sleep(2**attempt)
            except httpx.HTTPStatusError as error:
                raise RuntimeError('service HTTP '+str(error.response.status_code)+': '+redact(error.response.text[:600],self.private)) from None
        raise RuntimeError('service retry budget exhausted')

    def text_batch(self,token_lists):
        expected=sum(len(t) for t in token_lists)
        result=self.request('/embeddings',{'model':self.model,'input':token_lists,'encoding_format':'base64'})
        if result.get('model')!=self.model:
            raise ValueError('service returned a different model')
        if result.get('usage',{}).get('prompt_tokens')!=expected:
            raise ValueError('service token count differs; possible truncation or template mismatch')
        data=result.get('data',[])
        if len(data)!=len(token_lists) or sorted(r.get('index') for r in data)!=list(range(len(token_lists))):
            raise ValueError('embedding count/index mismatch')
        return [normalize(row['embedding'],self.dimensions) for row in sorted(data,key=lambda x:x['index'])],result['usage']

    def vision_embedding(self,messages,expected_tokens):
        # 多模态接口使用显式messages而非把图片链接当字符串；不能用文本代替视觉输入。
        result=self.request('/embeddings',{'model':self.model,'messages':messages,'encoding_format':'base64',
            **VISION_OPTIONS})
        data=result.get('data',[])
        if result.get('model')!=self.model or len(data)!=1 or data[0].get('index')!=0:
            raise ValueError('invalid multimodal embedding response')
        if result.get('usage',{}).get('prompt_tokens')!=expected_tokens:
            raise ValueError('visual token count differs; server processor/template must be verified')
        return normalize(data[0]['embedding'],self.dimensions),result.get('usage',{})


class Cache:
    """共享计算缓存不合并来源：键为profile+实际输入，每个来源另有representation。"""
    def __init__(self,profile_id,root=None):
        self.root=long_path(Path(root) if root is not None else SHARED/'.cache/vectors'/profile_id)
        self.root.mkdir(parents=True,exist_ok=True)
        self.profile_id=profile_id

    def get(self,input_hash):
        meta=self.root/(input_hash+'.json')
        file=self.root/(input_hash+'.f32')
        if not meta.exists() or not file.exists():
            return None
        description=read_json(meta)
        raw=file.read_bytes()
        if description.get('profile_id')!=self.profile_id or description.get('input_hash')!=input_hash or digest(raw)!=description['checksum']:
            raise ValueError('cached vector checksum/profile mismatch')
        values=np.frombuffer(raw,dtype='<f4').copy()
        normalized=normalize(values,description['dimensions'])
        if not np.allclose(values,normalized,rtol=1e-5,atol=1e-6):
            raise ValueError('cached vector not normalized')
        return values,description

    def put(self,input_hash,vector,usage):
        vector=normalize(vector)
        raw=vector.tobytes()
        path=self.root/(input_hash+'.f32')
        description={'profile_id':self.profile_id,'input_hash':input_hash,'status':'ready',
            'dimensions':len(vector),'dtype':'float32_le','checksum':digest(raw),
            'norm':float(np.linalg.norm(vector.astype(np.float64))),'path':self.profile_id+'/'+path.name,'usage':usage}
        # 文件封存后才写ready元数据。中断留下单独的f32可安全重算，不误报缓存命中。
        atomic_bytes(path,raw)
        write_json(path.with_suffix('.json'),description)
        return vector,description


def embedding_profile(config,counter,service_models,service_url):
    row=next((m for m in service_models['data'] if m['id']==config['model']),None)
    if row is None:
        raise ValueError('requested embedding model not advertised by service')
    profile={'version':'guance-embedding-1','model_family':config['model_family'],'service_model':row,
        'service_url':service_url,
        'client_dependencies':{name:version(name) for name in ('transformers','tokenizers','pillow','numpy','httpx')},
        'server_revision':None,'server_revision_status':'not_exposed_by_service',
        'local_processor_profile':counter.profile,'instruction':counter.instruction,
        'text_transport':'explicit_token_ids','encoding_format':'base64_float32_le',
        'dimensions':config['dimensions'],'normalization':config['normalization'],'distance':config['distance'],
        'max_input_tokens':config['max_input_tokens'],
        'vision_preprocess':{'patch_size':16,'merge_size':2,'max_pixels':config['vision_max_pixels'],'resize':'qwen_smart_resize_v1'},
        'vision_transport':{'endpoint':'embeddings','input':'messages_with_image_url',**VISION_OPTIONS},
        'vision_prompt':'Represent the user\'s input.'}
    return identity('profile',profile),profile
