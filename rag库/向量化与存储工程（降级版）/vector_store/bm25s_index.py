"""BM25S中文稀疏索引；jieba分词仅用于检索，永远不回写政策原文。

过滤在Top-K计算前执行，并再次排除越界ID与零分填充；未命中词不伪造结果。
索引保存真实BM25S矩阵和词表，重新打开采用mmap，不以SQL LIKE冒充BM25。
"""
import gc
from pathlib import Path
import numpy as np
import bm25s
import jieba
from .common import read_json,write_json,file_hash,digest


class BM25SLexical:
    def __init__(self,root):
        self.root=root;root.mkdir(parents=True,exist_ok=True)
        self.model=None;self.ids=[];self.generation=None
        self.tokenizer=jieba.Tokenizer();self.tokenizer.tmp_dir=str(root)

    def tokens(self,text):
        return [t for t in self.tokenizer.cut(text.lower(),HMM=False) if any(c.isalnum() for c in t)]

    def build(self,generation,documents):
        """documents为[(representation_id,完整检索片段)]；顺序与矩阵行一一对应。"""
        path=self.root/generation;path.mkdir(exist_ok=True)
        ids=[rid for rid,text in documents]
        fingerprint=digest([(rid,digest(text)) for rid,text in documents])
        if (path/'manifest.json').exists():
            old=read_json(path/'manifest.json')
            if old['fingerprint']!=fingerprint or old['ids']!=ids:raise ValueError('immutable BM25 input conflict')
            self.load(generation);return {'documents':len(ids),'reused':True}
        if documents:
            model=bm25s.BM25(method='lucene',k1=1.5,b=.75,backend='numpy')
            model.index([self.tokens(text) for _,text in documents],show_progress=False)
            model.save(str(path))
            del model;gc.collect()
        files={p.name:file_hash(p) for p in path.iterdir() if p.is_file() and p.name!='manifest.json'}
        write_json(path/'manifest.json',{'generation':generation,'ids':ids,'fingerprint':fingerprint,
            'files':files,'tokenization':'jieba-HMM-false-lower-alphanumeric; no stopword deletion','method':'lucene','k1':1.5,'b':.75})
        self.load(generation);return {'documents':len(ids),'reused':False}

    def load(self,generation):
        path=self.root/generation;manifest=read_json(path/'manifest.json')
        for name,h in manifest['files'].items():
            if file_hash(path/name)!=h:raise ValueError('BM25 index file changed: '+name)
        self.close();self.ids=manifest['ids'];self.generation=generation
        self.model=bm25s.BM25.load(str(path),mmap=True,load_corpus=False,show_progress=False) if self.ids else None

    def search(self,query,limit,allowed_ids=None):
        if self.model is None or not self.ids or limit<=0:return []
        tokens=self.tokens(query)
        if not any(t in self.model.vocab_dict for t in tokens):return []
        mask=None
        if allowed_ids is not None:
            mask=np.asarray([rid in allowed_ids for rid in self.ids],dtype=np.float32)
            if not mask.any():return []
        found,scores=self.model.retrieve([tokens],k=min(limit,len(self.ids)),weight_mask=mask,
            show_progress=False,backend_selection='numpy')
        results=[]
        for index,score in zip(found[0],scores[0]):
            rid=self.ids[int(index)]
            # BM25S在合格成员不足K时可用零分行补齐；这些行不能当作检索证据。
            if float(score)>0 and (allowed_ids is None or rid in allowed_ids):results.append({'id':rid,'score':float(score)})
        return results

    def validate(self,generation,expected_documents):
        self.load(generation)
        expected_ids=[rid for rid,text in expected_documents]
        if self.ids!=expected_ids:raise ValueError('BM25/SQLite member order mismatch')
        expected=digest([(rid,digest(text)) for rid,text in expected_documents])
        if read_json(self.root/generation/'manifest.json')['fingerprint']!=expected:raise ValueError('BM25/SQLite text fingerprint mismatch')
        return {'passed':True,'documents':len(self.ids),'mmap':True}

    def close(self):
        self.model=None;gc.collect()
