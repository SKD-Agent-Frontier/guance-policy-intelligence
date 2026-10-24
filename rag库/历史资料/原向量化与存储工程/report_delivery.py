"""从已激活批次的真实清单和验收结果生成交付报告，不运行模型或变更发布指针。

统计分别使用文档、原生子块、唯一图片、出现位置、派生块和来源表示等单位，
不能把缓存复用数量当成来源减少，或把OCR尝试结束当成所有文字均识别正确。
"""
from collections import Counter
from datetime import datetime,timezone,timedelta
from pathlib import Path
from vector_store.common import SHARED,CORPUS,read_json,rows,write_json,file_hash


def generate(project):
    """读取正式指针与确定性验收；本次恢复演练和历史恢复证据分别报告。

    补录沿用已验证的数据库及备份实现，不能因未重复恢复演练就伪造一份通过记录，
    也不把历史恢复的point数写成本批次的point数。
    """
    pointer=read_json(project/'当前批次.json')
    run=Path(pointer['run']).as_posix()
    output=project/pointer['run'];frozen=read_json(output/'manifest.json')
    prepared=Path(frozen['prepared_path']);media=Path(frozen['media_path'])
    manifest=read_json(prepared/'manifest.json')
    validation=read_json(output/'validation.json')
    restore=read_json(output/'restore_drill.json') if (output/'restore_drill.json').exists() else None
    history=[]
    for file in project.glob('outputs/generation_*/restore_drill.json'):
        past=read_json(file)
        if past.get('passed') and file.parent!=output:
            history.append({'generation_id':past['generation_id'],'points_checked':past['points_checked'],
                'report':file.relative_to(project).as_posix(),'passed':True})
    smoke=read_json(output/'storage_smoke.json')
    summary=read_json(output/'embedding_summary.json')
    media_summary=read_json(output/'media_summary.json')
    if not validation['passed'] or (restore is not None and not restore['passed']) or not smoke['passed']:
        raise ValueError('cannot deliver an unverified active release')
    representations=Counter(r['source_type']+'/'+r['representation_type'] for r in rows(output/'representations.jsonl'))
    exclusions=Counter(r['reason'] for r in rows(output/'representation_excluded.jsonl'))
    extraction_status=Counter(r['status'] for r in rows(media/'media_extractions.jsonl'))
    failures=list(rows(media/'media_failures.jsonl'))
    assets={a['id']:a for a in rows(prepared/'media_assets.jsonl')}
    now=datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='seconds')
    result={'updated_at':now,'generation_id':pointer['generation_id'],
        'activation_time':pointer['activated_at'],'partial':pointer.get('partial',False),
        'documents':manifest['documents'],'parents':manifest['counts']['parents'],
        'native_children':manifest['counts']['chunks'],'native_included':manifest['text_included'],
        'native_excluded':manifest['text_excluded'],'physical_images':manifest['physical_media_files'],
        'supplemental_parent_contexts':manifest.get('supplemental_parent_contexts',0),
        'content_coverage':validation.get('coverage'),
        'unique_images':media_summary['assets'],'ocr_parsed_assets':media_summary['recognized_assets'],
        'ocr_asset_status':dict(extraction_status),'ocr_failed_assets':len(failures),
        'visual_parents':media_summary['visual_parents'],'visual_children':media_summary['visual_chunks'],
        'uncertain_visual_children':media_summary['uncertain_visual_chunks'],
        'representations':dict(representations),'embedding':summary,
        'point_set_and_vectors_verified':validation['passed'],'restored_points':restore['points_checked'] if restore else None,
        'backup_restore_verified':restore['passed'] if restore else False,'historical_restore_evidence':history,
        'self_lookup_checks':len(smoke['checks']),
        'semantic_correctness_claimed':False}
    snapshot=output/'code_snapshot/receipt.json'
    if snapshot.exists():
        recorded=read_json(snapshot)
        if recorded['files']!=manifest['specification']['code']:
            raise ValueError('runtime code snapshot manifest mismatch')
        for name,checksum in recorded['files'].items():
            relative=Path('vector_store')/name if name.endswith('.py') else Path(name)
            if file_hash(output/'code_snapshot'/relative)!=checksum:
                raise ValueError('runtime code snapshot changed: '+name)
        result['runtime_code_snapshot_verified']=True
    health_file=SHARED/'.cache/current-runtime.json'
    if health_file.exists():
        health=read_json(health_file)
        variant=read_json(project/'config.json')['variant']
        if health['releases'][variant]['generation_id']==pointer['generation_id']:
            result['runtime_health']={'checked_at':health['at'],**health['releases'][variant]}
    write_json(project/'验收摘要.json',result)
    lines=['# 向量化与存储验收报告','',f'生成时间：{now}。',
        '',f"当前入口：`{pointer['generation_id']}`；激活时间：{pointer['activated_at']}。",
        '',('**文本及现有图片完整覆盖批次。** 原生子块全部入库，质量标签仅作提示；超长输入分片，父块保留完整原文。缺图引用与OCR未完成项仍明确记录。'
            if manifest['specification']['config'].get('coverage_policy')=='all_existing_content'
            else ('**已发布带明确缺口的批次。** 识别不确定、超限和已知不合格对象按清单排除。' if result['partial'] else '本批次通过全部确定性发布检查。')),
        '', '| 项目 | 实际数量 |','|---|---:|',
        *[f'| {label} | {value} |' for label,value in [
            ('来源文档',result['documents']),('原生父块',result['parents']),('原生子块',result['native_children']),
            ('补充完整父级上下文',result['supplemental_parent_contexts']),
            ('原生子块纳入/排除',str(result['native_included'])+'/'+str(result['native_excluded'])),
            ('图片物理文件/唯一字节资源',str(result['physical_images'])+'/'+str(result['unique_images'])),
            ('获得可解析转录的唯一图片',result['ocr_parsed_assets']),('识别失败的唯一图片',len(failures)),
            ('按出现位置派生的视觉父块/子块',str(result['visual_parents'])+'/'+str(result['visual_children'])),
            ('不确定视觉子块',result['uncertain_visual_children']),('来源表示/真实point',summary['representations']),
            ('唯一实际模型输入',summary['unique_inputs']),('本次复用/新增输入向量',str(summary['cache_hits'])+'/'+str(summary['new_input_vectors'])),
            ('本批次恢复后核对',restore['points_checked'] if restore else '本次补录未重复恢复演练')]],
        '', '## 表示构成','', '| 来源类型/表示类型 | 数量 |','|---|---:|',
        *[f'| {key} | {count} |' for key,count in sorted(representations.items())],
        '', '## 实际验收','',
        f"- PostgreSQL、计划清单、Qdrant point身份集合一致，逐点验证{validation['vectors_checked']}条向量及payload，未完成任务{validation['unfinished_tasks']}。",
        '- 重新读取冻结原文、基础单元与父子范围；数据库来源对象、父块、关系及识别派生范围与冻结数据核对。',
        (f"- 内容覆盖：原生子块{validation['coverage']['native_covered']}/{validation['coverage']['native_chunks']}；视觉子块{validation['coverage']['visual_covered']}/{validation['coverage']['visual_chunks']}；现存原图出现{validation['coverage']['original_images_covered']}/{validation['coverage']['original_image_occurrences']}。超长输入分片按码点范围无缺口重建，逐片重编码核验。" if validation.get('coverage') else '- 此历史批次没有独立内容覆盖记录。'),
        f"- {len(smoke['checks'])}篇固定样例完成真实向量自命中及来源/父块回取。这是存储连通检查，不是检索准确率评估。",
        (f"- 已导出PostgreSQL、创建Qdrant快照并复制来源和向量缓存；恢复到新数据库`{restore['database']}`及新collection`{restore['collection']}`，核对真实成员、payload、向量和来源副本。" if restore else '- 本次补录未重复执行整库备份恢复演练；原有已验证备份保留，历史证据单独列出，不冒充本批次恢复结果。'),
        '- 图片bbox为模型估计；转录始终标为识别文本，未经人工逐字校对。程序校验通过不等于政策语义全部正确。',
        '- 历史基线曾遇Windows后台优化器重命名拒绝访问；当时仅重启本工程Qdrant即恢复green/ok。当前批次健康状态以验收摘要中匹配generation的runtime_health记录为准。维护方式见共享README。',
        '', '## 待处理与排除','',
        f"原生子块排除{result['native_excluded']}条，原因在准备批次的eligibility.jsonl；视觉缺口见[待处理图片](待处理图片.md)。",
        '', '| 表示排除原因 | 条目数 |','|---|---:|',
        *[f'| {key} | {count} |' for key,count in sorted(exclusions.items())],
        '', '上述条目、图片和派生块采用不同计数单位，不能相加当作政策数。缺图引用和归属记录见media_sources.jsonl及media_excluded.jsonl。',
        '', '## 原始验收入口','',
        f"- [独立全量校验]({run}/validation.json)",
        f"- [存储回取检查]({run}/storage_smoke.json)",
        *([f"- [本批次备份清单]({run}/backup_receipt.json)"] if (output/'backup_receipt.json').exists() else []),
        *([f"- [本批次真实恢复演练]({run}/restore_drill.json)"] if restore else []),
        *[f"- [历史恢复证据：{past['generation_id']}]({past['report']})" for past in history],
        *([f"- [本批次实际执行的代码快照及逐文件哈希]({run}/code_snapshot/receipt.json)"] if snapshot.exists() else []),
        '- [批次时间及最新版本](outputs/README.md)','']
    (project/'验收报告.md').write_text('\n'.join(lines),encoding='utf-8')
    pending=['# 图片待处理清单','',f'更新时间：{now}。',
        '',f'识别失败的唯一图片：{len(failures)}。不确定视觉子块：{result["uncertain_visual_children"]}（按出现位置计）。',
        '', '| 原图 | 原因 | 原始响应证据 |','|---|---|---|']
    for failure in failures:
        asset=assets[failure['asset_id']];path=asset['paths'][0]
        artifact=failure.get('raw_attempt_artifact')
        link=f'[{Path(path).name}](<{(CORPUS/path).as_posix()}>)'
        evidence=f'`{artifact}`（共享工程内）' if artifact else '该次早期调用未保存响应正文；保留实际调用摘要'
        pending.append(f"| {link} | {failure['reason']} | {evidence} |")
    pending+=['',f'完整不确定区域及原图bbox：[视觉子块清单](<{(media/"visual_chunks.jsonl").as_posix()}>)；当前完整覆盖模式已将不确定识别文本向量化，原有pending状态及识别提示保留。识别失败但原图存在的资源可通过原图向量检索，不能据此声称OCR已成功。',
        '', '输出上限、无法解析、无法识别、不确定文字及完整单元超限分别保留原因。此清单不把未完成的识别拼接成完整政策事实。','']
    (project/'待处理图片.md').write_text('\n'.join(pending),encoding='utf-8')
    return result


if __name__=='__main__':
    # 双版实施对照作为历史证据保留，不再读取已删除工程或重写历史统计。
    generate(SHARED)
    print('Verified delivery report generated for the retained release.')
