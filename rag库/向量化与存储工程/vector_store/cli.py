"""明确区分只读准备与真实构建：prepare不联网，build才会调用模型并写入数据库。"""
import argparse
from pathlib import Path
import json
from .common import SHARED,read_json,paths,write_json,secrets,redact,utc_now


def main(project):
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    for command in ('prepare','dedup','build'):
        p=sub.add_parser(command)
        mode=p.add_mutually_exclusive_group(required=True)
        mode.add_argument('--all',action='store_true')
        mode.add_argument('--sample',action='store_true')
        if command=='build':
            p.add_argument('--allow-partial',action='store_true',help='仅允许明确记录的视觉待处理项；模型输入失败仍阻止发布')
            p.add_argument('--fault-after',type=int,help='故障演练：第N个输入缓存封存后中断，随后用同命令恢复')
    for command in ('verify','smoke','backup','restore-drill','status','probe'):
        sub.add_parser(command)
    activate=sub.add_parser('activate')
    activate.add_argument('--generation',required=True)
    activate.add_argument('--expected-revision',type=int,required=True)
    activate.add_argument('--reason',required=True)
    document=sub.add_parser('document')
    document.add_argument('--planid',required=True)
    args=parser.parse_args()
    try:
        if args.command=='prepare':
            from .prepare import prepare
            from .validation import validate_prepared
            output,manifest=prepare(project,args.sample)
            check=validate_prepared(output,manifest)
            write_json(output/'prepared_validation.json',check)
            if not check['passed']:raise ValueError('prepared validation failed')
            print(json.dumps({'output':str(output),'status':manifest['status'],'documents':manifest['documents'],
                'text_included':manifest['text_included'],'text_excluded':manifest['text_excluded'],
                'physical_media_files':manifest['physical_media_files'],'validation':check},ensure_ascii=False))
        elif args.command=='dedup':
            from .prepare import prepare
            from .dedup import prepare_dedup
            prepared,_=prepare(project,args.sample)
            config,_,_=paths(project)
            output,summary=prepare_dedup(prepared,config['dedup'])
            print(json.dumps({'output':str(output),'summary':summary},ensure_ascii=False))
        elif args.command=='build':
            from filelock import FileLock
            from .pipeline import build
            # 两版共享缓存，整个构建串行；禁止两个进程同时封存同一cache键。
            with FileLock(str(SHARED/'.cache/build.lock'),timeout=0):
                output,result=build(project,args.sample,args.allow_partial,args.fault_after)
            print(json.dumps({'output':str(output),'validation':result},ensure_ascii=False))
        elif args.command=='status':
            pointer=Path(project)/'当前批次.json'
            print(json.dumps(read_json(pointer) if pointer.exists() else {'status':'not_activated'},ensure_ascii=False,indent=2))
        elif args.command=='probe':
            from .prepare import prepare
            from .embedding import Counter,Service,embedding_profile
            from .probe import visual_protocol
            config,upstream,_=paths(project)
            prepared,_=prepare(project,True)
            private=secrets();counter=Counter(upstream);service=Service(private,config['model'])
            try:
                models=service.request('/models')
                profile_id,profile=embedding_profile(config,counter,models,private['embedding_base_url'])
                _,tokens=counter.encode('政策检索向量空间校验。')
                values,usage=service.text_batch([tokens])
                result={'profile_id':profile_id,'profile':profile,
                    'text':{'dimensions':len(values[0]),'expected_tokens':len(tokens),'usage':usage},
                    'visual':visual_protocol(service,counter,prepared,config)}
                write_json(Path(project)/'.cache/latest-probe.json',result)
                print(json.dumps(result,ensure_ascii=False))
            finally:service.close()
        elif args.command=='document':
            from .operations import document_status
            print(json.dumps(document_status(project,args.planid),ensure_ascii=False,default=str))
        elif args.command=='activate':
            from .operations import verify,open_release
            from .storage import Database
            # 回滚同样先实查旧库/旧来源/旧向量，不只检查曾经留下的报告。
            check=verify(project,args.generation)
            if not check['passed']:raise ValueError('target release does not validate')
            config,generation,output,frozen,private=open_release(project,args.generation)
            db=Database(private,config['postgres_schema'])
            try:
                revision=db.activate(config['knowledge_base_id'],generation,args.expected_revision,args.reason)
            finally:db.close()
            write_json(Path(project)/'当前批次.json',{'generation_id':generation,'run':str(output.relative_to(project)),
                'collection':frozen['collection'],'postgres_schema':config['postgres_schema'],'profile_id':frozen['profile_id'],
                'revision':revision,'status':'active','activated_at':utc_now(),'reason':args.reason})
            print(json.dumps({'generation_id':generation,'revision':revision},ensure_ascii=False))
        else:
            from . import operations
            function=getattr(operations,args.command.replace('-','_'))
            print(json.dumps(function(project),ensure_ascii=False,default=str))
    except Exception as error:
        # psycopg/HTTP异常可能包含连接字符串，打印前统一脱敏，不泄露原始traceback。
        try:private=secrets()
        except Exception:private={}
        message=redact(str(error),private)
        parser.exit(1,type(error).__name__+': '+message+'\n')
