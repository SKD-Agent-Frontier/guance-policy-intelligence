# D：Deep Agents + 上下文 Skills + MCP

**真实运行路径：** 沿用原项目 Qwen 与只读 RAG，使用 Deep Agents 的 StateBackend 把上游 Skill 文件作为内存虚拟文件加载；借助 LangChain MCPAdapter 接入官方 Sequential Thinking 与 Memory MCP。无新增政策核心 Skill。

## 准备（Windows PowerShell，在 agent/ 下）
```powershell
py -3.11 -m venv .venv-d
.venv-d\Scripts\python.exe -m pip install -e .
.venv-d\Scripts\python.exe -m pip install "deepagents>=0.7.23,<1" "langchain-openai>=1" "langchain[mcp]>=1.4"
.venv-d\Scripts\python.exe experiments/install_skills.py
.venv-d\Scripts\python.exe experiments/install_skills.py --check
# 安装 Node.js 后验证 node -v 和 npx -v
.venv-d\Scripts\python.exe experiments/run.py --question "甲市十四五农业农村规划第二章有哪些重点任务？" --all
```

### 配置可消融
- 无 flag：等价 C 的底座配置（运行时不自动启用 Skill/MCP）。
- `--skills`：启用 Planning with Files + Context Engineering 社区技能集（只导入 Skill/参考文本；其特定 IDE lifecycle hooks 在本 Harness 中不会运行）。
- `--thinking`：启用官方 Sequential Thinking MCP。
- `--memory`：启用官方 Memory MCP；专用数据文件写入 experiments/runtime_data/，不使用用户会话。
- `--all`：全部启用。

**安全：** 完整上游仓库从锁定的 commit 下载到被忽略的 third_party_skills/；项目代码只读取 UTF-8 文本到虚拟 StateBackend，绝不运行外部 Skill 脚本。Superpowers 的 Debugging/Verification 仅供开发 Agent 使用，不注入政策问答运行时。MCP 需要本地 npx 安装，初次启动可能联网。不要在高权限机器上执行未经审查的第三方代码；勿将密钥放入 Skill 库。

## 状态与限制
- 已提交真实接入代码和安装脚本；需要在具有网络、Node.js、学校 Qwen API、已恢复 RAG 的机器上执行安装和实测。
- MCP 依赖和 180 秒时限可能导致失败；记录失败类型，不把「安装成功」误写成「问答验收通过」。
- 结果标识 unreviewed，未替代现有的引用与语义审核。
- 上游版本已锁定 Git commit；升级需单独检验。
