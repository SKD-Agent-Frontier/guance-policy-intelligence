# QA 真实验收题集

- `qa_quality_regression.json`：原诊断的 12 道开发失败回归题，包含少量人工核对的预期关键词。`expected` 只用于人工对照，不发送给模型，也不是完整金标。
- `qa_quality_extended.json`：30 道扩展挑战题，覆盖指定事实、开放主题、比较、改写、多轮及边界场景。没有专家逐题标注，且其中的毕业生改写模式已用于开发回归测试，因此**不是独立盲测集**，不能据此报告通用准确率或 Recall@20。

在安装 Agent、Web backend 和实际 RAG 依赖的环境中，使用本地配置与独立的已核对语料快照运行：

```powershell
python agent/scripts/qa_quality_acceptance.py --source . --env-file <local-env-file> --store-project <independent-rag-snapshot> --cases agent/benchmarks/qa_quality_regression.json --output <outside-repo-output> --repeats 3
```

扩展题可替换 `--cases`，使用 `--repeats 1 --concurrency 3` 做进程内负载探索；这时耗时包含并发排队，不能与串行延迟直接比较。输出目录必须在仓库外，包含原始模型输入输出、独立会话数据库和运行日志。请勿提交原始输出或本地配置。

脚本要求真实 RAG backend，不会退回 Mock。`--store-project` 必须指向独立快照，避免争抢正在使用的生产索引锁。原文路径若由快照引用，应保持只读。脚本记录生产源码及题集 SHA256，拒绝向混合代码版本、不同题集或并发配置的已有目录续跑。已有单题文件会跳过，因此失败样本也不会被静默重试替换。

`completed` 表示请求生命周期结束，`verified` 表示内部证据状态；二者都不能代替人工核对的切题性、事实正确性、任务覆盖与适用条件。真实请求之外还需分别验收来源/版本绑定、原句、数字、目标与成效的区分、合理拒答。进程内 ASGI 测试不包含浏览器、网络代理或操作系统进程托管的长稳验收。
