"""确定性身份、原子文件、配置和原文范围工具；不接触模型或数据库。"""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import time
import uuid

SHARED=Path(__file__).resolve().parents[1]
# 源码已归档到rag库/历史资料，语料库仍位于仓库根目录。
REPO=SHARED.parents[2]
CORPUS=REPO/'语料库/Agent标准引用库'
POINT_NAMESPACE=uuid.UUID('6ab2851a-e8be-43eb-b123-23f6f19bd82a')


def long_path(path):
    """Windows工程名+完整SHA目录可能超过MAX_PATH，使用文件API的扩展路径。

    只改变本次文件访问的写法，不修改系统长路径开关，不改变业务ID、原文路径或哈希。
    """
    value=os.path.abspath(os.fspath(path))
    if os.name=='nt' and not value.startswith('\\\\?\\'):
        value='\\\\?\\UNC\\'+value[2:] if value.startswith('\\\\') else '\\\\?\\'+value
    return Path(value)


def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)


def digest(value):
    """复合对象用规范JSON；字节/字符串不改变内容，避免拼接身份产生歧义。"""
    if not isinstance(value,(bytes,str)):
        value=canonical(value)
    return hashlib.sha256(value if isinstance(value,bytes) else value.encode('utf-8')).hexdigest()


def identity(kind,*parts):
    return kind+'_'+digest(list(parts))


def point_id(representation_id):
    return str(uuid.uuid5(POINT_NAMESPACE,representation_id))


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def read_json(path):
    return json.loads(long_path(path).read_text(encoding='utf-8'))


def rows(path):
    with long_path(path).open(encoding='utf-8') as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def atomic_bytes(path, value):
    """先fsync临时文件再原子替换；Windows短时文件占用有界重试，不吞永久失败。"""
    path=long_path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+'.tmp-'+uuid.uuid4().hex)
    try:
        with tmp.open('wb') as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        for attempt in range(9):
            try:
                os.replace(tmp,path)
                return
            except PermissionError:
                if attempt==8:
                    raise
                time.sleep(min(.05*2**attempt,.8))
    finally:
        tmp.unlink(missing_ok=True)


def write_json(path,value):
    atomic_bytes(path,(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n').encode('utf-8'))


def write_rows(path, values):
    # 逐表输出的输入规模由已冻结上游限制；大表传入生成器，避免一次拼成大字符串。
    path=long_path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+'.writing')
    with temporary.open('w',encoding='utf-8',newline='\n') as stream:
        for value in values:
            stream.write(canonical(value)+'\n')
        stream.flush()
        os.fsync(stream.fileno())
    atomic_bytes(path,temporary.read_bytes())
    temporary.unlink()


def file_hash(path):
    with long_path(path).open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def validate_segments(text, segments):
    """输入位置均为完整UTF-8解码文件的Unicode码点，左闭右开，不规范化CRLF。"""
    for segment in segments:
        a,b=segment['start'],segment['end']
        if not isinstance(a,int) or not isinstance(b,int) or not 0<=a<b<=len(text):
            raise ValueError('invalid Unicode codepoint range')
        if text[a:b]!=segment['text']:
            raise ValueError('source range does not equal original bytes decoded as UTF-8')


def secrets():
    """只读被忽略的私密配置；异常消息不能包含凭证值或完整连接串。"""
    value=read_json(SHARED/'secrets.local.json')
    for key in ('embedding_base_url','embedding_api_key','postgres_dsn','qdrant_url','qdrant_api_key'):
        if not value.get(key):
            raise ValueError('missing private configuration field: '+key)
    return value


def redact(message,private):
    for key in ('embedding_api_key','postgres_dsn','qdrant_api_key'):
        if private.get(key):
            message=message.replace(private[key],'[REDACTED]')
    return message


def paths(project):
    project=Path(project).resolve()
    config=read_json(project/'config.json')
    upstream=(project/config['upstream_project']).resolve()
    pointer=read_json(upstream/'当前批次.json')
    if not pointer.get('validation_passed'):
        raise ValueError('upstream batch has not passed validation')
    return config,upstream,upstream/pointer['run']
