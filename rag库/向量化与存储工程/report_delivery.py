"""从已激活批次的真实清单和验收结果生成交付报告，不运行模型或变更发布指针。

统计分别使用文档、原生子块、唯一图片、出现位置、派生块和来源表示等单位，
不能把缓存复用数量当成来源减少，或把OCR尝试结束当成所有文字均识别正确。
"""
from collections import Counter
from datetime import datetime,timezone,timedelta
from pathlib import Path
from vector_store.common import SHARED,CORPUS,read_json,rows,write_json


def generate(project):
    """输入工程目录，读取正式指针及其冻结副本；缺少任何验收文件即抛错。"""
    pointer=read_json(project/'当前批次.json')
    output=project/pointer['run'];frozen=read_json(output/'manifest.json')
    prepared=Path(frozen['prepared_path']);media=Path(frozen['media_path'])
    manifest=read_json(prepared/'manifest.json')
    validation=read_json(output/'validation.json')
    restore=read_json(output/'restore_drill.json')
    smoke=read_json(output/'storage_smoke.json')
    summary=read_json(output/'embedding_summary.json')
    media_summary=read_json(output/'media_summary.json')
    if not validation['passed'] or not restore['passed'] or not smoke['passed']:
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
        'unique_images':media_summary['assets'],'ocr_parsed_assets':media_summary['recognized_assets'],
        'ocr_asset_status':dict(extraction_status),'ocr_failed_assets':len(failures),
        'visual_parents':media_summary['visual_parents'],'visual_children':media_summary['visual_chunks'],
        'uncertain_visual_children':media_summary['uncertain_visual_chunks'],
        'representations':dict(representations),'embedding':summary,
        'point_set_and_vectors_verified':validation['passed'],'restored_points':restore['points_checked'],
        'backup_restore_verified':restore['passed'],'self_lookup_checks':len(smoke['checks']),
        'semantic_correctness_claimed':False}
    write_json(project/'验收摘要.json',result)
    lines=['# 向量化与存储验收报告','',f'生成时间：{now}。',
        '',f"当前入口：`{pointer['generation_id']}`；激活时间：{pointer['activated_at']}。",
        '',('**已发布带明确缺口的批次。** 识别不确定、超限和已知不合格对象按清单排除。' if result['partial'] else '本批次通过全部确定性发布检查。'),
        '', '| 项目 | 实际数量 |','|---|---:|',
        *[f'| {label} | {value} |' for label,value in [
            ('来源文档',result['documents']),('原生父块',result['parents']),('原生子块',result['native_children']),
            ('原生子块纳入/排除',str(result['native_included'])+'/'+str(result['native_excluded'])),
            ('图片物理文件/唯一字节资源',str(result['physical_images'])+'/'+str(result['unique_images'])),
            ('获得可解析转录的唯一图片',result['ocr_parsed_assets']),('识别失败的唯一图片',len(failures)),
            ('按出现位置派生的视觉父块/子块',str(result['visual_parents'])+'/'+str(result['visual_children'])),
            ('不确定视觉子块',result['uncertain_visual_children']),('来源表示/真实point',summary['representations']),
            ('唯一实际模型输入',summary['unique_inputs']),('本次复用/新增输入向量',str(summary['cache_hits'])+'/'+str(summary['new_input_vectors'])),
            ('恢复后逐点核对',restore['points_checked'])]],
        '', '## 表示构成','', '| 来源类型/表示类型 | 数量 |','|---|---:|',
        *[f'| {key} | {count} |' for key,count in sorted(representations.items())],
        '', '## 实际验收','',
        f"- PostgreSQL、计划清单、Qdrant point身份集合一致，逐点验证{validation['vectors_checked']}条向量及payload，未完成任务{validation['unfinished_tasks']}。",
        '- 重新读取冻结原文、基础单元与父子范围；数据库来源对象、父块、关系及识别派生范围与冻结数据核对。',
        f"- {len(smoke['checks'])}篇固定样例完成真实向量自命中及来源/父块回取。这是存储连通检查，不是检索准确率评估。",
        f"- 已导出PostgreSQL、创建Qdrant快照并复制来源和向量缓存；恢复到新数据库`{restore['database']}`及新collection`{restore['collection']}`，核对真实成员、payload、向量和来源副本。",
        '- 图片bbox为模型估计；转录始终标为识别文本，未经人工逐字校对。程序校验通过不等于政策语义全部正确。',
        '', '## 待处理与排除','',
        f"原生子块排除{result['native_excluded']}条，原因在准备批次的eligibility.jsonl；视觉缺口见[待处理图片](待处理图片.md)。",
        '', '| 表示排除原因 | 条目数 |','|---|---:|',
        *[f'| {key} | {count} |' for key,count in sorted(exclusions.items())],
        '', '上述条目、图片和派生块采用不同计数单位，不能相加当作政策数。缺图引用和归属记录见media_sources.jsonl及media_excluded.jsonl。',
        '', '## 原始验收入口','',
        f"- [独立全量校验]({pointer['run']}/validation.json)",
        f"- [存储回取检查]({pointer['run']}/storage_smoke.json)",
        f"- [备份清单]({pointer['run']}/backup_receipt.json)",
        f"- [真实恢复演练]({pointer['run']}/restore_drill.json)",
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
    pending+=['',f'完整不确定区域及原图bbox：[视觉子块清单](<{(media/"visual_chunks.jsonl").as_posix()}>)；仅status=ready且输入合格的识别子块进入相应表示。',
        '', '输出上限、无法解析、无法识别、不确定文字及完整单元超限分别保留原因。此清单不把未完成的识别拼接成完整政策事实。','']
    (project/'待处理图片.md').write_text('\n'.join(pending),encoding='utf-8')
    return result


if __name__=='__main__':
    results=[generate(p) for p in (SHARED,SHARED.with_name('向量化与存储工程（开源组件版）'))]
    lines=['# 两版向量化与存储实施对照','',
        '两版使用相同的API模型、输入模板和存储规则，分别接入原版及开源组件版切块。共享计算不合并来源身份。','',
        '| 指标 | 原版输入 | 开源组件输入 |','|---|---:|---:|']
    for label,key in [('文档','documents'),('原生父块','parents'),('原生子块','native_children'),
        ('纳入原生子块','native_included'),('排除原生子块','native_excluded'),('唯一图片','unique_images'),
        ('可解析转录图片','ocr_parsed_assets'),('识别失败图片','ocr_failed_assets'),('视觉子块','visual_children'),
        ('不确定视觉子块','uncertain_visual_children'),('恢复后核对point','restored_points')]:
        lines.append(f'| {label} | {results[0][key]} | {results[1][key]} |')
    lines+=['','详细验收分别见本目录验收报告.md及兄弟工程验收报告.md。向量数或块数差异本身不能证明检索质量提升，本轮没有进行有标注问题集的检索准确率对比。','']
    (SHARED/'双版实施对照.md').write_text('\n'.join(lines),encoding='utf-8')
    print('Verified delivery reports generated for both releases.')
