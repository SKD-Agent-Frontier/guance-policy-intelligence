import { useEffect, useRef, useState } from "react";
import { X } from "lucide-react";
import { Home } from "./components/Home";
import { Sidebar } from "./components/Sidebar";
import { ConversationView } from "./components/ConversationView";
import { PolicyCatalog } from "./components/PolicyCatalog";
import { TopicsPage, type TopicSelection } from "./components/TopicsPage";
import { addPolicyToTopic, loadTopicCollections, newTopicCollection, removePolicyFromTopic, saveTopicCollections, TOPIC_STORAGE_KEY, type TopicCollection, type TopicPolicy } from "./topicStore";
import { isRealMode, type DemoScenario } from "./api/client";
import { useConversations } from "./hooks/useConversations";
import { createAdvancedDraft } from "./conversationTypes";
import type { ResearchRequest, TaskType } from "./types";

type Page = "home" | "workspace" | "catalog" | "topics";
type Dialog = "settings" | null;

const scenarioOptions: { value: DemoScenario; label: string }[] = [
  { value: "success", label: "正常完成" },
  { value: "insufficient", label: "证据不足" },
  { value: "empty", label: "无可用证据" },
  { value: "failed", label: "执行失败" },
  { value: "limit_reached", label: "达到执行限制" },
];

export default function App() {
  const { sessions, activeId, active, selectConversation, renameConversation, deleteConversation, newConversation, setMode, send, runAdvanced, retry, regenerate, editAndResend, restartPreparation, updateAdvancedDraft, openAdvanced } = useConversations();
  const [page, setPage] = useState<Page>("home");
  const [topicSelection, setTopicSelection] = useState<TopicSelection>(null);
  const [topicCollections, setTopicCollections] = useState<TopicCollection[]>(loadTopicCollections);
  const topicsRef = useRef(topicCollections);
  const [topicSaveError, setTopicSaveError] = useState(false);
  const [requestedPolicyId, setRequestedPolicyId] = useState<string | null>(null);
  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [dialog, setDialog] = useState<Dialog>(null);
  const [scenario, setScenario] = useState<DemoScenario>("success");
  const [homeDraft, setHomeDraft] = useState("");
  const [homeMode, setHomeMode] = useState<TaskType>("qa");
  const [homeAdvancedDraft, setHomeAdvancedDraft] = useState(createAdvancedDraft);
  const dialogRef = useRef<HTMLDialogElement>(null);
  const restoreFocusRef = useRef<HTMLElement | null>(null);
  const researchPageBeforeCatalog = useRef<"home" | "workspace">("home");

  const updateTopics = (next: TopicCollection[]): boolean => {
    if (!saveTopicCollections(next)) {
      setTopicSaveError(true);
      return false;
    }
    topicsRef.current = next;
    setTopicCollections(next);
    setTopicSaveError(false);
    return true;
  };
  const createTopic = (name: string): string | null => {
    try {
      const topic = newTopicCollection(name);
      if (topicsRef.current.some((item) => item.name.toLocaleLowerCase() === topic.name.toLocaleLowerCase())) return null;
      return updateTopics([...topicsRef.current, topic]) ? topic.id : null;
    } catch {
      return null;
    }
  };
  const addToTopic = (id: string, policy: TopicPolicy) => {
    updateTopics(addPolicyToTopic(topicsRef.current, id, policy));
  };
  const removeFromTopic = (id: string, planid: string) => {
    updateTopics(removePolicyFromTopic(topicsRef.current, id, planid));
  };
  const deleteTopic = (id: string) => {
    updateTopics(topicsRef.current.filter((topic) => topic.id !== id));
  };
  useEffect(() => {
    const syncFromOtherTab = (event: StorageEvent) => {
      if (event.key !== TOPIC_STORAGE_KEY) return;
      const next = loadTopicCollections();
      topicsRef.current = next;
      setTopicCollections(next);
    };
    window.addEventListener("storage", syncFromOtherTab);
    return () => window.removeEventListener("storage", syncFromOtherTab);
  }, []);


  useEffect(() => {
    if (dialog && dialogRef.current && !dialogRef.current.open) {
      dialogRef.current.showModal();
      dialogRef.current.querySelector<HTMLElement>("button, input, [tabindex]:not([tabindex='-1'])")?.focus();
    }
  }, [dialog]);
  useEffect(() => {
    if (!mobileOpen) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMobileOpen(false);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [mobileOpen]);
  const openDialog = (nextDialog: Exclude<Dialog, null>) => {
    restoreFocusRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    setDialog(nextDialog);
  };
  const closeDialog = () => {
    if (dialogRef.current?.open) dialogRef.current.close();
    setDialog(null);
    restoreFocusRef.current?.focus();
  };
  // The landing page is a fresh composer, never a mirror of the selected history session.
  const resetHome = () => {
    setHomeDraft("");
    setHomeMode("qa");
    setHomeAdvancedDraft(createAdvancedDraft());
  };
  const goHome = () => {
    resetHome();
    setPage("home");
  };
  const beginHomeMessage = (message: string) => {
    if (!message.trim()) return;
    newConversation(homeMode);
    resetHome();
    setPage("workspace");
    void send(message, scenario);
  };
  const beginHomeAdvanced = (request: ResearchRequest) => {
    newConversation(request.task_type);
    resetHome();
    setPage("workspace");
    void runAdvanced(request, scenario);
  };
  const beginWorkspaceMessage = (message: string) => {
    void send(message, scenario);
  };
  const beginWorkspaceAdvanced = (request: ResearchRequest) => {
    void runAdvanced(request, scenario);
  };
  const startNew = () => {
    // A draft isn't a conversation until the first message is submitted.
    goHome();
    closeDialog();
  };
  const openConversation = (id: string) => {
    selectConversation(id);
    setPage("workspace");
  };
  const openCatalog = () => {
    if (page === "home" || page === "workspace") researchPageBeforeCatalog.current = page;
    setRequestedPolicyId(null);
    setPage("catalog");
  };
  const openTopics = () => {
    if (page === "home" || page === "workspace") researchPageBeforeCatalog.current = page;
    setRequestedPolicyId(null);
    setPage("topics");
  };
  const openPolicyFromTopics = (planid: string) => {
    setRequestedPolicyId(planid);
    setPage("catalog");
  };
  const goResearch = () => {
    // The tab is navigation, not a shortcut back to whichever chat happened to be active.
    if (page === "catalog" || page === "topics") setPage(researchPageBeforeCatalog.current);
  };

  return <div className={`app-shell app-page-${page}`}>
    <Sidebar
      conversations={sessions}
      activeId={page === "workspace" ? activeId : null}
      collapsed={collapsed}
      mobileOpen={mobileOpen}
      onExpand={() => {
        if (window.matchMedia?.("(max-width: 768px)").matches) setMobileOpen(true);
        else setCollapsed(false);
      }}
      onToggle={() => {
        if (mobileOpen) setMobileOpen(false);
        else setCollapsed((value) => !value);
      }}
      onCloseMobile={() => setMobileOpen(false)}
      onHome={goHome}
      onNew={startNew}
      onSelect={openConversation}
      onRename={renameConversation}
      onDelete={(id) => { deleteConversation(id); if (id === activeId && page === "workspace") goHome(); }}
      onSettings={() => openDialog("settings")}
    />
    <div className={`app-content ${collapsed ? "sidebar-is-collapsed" : ""}`}>
      <header className="global-topbar">
        <div className="topbar-leading">
          {page !== "home" && <button type="button" className="global-brand" onClick={goHome} aria-label="管策智研首页"><span className="brand-mark"><img src="/guance-mark.svg" alt="" /></span><span>管策智研</span></button>}
        </div>
        <nav className="global-nav" aria-label="主导航">
          <button type="button" className={page === "home" || page === "workspace" ? "is-current" : ""} onClick={goResearch}>研究</button>
          <button type="button" className={page === "catalog" ? "is-current" : ""} onClick={openCatalog}>文献</button>
          <button type="button" className={page === "topics" ? "is-current" : ""} onClick={openTopics}>专题</button>
        </nav>
      </header>

      {page === "home" && <div className="page-body page-body-home"><Home mode={homeMode} value={homeDraft} busy={false} onChange={setHomeDraft} onModeChange={(mode) => { setHomeMode(mode); setHomeAdvancedDraft(createAdvancedDraft()); }} onSend={beginHomeMessage} onAdvancedSubmit={beginHomeAdvanced} advancedDraft={homeAdvancedDraft} onAdvancedDraftChange={(patch) => setHomeAdvancedDraft((current) => ({ ...current, ...patch }))} onOpenCatalog={openCatalog} /></div>}
      {page === "workspace" && active && <div className="page-body page-body-workspace"><ConversationView conversation={active} onModeChange={setMode} onSend={beginWorkspaceMessage} onAdvancedSubmit={beginWorkspaceAdvanced} onRetry={() => void retry()} onRegenerate={(id) => void regenerate(id, scenario)} onEditAndResend={(id, text) => void editAndResend(id, text, scenario)} onRestartPreparation={() => void restartPreparation()} onAdvancedDraftChange={updateAdvancedDraft} onSpecifyPolicies={openAdvanced} onOpenCatalog={openCatalog} /></div>}
      {page === "catalog" && <div className="page-body page-body-catalog"><PolicyCatalog
        collections={topicCollections} onCreateTopic={createTopic} onAddPolicy={addToTopic}
        requestedPolicyId={requestedPolicyId} onBackToTopics={requestedPolicyId ? openTopics : undefined}
      /></div>}
      {page === "topics" && <div className="page-body page-body-topics"><TopicsPage
        collections={topicCollections} onCreate={createTopic} onAddPolicy={addToTopic}
        onRemovePolicy={removeFromTopic} onDelete={deleteTopic} onOpenPolicy={openPolicyFromTopics}
        selection={topicSelection} onSelectionChange={setTopicSelection}
      /></div>}
      {topicSaveError && <div className="topic-storage-error" role="alert">专题保存失败：请检查浏览器本地存储空间或权限。</div>}
    </div>

    {dialog && <dialog ref={dialogRef} className="global-dialog" aria-labelledby="global-dialog-title" onCancel={(event) => { event.preventDefault(); closeDialog(); }} onClick={(event) => { if (event.target === event.currentTarget) closeDialog(); }}>
        <header className="global-dialog-header"><div><span className="dialog-eyebrow">管策智研</span><h2 id="global-dialog-title">身份与环境设置</h2></div><button type="button" aria-label="关闭" onClick={closeDialog}><X size={18} /></button></header>
        {dialog === "settings" && <div className="global-dialog-content"><div className="identity-status"><span>学号</span><strong>未连接身份认证</strong></div><div className="setting-block"><strong>当前运行环境</strong><p>{isRealMode ? "Real · 使用已配置的真实后端" : "Demo · 本地演示数据"}</p></div>{!isRealMode && <div className="setting-block"><strong>本地演示情景</strong><p>仅影响 Demo 演示，不会在真实环境失败时切换数据。</p><div className="scenario-options">{scenarioOptions.map((option) => <label key={option.value} className={`scenario-option ${scenario === option.value ? "is-selected" : ""}`}><input type="radio" name="demo-scenario" value={option.value} checked={scenario === option.value} onChange={() => setScenario(option.value)} /><span className="radio-indicator" />{option.label}</label>)}</div></div>}<p className="setting-disclaimer">当前没有登录或学号认证功能。</p></div>}
    </dialog>}
  </div>;
}
