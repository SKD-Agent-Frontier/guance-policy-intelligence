import { useEffect, useRef, useState } from "react";
import {
  ArrowLeft,
  BookOpenText,
  Clock3,
  FilePlus2,
  Home as HomeIcon,
  Settings2,
  ShieldCheck,
  X,
  PanelRightOpen,
} from "lucide-react";
import { Home } from "./components/Home";
import { Results } from "./components/Results";
import { EvidencePanel } from "./components/EvidencePanel";
import { researchClient, type DemoScenario } from "./api/client";
import type { ResearchRequest, RunResult, TaskType } from "./types";

type HistoryItem = {
  id: string;
  request: ResearchRequest;
  result: RunResult;
  scenario: DemoScenario;
  timestamp: number;
};
type View = "home" | "workspace";

const scenarioOptions: { value: DemoScenario; label: string }[] = [
  { value: "success", label: "正常完成" },
  { value: "insufficient", label: "证据不足" },
  { value: "empty", label: "无可用证据" },
  { value: "failed", label: "执行失败" },
  { value: "limit_reached", label: "达到执行限制" },
];
const taskNames: Record<TaskType, string> = {
  qa: "政策问答",
  extract: "要素提取",
  organize: "文献梳理",
  compare: "政策比较",
  compare_plans: "多方案分析",
};

export default function App() {
  const [view, setView] = useState<View>("home");
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [active, setActive] = useState<HistoryItem | null>(null);
  const [activeRequest, setActiveRequest] = useState<
    ResearchRequest | undefined
  >();
  const [selectedChunkId, setSelectedChunkId] = useState<string | null>(null);
  const [isRunning, setIsRunning] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);
  const [scenario, setScenario] = useState<DemoScenario>("success");
  const [dialog, setDialog] = useState<"history" | "settings" | "about" | null>(null);
  const [homeRevision, setHomeRevision] = useState(0);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [showEvidence, setShowEvidence] = useState(false);
  const requestSequence = useRef(0);
  const dialogRef = useRef<HTMLDialogElement>(null);
  const restoreFocusRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (dialog && dialogRef.current && !dialogRef.current.open) {
      dialogRef.current.showModal();
      dialogRef.current
        .querySelector<HTMLElement>(
          "button, input, [tabindex]:not([tabindex='-1'])",
        )
        ?.focus();
    }
  }, [dialog]);

  const openDialog = (name: "history" | "settings" | "about") => {
    restoreFocusRef.current =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    setDialog(name);
  };
  const closeDialog = () => {
    if (dialogRef.current?.open) dialogRef.current.close();
    setDialog(null);
    restoreFocusRef.current?.focus();
  };
  const goHome = () => {
    setView("home");
    if (dialog) closeDialog();
  };

  const submit = async (request: ResearchRequest) => {
    const sequence = ++requestSequence.current;
    const selectedScenario = scenario;
    setActiveRequest(request);
    setActive(null);
    setSelectedChunkId(null);
    setShowEvidence(false);
    setRunError(null);
    setIsRunning(true);
    setView("workspace");
    try {
      const result = await researchClient.submit(request, selectedScenario);
      if (sequence !== requestSequence.current) return;
      const item: HistoryItem = {
        id: result.run_id,
        request,
        result,
        scenario: selectedScenario,
        timestamp: Date.now(),
      };
      setActive(item);
      setHistory((items) => [
        item,
        ...items.filter((existing) => existing.id !== item.id),
      ]);
    } catch (error) {
      if (sequence === requestSequence.current)
        setRunError(
          error instanceof Error
            ? error.message
            : "研究任务暂时无法完成，请重试。",
        );
    } finally {
      if (sequence === requestSequence.current) setIsRunning(false);
    }
  };

  const openHistoryItem = (item: HistoryItem) => {
    requestSequence.current += 1;
    setActive(item);
    setActiveRequest(item.request);
    setScenario(item.scenario);
    setSelectedChunkId(null);
    setShowEvidence(false);
    setRunError(null);
    setIsRunning(false);
    closeDialog();
    setView("workspace");
  };

  const newResearch = () => {
    requestSequence.current += 1;
    setActive(null);
    setActiveRequest(undefined);
    setSelectedChunkId(null);
    setShowEvidence(false);
    setRunError(null);
    setIsRunning(false);
    closeDialog();
    setHomeRevision((value) => value + 1);
    setView("home");
  };

  const current = active?.result;
  const displayTitle = activeRequest?.question ?? "新建研究";
  const runLabel = isRunning
    ? "正在研究"
    : current?.status === "failed"
      ? "执行失败"
      : current?.status === "limit_reached"
        ? "达到执行限制"
        : current
          ? "研究已完成"
          : "准备就绪";

  return (
    <div className="app-shell">
      <div
        className={`home-layer ${view === "home" ? "is-visible" : "is-hidden"}`}
        aria-hidden={view !== "home"}
      >
        <Home
          key={homeRevision}
          onSubmit={submit}
          initialRequest={activeRequest}
          onHistory={() => openDialog("history")}
          onSettings={() => openDialog("settings")}
          onAbout={() => openDialog("about")}
        />
      </div>
      {view === "workspace" && (
        <div
          className={`workspace ${sidebarCollapsed ? "sidebar-collapsed" : ""} ${showEvidence ? "evidence-visible" : ""}`}
        >
          <aside
            className={`workspace-sidebar ${sidebarCollapsed ? "is-collapsed" : ""}`}
          >
            <a
              className="brand workspace-brand"
              href="#workspace"
              onClick={(event) => {
                event.preventDefault();
                newResearch();
              }}
            >
              <span className="brand-mark">
                <BookOpenText size={17} />
              </span>
              {!sidebarCollapsed && (
                <>
                  <span>管策智研</span>
                  <span className="workspace-edition">WORKSPACE</span>
                </>
              )}
            </a>
            <div className="sidebar-primary">
              <button
                type="button"
                className="side-link"
                onClick={goHome}
                title="返回首页"
              >
                <HomeIcon size={16} />
                {!sidebarCollapsed && <span>返回首页</span>}
              </button>
              <button
                type="button"
                className="side-link new-link"
                onClick={newResearch}
                title="新建研究"
              >
                <FilePlus2 size={16} />
                {!sidebarCollapsed && <span>新建研究</span>}
              </button>
            </div>
            {!sidebarCollapsed && (
              <>
                <div className="sidebar-section-title">
                  <span>最近研究</span>
                  <button
                    type="button"
                    aria-label="收起侧栏"
                    title="收起侧栏"
                    className="collapse-button"
                    onClick={() => setSidebarCollapsed(true)}
                  >
                    <ArrowLeft size={14} />
                  </button>
                </div>
                <div className="sidebar-history">
                  {history.length === 0 ? (
                    <div className="sidebar-empty">完成的研究会显示在这里</div>
                  ) : (
                    history.map((item) => (
                      <button
                        key={item.id}
                        className={`history-link ${active?.id === item.id ? "is-current" : ""}`}
                        onClick={() => openHistoryItem(item)}
                        title={item.request.question}
                      >
                        <span className="history-type">
                          {taskNames[item.request.task_type]}
                        </span>
                        <span className="history-question">
                          {item.request.question}
                        </span>
                      </button>
                    ))
                  )}
                </div>
                <div className="sidebar-bottom">
                  <div className="local-demo-note">
                    <ShieldCheck size={14} />
                    <span>
                      本地演示记录
                      <br />
                      仅保存在当前会话
                    </span>
                  </div>
                  <button
                    type="button"
                    className="side-link"
                    onClick={() => openDialog("settings")}
                  >
                    <Settings2 size={15} />
                    <span>演示设置</span>
                  </button>
                </div>
              </>
            )}
            {sidebarCollapsed && (
              <button
                type="button"
                aria-label="展开侧栏"
                title="展开侧栏"
                className="expand-button"
                onClick={() => setSidebarCollapsed(false)}
              >
                ›
              </button>
            )}
          </aside>

          <div className="workspace-main-column">
            <header className="workspace-topbar">
              <div className="workspace-breadcrumb">
                <button
                  type="button"
                  className="breadcrumb-home"
                  onClick={goHome}
                >
                  首页
                </button>
                <span>/</span>
                <span>
                  {activeRequest
                    ? taskNames[activeRequest.task_type]
                    : "新建研究"}
                </span>
              </div>
              <div className="workspace-top-right">
                <span className="demo-badge">演示数据</span>
                <button
                  className="workspace-icon-button"
                  type="button"
                  onClick={() => openDialog("history")}
                  aria-label="研究历史"
                >
                  <Clock3 size={16} />
                </button>
                <button
                  className="workspace-icon-button"
                  type="button"
                  onClick={() => openDialog("settings")}
                  aria-label="演示设置"
                >
                  <Settings2 size={16} />
                </button>
                <button
                  className="evidence-toggle"
                  type="button"
                  onClick={() => setShowEvidence(true)}
                  disabled={!current}
                >
                  <PanelRightOpen size={14} />
                  证据
                </button>
              </div>
            </header>
            <main className="workspace-content">
              <div className="workspace-heading">
                <div className="workspace-heading-eyebrow">
                  {activeRequest
                    ? taskNames[activeRequest.task_type]
                    : "研究工作区"}
                  <span className="heading-dot" />
                  {runLabel}
                </div>
                <h1>{displayTitle}</h1>
                <div className="workspace-status-row">
                  <span
                    className={`run-status ${isRunning ? "is-loading" : current?.status === "failed" ? "is-failed" : current?.status === "limit_reached" ? "is-limited" : ""}`}
                  >
                    <i />
                    {isRunning
                      ? "正在整理政策依据…"
                      : current?.status === "failed"
                        ? "演示任务执行失败，可重新提交"
                        : current?.status === "limit_reached"
                          ? "演示任务达到执行限制"
                          : current
                            ? "演示数据 · 非真实模型调用"
                            : "填写研究问题以开始"}
                  </span>
                  {current && (
                    <span
                      className={`evidence-summary evidence-${current.evidence_status}`}
                    >
                      {current.evidence_status === "verified"
                        ? "引文字面核验：已通过"
                        : current.evidence_status === "insufficient"
                          ? "证据不足"
                          : "无可核验的证据"}
                    </span>
                  )}
                  {active && (
                    <span className="run-time">
                      {new Date(active.timestamp).toLocaleString("zh-CN", {
                        dateStyle: "medium",
                        timeStyle: "short",
                      })}
                    </span>
                  )}
                </div>
                {current?.error && (
                  <div className="result-status-message" role="status">
                    {current.error.message}
                  </div>
                )}
                {current?.warnings.length ? (
                  <ul className="result-warnings" aria-label="研究提醒">
                    {current.warnings.map((warning, index) => (
                      <li key={`${index}-${warning}`}>{warning}</li>
                    ))}
                  </ul>
                ) : null}
                {current?.status === "failed" && (
                  <button
                    type="button"
                    className="retry-inline"
                    onClick={() => activeRequest && submit(activeRequest)}
                  >
                    重试本次研究
                  </button>
                )}
              </div>
              {isRunning && (
                <div className="loading-panel" role="status" aria-live="polite">
                  <span className="loading-spinner" />
                  <div>
                    <strong>正在准备研究结果</strong>
                    <p>演示任务正在生成，请稍候…</p>
                  </div>
                </div>
              )}
              {runError && (
                <div className="error-panel" role="alert">
                  <strong>研究暂时中断</strong>
                  <p>{runError}</p>
                  <button
                    type="button"
                    onClick={() => activeRequest && submit(activeRequest)}
                  >
                    重试
                  </button>
                </div>
              )}
              {!isRunning && !runError && current && (
                <Results
                  result={current}
                  onEvidence={(chunkId) => {
                    setSelectedChunkId(chunkId);
                    setShowEvidence(true);
                  }}
                />
              )}
              {!isRunning && !runError && !current && (
                <div className="workspace-empty">
                  <div className="empty-icon">
                    <FilePlus2 size={20} />
                  </div>
                  <strong>从一个研究问题开始</strong>
                  <p>返回首页选择研究模式，并提交你的问题或政策对象。</p>
                  <button type="button" onClick={goHome}>
                    返回首页 <ArrowLeft size={14} />
                  </button>
                </div>
              )}
            </main>
          </div>
          {current && (
            <aside
              className={`evidence-column ${showEvidence ? "has-selection" : ""}`}
              aria-label="引用证据"
            >
              <EvidencePanel
                result={current}
                selectedChunkId={selectedChunkId}
                onClose={() => setShowEvidence(false)}
              />
            </aside>
          )}
        </div>
      )}

      {dialog && (
        <dialog
          ref={dialogRef}
          className="app-dialog"
          aria-labelledby="dialog-title"
          onCancel={(event) => {
            event.preventDefault();
            closeDialog();
          }}
          onClick={(event) => {
            if (event.target === event.currentTarget) closeDialog();
          }}
        >
          <header>
            <div>
              <span className="dialog-eyebrow">GUANCE RESEARCH</span>
              <h2 id="dialog-title">
                {dialog === "settings"
                  ? "演示设置"
                  : dialog === "about"
                    ? "关于管策智研"
                    : "研究历史"}
              </h2>
            </div>
            <button
              type="button"
              className="dialog-close"
              aria-label="关闭"
              onClick={closeDialog}
            >
              <X size={17} />
            </button>
          </header>
          {dialog === "settings" ? (
            <div className="settings-content">
              <div className="setting-block">
                <strong>演示状态</strong>
                <p>选择提交研究时展示的演示结果状态。</p>
                <div className="scenario-options">
                  {scenarioOptions.map((item) => (
                    <label
                      key={item.value}
                      className={`scenario-option ${scenario === item.value ? "is-selected" : ""}`}
                    >
                      <input
                        type="radio"
                        name="demo-scenario"
                        value={item.value}
                        checked={scenario === item.value}
                        onChange={() => setScenario(item.value)}
                      />
                      <span className="radio-indicator" />
                      <span>{item.label}</span>
                    </label>
                  ))}
                </div>
              </div>
              <div className="setting-disclaimer">
                <ShieldCheck size={15} />
                所有结果均为本地演示数据，不来自真实模型调用。
              </div>
            </div>
          ) : dialog === "history" ? (
            <div className="dialog-history">
              {history.length === 0 ? (
                <div className="history-dialog-empty">
                  <Clock3 size={20} />
                  <strong>还没有研究记录</strong>
                  <p>完成一次演示研究后，记录会出现在这里。</p>
                </div>
              ) : (
                history.map((item) => (
                  <button
                    key={item.id}
                    type="button"
                    className="dialog-history-item"
                    onClick={() => openHistoryItem(item)}
                  >
                    <span className="dialog-history-type">
                      {taskNames[item.request.task_type]}
                      <i />
                      {item.result.status === "completed"
                        ? "已完成"
                        : item.result.status === "failed"
                          ? "执行失败"
                          : "达到限制"}
                    </span>
                    <strong>{item.request.question}</strong>
                    <small>
                      {new Date(item.timestamp).toLocaleString("zh-CN", {
                        dateStyle: "medium",
                        timeStyle: "short",
                      })}
                    </small>
                  </button>
                ))
              )}
            </div>
          ) : (
            <div className="settings-content about-content">
              <div className="setting-block">
                <strong>基于权威政策文献的专业分析平台</strong>
                <p>
                  管策智研通过政策问答、要素提取、文献梳理、政策比较和多方案分析，帮助研究者理解与分析公共政策。
                </p>
              </div>
              <div className="setting-disclaimer">
                <ShieldCheck size={15} />
                当前为本地演示环境，研究结果使用演示数据。
              </div>
            </div>
          )}
          <footer>
            <button type="button" className="dialog-done" onClick={closeDialog}>
              完成
            </button>
          </footer>
        </dialog>
      )}
    </div>
  );
}
