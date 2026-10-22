"""从真实产物汇总两版进度，维护批次时间索引；不伪造未运行的向量或数据库结果。"""
from datetime import datetime,timezone,timedelta
from importlib.metadata import version
import argparse
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET
from vector_store.common import SHARED,rows,read_json,write_json,file_hash,digest,CORPUS


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--git-status',default='pending_local_commit')
    args=parser.parse_args()
    now=datetime.now(timezone(timedelta(hours=8))).isoformat()
    code={p.name:file_hash(p) for p in sorted((SHARED/'vector_store').glob('*.py'))}
    for name in ('schema.sql','requirements.txt','requirements.lock.txt'):
        if (SHARED/name).exists():code[name]=file_hash(SHARED/name)
    tests=SHARED/'.cache/pytest-ready.xml'
    suite=ET.parse(tests).getroot().find('testsuite') if tests.exists() else None
    tested={key:int(suite.attrib[key]) for key in ('tests','failures','errors','skipped')} if suite is not None else None
    packages={name:version(name) for name in ('httpx','numpy','psycopg','psycopg-binary','qdrant-client','datasketch','transformers','Pillow','pytest')}
    wheel_records=read_json(SHARED/'.cache/verified_wheels.json')
    installed=[]
    for item in wheel_records:
        try:matches=version(item['package'].replace('_','-'))==item['version']
        except Exception:matches=False
        if matches:installed.append(item)
    unique={r['sha256']:r for r in installed}
    write_json(SHARED/'依赖来源.json',{'created_at':now,'python':sys.version,'direct_versions':packages,
        'initial_environment_source':'只读复制已验证切块环境的site-packages到新.venv；旧环境不改写',
        'additional_wheels':list(unique.values()),'additional_install_mode':'pip --no-index --find-links local_verified_wheelhouse',
        'integrity':'新增wheel逐一与缓存中的PyPI索引SHA-256匹配，未关闭哈希验证',
        'lock_sha256':file_hash(SHARED/'requirements.lock.txt')})
    print('actual dependency versions recorded',flush=True)
    check=subprocess.run([sys.executable,'-m','pip','check'],capture_output=True,text=True)
    for project in (SHARED,SHARED.parent/'向量化与存储工程（开源组件版）'):
        config=read_json(project/'config.json');batches=[];sources={}
        for file in sorted((project/'outputs').glob('prepared_*/manifest.json')):
            manifest=read_json(file)
            same=manifest['specification']['code']==code and manifest['specification']['config']==config
            validation=file.parent/'prepared_validation.json'
            batch={'directory':file.parent.name,'created_at':manifest['created_at'],
                'sample':manifest['specification']['sample'],'documents':manifest['documents'],
                'text_included':manifest['text_included'],'text_excluded':manifest['text_excluded'],
                'physical_media_files':manifest['physical_media_files'],'same_current_code_and_config':same,
                'validation':read_json(validation) if validation.exists() else {'passed':False,'reason':'no completed validation record'}}
            dedup=project/'.cache/dedup'/manifest['prepared_id']/'receipt.json'
            if dedup.exists():batch['dedup']=read_json(dedup)['summary']
            batches.append(batch)
            if same and not batch['sample']:
                for doc in rows(file.parent/'documents.jsonl'):
                    sources[doc['source_path']]=doc['source_hash']
                for asset in rows(file.parent/'media_assets.jsonl'):
                    for path in asset['paths']:sources[path]=asset['sha256']
        source_mismatches=[p for p,h in sources.items() if file_hash(CORPUS/p)!=h]
        active=project/'当前批次.json'
        active_data=read_json(active) if active.exists() else None
        live_dir=project/active_data['run'] if active_data else None
        live_media=read_json(live_dir/'media_summary.json') if live_dir and (live_dir/'media_summary.json').exists() else None
        probe_file=live_dir/'service_probe.json' if live_dir else SHARED/'.cache/latest-probe.json'
        live_probe=read_json(probe_file) if probe_file.exists() else None
        live_validation=read_json(live_dir/'validation.json') if live_dir and (live_dir/'validation.json').exists() else None
        live_restore=read_json(live_dir/'restore_drill.json') if live_dir and (live_dir/'restore_drill.json').exists() else None
        database_file=SHARED/'.cache/database-bootstrap.json'
        database=read_json(database_file) if database_file.exists() else None
        imported=project/'.cache/source-import-receipt.json'
        receipt={'updated_at':now,'status':'runtime_execution_in_progress' if not active.exists() else 'see_active_pointer',
            'variant':config['variant'],'scope_confirmed':'两版文本+图片识别+语义切块+多模态向量化+PostgreSQL/Qdrant',
            'shared_code_hashes':code,'dependencies_installed':True,'pip_check_passed':check.returncode==0,
            'offline_pytest':tested,'prepared_batches':batches,'input_files_rechecked':len(sources),
            'input_mismatches':source_mismatches,'formal_active_pointer':active_data,
            'ocr_full_run_completed':bool(live_media and live_media['assets']==live_media['recognized_assets'] and not live_media['recognition_failures']),
            'ocr_all_assets_attempted':bool(live_media and live_media['assets']==live_media['recognized_assets']+live_media['recognition_failures']),
            'multimodal_embedding_protocol_verified':bool(live_probe and live_probe.get('visual',{}).get('status')=='passed'),
            'postgres_qdrant_reconciled':bool(live_validation and live_validation['passed']),
            'backup_restore_tested':bool(live_restore and live_restore['passed']),
            'database_bootstrap':database,
            'small_scale_database_protocol_probe':read_json(SHARED/'.cache/database-protocol-probe.json') if (SHARED/'.cache/database-protocol-probe.json').exists() else None,
            'small_scale_restore_filter_probe':read_json(SHARED/'.cache/restore-protocol-recheck.json') if (SHARED/'.cache/restore-protocol-recheck.json').exists() else None,
            'source_import':read_json(imported) if imported.exists() else None,
            'blocking_condition':None,
            'git_delivery':args.git_status}
        if active.exists():
            receipt['status']='active_release_validated' if receipt['postgres_qdrant_reconciled'] else 'active_pointer_requires_verification'
        write_json(project/'实施记录.json',receipt)
        lines=['# 向量化批次时间索引','',f'更新时间：{now}。','',
            '**尚无正式向量批次，没有已激活版本。** 下列都是准备批次，不是完成的向量库。' if not active.exists() else '正式入口以工程当前批次.json及PostgreSQL指针为准。','',
            '| 目录 | 创建时间（北京时间） | 范围 | 文档 | 纳入/排除文本块 | 当前代码配置 | 原文验证 |',
            '|---|---|---|---:|---:|---|---|']
        for b in sorted(batches,key=lambda b:b['created_at']):
            local_time=datetime.fromisoformat(b['created_at']).astimezone(timezone(timedelta(hours=8))).isoformat(timespec='seconds')
            lines.append(f"| [{b['directory']}]({b['directory']}/manifest.json) | {local_time} | {'5篇样例' if b['sample'] else '全量'} | {b['documents']} | {b['text_included']}/{b['text_excluded']} | {'一致' if b['same_current_code_and_config'] else '开发版本'} | {'通过' if b['validation']['passed'] else '未完成'} |")
        newest=next((b for b in sorted(batches,key=lambda b:b['created_at'],reverse=True) if not b['sample'] and b['same_current_code_and_config']),None)
        if newest:lines+=['',f"最新全量准备版本：`{newest['directory']}`。准备版本与向量发布状态分别记录。"]
        if active_data:
            lines+=['',f"**当前已激活版本：[{active_data['generation_id']}]({active_data['run'].replace('outputs/','').replace('outputs'+chr(92),'')}/manifest.json)**。激活时间：{active_data.get('activated_at','未知')}。"]
        generations=[]
        for file in (project/'outputs').glob('generation_*/manifest.json'):
            frozen=read_json(file);folder=file.parent
            check_file=folder/'validation.json'
            check_data=read_json(check_file) if check_file.exists() else None
            generations.append({'directory':folder.name,'time':datetime.fromtimestamp(file.stat().st_mtime,timezone(timedelta(hours=8))).isoformat(timespec='seconds'),
                'validation':check_data,'active':bool(active_data and active_data['generation_id']==folder.name)})
        if generations:
            lines+=['','| 向量批次 | 清单写入时间（北京时间） | 对账 | 当前激活 |','|---|---|---|---|']
            for g in sorted(generations,key=lambda g:g['time']):
                lines.append(f"| [{g['directory']}]({g['directory']}/manifest.json) | {g['time']} | {'通过' if g['validation'] and g['validation']['passed'] else '未通过或未完成'} | {'是' if g['active'] else '否'} |")
        lines+=['','`prepared_*`冻结上游、原文快照、入库资格与媒体；`media_*`冻结识别结果；`generation_*`才承载向量与存储候选。构建失败不切换正式指针。开发样例若归档至.cache/development-runs，保留其数据但不混在正式输出中。','']
        (project/'outputs/README.md').write_text('\n'.join(lines),encoding='utf-8')
        print(project.name,{'batches':len(batches),'original_input_hashes_checked':len(sources),'mismatches':len(source_mismatches)},flush=True)


if __name__=='__main__':main()
