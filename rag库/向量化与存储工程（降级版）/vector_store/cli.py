"""降级版统一入口；本地存储命令无需启动服务，模型调用仍走既有Embedding API。"""
import argparse
import json
from pathlib import Path
from filelock import FileLock
from .common import SHARED,secrets,redact


def main(project):
    project=Path(project).resolve();parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('migrate');p.add_argument('--source');p.add_argument('--resume',action='store_true');p.add_argument('--fault-after',type=int)
    p=sub.add_parser('build');group=p.add_mutually_exclusive_group(required=True);group.add_argument('--all',action='store_true');group.add_argument('--sample',action='store_true')
    p.add_argument('--resume',action='store_true');p.add_argument('--allow-partial',action='store_true');p.add_argument('--fault-after',type=int)
    for name in ('status','verify','smoke','probe','backup','restore-drill'):sub.add_parser(name)
    p=sub.add_parser('restore');p.add_argument('--archive',required=True);p.add_argument('--destination',required=True)
    p=sub.add_parser('query');p.add_argument('text');p.add_argument('--mode',choices=['hybrid','dense','bm25'],default='hybrid');p.add_argument('--limit',type=int,default=5)
    p.add_argument('--filter',action='append',default=[],help='key=value；可重复，多个条件取交集');p.add_argument('--full',action='store_true')
    args=parser.parse_args()
    lock=project/'.runtime/project.lock';lock.parent.mkdir(exist_ok=True)
    try:
        # Qdrant Local同一路径只允许一个进程，读取也必须持锁；不删除锁文件强行启动。
        with FileLock(str(lock),timeout=0):
            if args.command in ('migrate','build'):
                from .pipeline import migrate,build
                result=migrate(project,args.source,args.fault_after) if args.command=='migrate' else build(project,args.sample,args.allow_partial,args.fault_after)
            elif args.command=='query':
                from .operations import query
                filters=dict(item.split('=',1) for item in args.filter)
                result=query(project,args.text,args.limit,args.mode,filters)
                if not args.full:
                    for r in result['results']:
                        for field in ('source','parent'):
                            obj=r[field]
                            if obj:
                                text=''.join(s['text'] for s in obj.get('source_segments',[])) or obj.get('text') or obj.get('transcript','')
                                r[field]={'id':obj['id'],'preview':text[:600],'preview_truncated':len(text)>600,
                                    'ranges':[{'start':s['start'],'end':s['end']} for s in obj.get('source_segments',[])]}
                        r['document']={k:r['document'].get(k) for k in ('id','planid','source_path','source_hash','snapshot','metadata')}
            elif args.command in ('backup','restore-drill','restore'):
                from . import backup
                result=backup.restore(args.archive,args.destination) if args.command=='restore' else getattr(backup,args.command.replace('-','_'))(project)
            else:
                from . import operations
                result=getattr(operations,args.command)(project)
        print(json.dumps(result,ensure_ascii=False,indent=2))
    except Exception as error:
        try:private=secrets()
        except Exception:private={}
        parser.exit(1,type(error).__name__+': '+redact(str(error),private)+'\n')
