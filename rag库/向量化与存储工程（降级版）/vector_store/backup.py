"""关闭本地库后的文件备份；Local不支持服务端snapshot，不能调用空接口冒充备份。

备份不含凭证和Python环境。每个文件记录SHA-256；恢复到全新目录并重定位本地
清单，保留来源身份、向量空间及相对位置。执行期间由CLI项目锁阻止其他读写。
"""
from pathlib import Path,PurePosixPath
import json
import shutil
import uuid
import zipfile
from .common import read_json,write_json,file_hash,long_path,utc_now
from .storage import open_stores
from .operations import active_manifest,verify


def backup(project,destination=None):
    config=read_json(project/'config.json');db,vectors,lexical=open_stores(project,config)
    try:
        _,manifest,_=active_manifest(db,project)
        count=vectors.count(manifest['collection'])
        if not db.integrity():raise ValueError('SQLite integrity failed before backup')
    finally:lexical.close();vectors.close();db.close()
    # 所有SQLite/Qdrant连接已关闭，WAL已提交；不能只复制正在写入的主数据库文件。
    destination=Path(destination or project/'.cache/backups'/('backup-'+uuid.uuid4().hex[:12]+'.zip'))
    destination.parent.mkdir(parents=True,exist_ok=True)
    if destination.exists():raise ValueError('backup file already exists')
    roots=[project/config['storage']['directory'],project/'outputs']
    roots += [project/'.cache'/n for n in ('vectors','qwen-tokenizer','ocr','ocr-failures','ocr-attempts','derived_images','service-canaries')]
    files=[p for name in ('run.py','README.md','AGENTS.md','requirements.txt','requirements.lock.txt',
        'config.json','model_profile.json','schema.sql','当前批次.json') if (p:=project/name).exists()]
    if (project/'vector_store').exists():files.extend((project/'vector_store').glob('*.py'))
    for root in roots:
        if root.exists():files.extend(project/p.relative_to(long_path(project)) for p in long_path(root).rglob('*') if p.is_file() and p.name!='.lock' and not p.name.endswith(('-wal','-shm')))
    entries=[]
    with zipfile.ZipFile(long_path(destination),'x',compression=zipfile.ZIP_STORED,allowZip64=True) as archive:
        for path in files:
            relative=path.relative_to(project).as_posix()
            entries.append({'path':relative,'sha256':file_hash(path)})
            archive.write(long_path(path),relative)
        archive.writestr('backup_manifest.json',json.dumps({'project_root':str(project),'generation_id':manifest['generation_id'],
            'points':count,'files':entries,'created_at':utc_now()},ensure_ascii=False))
    result={'path':str(destination),'sha256':file_hash(destination),'files':len(entries),'points':count,'credentials_included':False}
    write_json(destination.with_suffix('.receipt.json'),result);return result


def restore(archive_path,destination):
    """只恢复到尚不存在的目录；拒绝Zip路径穿越和缺失/被篡改的文件。"""
    destination=Path(destination).resolve()
    if destination.exists():raise ValueError('restore destination must be new')
    with zipfile.ZipFile(long_path(archive_path)) as archive:
        manifest=json.loads(archive.read('backup_manifest.json'))
        for entry in manifest['files']:
            relative=PurePosixPath(entry['path'])
            if relative.is_absolute() or '..' in relative.parts or '\\' in entry['path'] or ':' in entry['path']:raise ValueError('unsafe backup path')
            path=destination/entry['path']
            if not path.resolve().is_relative_to(destination):raise ValueError('backup path escaped destination')
            target=long_path(path);target.parent.mkdir(parents=True,exist_ok=True)
            with archive.open(entry['path']) as source,target.open('wb') as output:shutil.copyfileobj(source,output)
            if file_hash(path)!=entry['sha256']:raise ValueError('restored file checksum mismatch')
    config=read_json(destination/'config.json');db,vectors,lexical=open_stores(destination,config)
    try:db.rebase(manifest['project_root'],str(destination))
    finally:lexical.close();vectors.close();db.close()
    for path in (destination/'outputs').glob('local_generation_*/manifest.json'):
        data=read_json(path)
        for key in ('prepared_path','media_path'):
            data[key]=str(destination/Path(data[key]).relative_to(Path(manifest['project_root'])))
        write_json(path,data)
    return {'destination':str(destination),'source_generation':manifest['generation_id'],'expected_points':manifest['points']}


def restore_drill(project):
    receipt=backup(project);destination=project/'.cache/restore-tests'/uuid.uuid4().hex[:12]
    restored=restore(receipt['path'],destination);check=verify(destination)
    result={'backup':receipt,'restore':restored,'validation':check,'passed':check['passed']}
    write_json(project/'恢复演练.json',result);return result
