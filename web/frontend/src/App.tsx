import { useEffect, useRef, useState } from "react";
import { BookOpenText, Menu, Settings2, X } from "lucide-react";
import { Home } from "./components/Home";
import { Sidebar } from "./components/Sidebar";
import { ConversationView } from "./components/ConversationView";
import { PolicyCatalog } from "./components/PolicyCatalog";
import { isRealMode, type DemoScenario } from "./api/client";
import { useConversations } from "./hooks/useConversations";
import type { ResearchRequest } from "./types";

type Page = "home" | "workspace" | "catalog";
type Dialog = "settings" | "about" | "data" | null;

const scenarioOptions: { value: DemoScenario; label: string }[] = [
  { value: "success", label: "正常完成" },
  { value: "insufficient", label: "证据不足" },
  { value: "empty", label: "无可用证据" },
  { value: "failed", label: "执行失败" },
  { value: "limit_reached", label: "达到执行限制" },
];

export default function App() {
  const { sessions, activeId, active, selectConversation, newConversation, setMode, send, runAdvanced, retry, restartPreparation } = useConversations();
  const [page, setPage] = useState<Page>("home");
  const [collapsed, setCollapsed] = useState(true);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [dialog, setDialog] = useState<Dialog>(null);
  const [scenario, setScenario] = useState<DemoScenario>("success");
  const [homeDraft, setHomeDraft] = useState("");
  const dialogRef = useRef<HTMLDialogElement>(null);
  const restoreFocusRef = useRef<HTMLElement | null>(null);

  const mode = active?.mode ?? "qa";
  const busy = active ? active.busy !== "idle" : false;
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
  const beginMessage = (message: string) => {
    setPage("workspace");
    void send(message, scenario);
  };
  const beginAdvanced = (request: ResearchRequest) => {
    setPage("workspace");
    void runAdvanced(request, scenario);
  };
  const startNew = () => {
    newConversation();
    setHomeDraft("");
    setPage("home");
    closeDialog();
  };
  const openConversation = (id: string) => {
    selectConversation(id);
    setPage("workspace");
  };
  const goResearch = () => setPage(active?.messages.length ? "workspace" : "home");

  return <div className={`app-shell app-page-${page}`}>
    <Sidebar
      conversations={sessions}
      activeId={activeId}
      collapsed={collapsed}
      mobileOpen={mobileOpen}
      onToggle={() => {
        if (mobileOpen) setMobileOpen(false);
        else setCollapsed((value) => !value);
      }}
      onCloseMobile={() => setMobileOpen(false)}
      onHome={() => setPage("home")}
      onNew={startNew}
      onCatalog={() => setPage("catalog")}
      onSelect={openConversation}
      onSettings={() => openDialog("settings")}
    />
    <div className={`app-content ${collapsed ? "sidebar-is-collapsed" : ""}`}>
      <header className="global-topbar">
        <div className="topbar-leading">
          <button type="button" className="menu-toggle" aria-label="打开侧栏" title="打开侧栏" onClick={() => {
            if (window.matchMedia?.("(max-width: 768px)").matches) setMobileOpen(true);
            else setCollapsed((value) => !value);
          }}><Menu size={19} /></button>
          <button type="button" className="global-brand" onClick={() => setPage("home")} aria-label="管策智研首页"><span className="brand-mark"><BookOpenText size={19} strokeWidth={1.8} /></span><span>管策智研</span></button>
        </div>
        <nav className="global-nav" aria-label="主导航">
          <button type="button" className={page !== "catalog" ? "is-current" : ""} onClick={goResearch}>研究</button>
          <button type="button" className={page === "catalog" ? "is-current" : ""} onClick={() => setPage("catalog")}>文献</button>
          <button type="button" onClick={() => openDialog("data")}>数据</button>
          <button type="button" onClick={() => openDialog("about")}>关于</button>
        </nav>
        <div className="topbar-trailing">
          <span className={`global-env-badge ${isRealMode ? "is-real" : "is-demo"}`}>{isRealMode ? "Real" : "Demo 数据"}</span>
          <button type="button" className="identity-button" onClick={() => openDialog("settings")} aria-label="学号与环境设置"><span>学号</span><Settings2 size={15} /></button>
        </div>
      </header>

      {page === "home" && <div className="page-body page-body-home"><Home mode={mode} value={homeDraft} busy={busy} onChange={setHomeDraft} onModeChange={setMode} onSend={beginMessage} onAdvancedSubmit={beginAdvanced} /></div>}
      {page === "workspace" && active && <div className="page-body page-body-workspace"><ConversationView conversation={active} onModeChange={setMode} onSend={beginMessage} onAdvancedSubmit={beginAdvanced} onRetry={() => void retry()} onRestartPreparation={() => void restartPreparation()} /></div>}
      {page === "catalog" && <div className="page-body page-body-catalog"><PolicyCatalog /></div>}
    </div>

    {dialog && <dialog ref={dialogRef} className="global-dialog" aria-labelledby="global-dialog-title" onCancel={(event) => { event.preventDefault(); closeDialog(); }} onClick={(event) => { if (event.target === event.currentTarget) closeDialog(); }}>
        <header className="global-dialog-header"><div><span className="dialog-eyebrow">管策智研</span><h2 id="global-dialog-title">{dialog === "settings" ? "身份与环境设置" : dialog === "about" ? "关于管策智研" : "数据入口"}</h2></div><button type="button" aria-label="关闭" onClick={closeDialog}><X size={18} /></button></header>
        {dialog === "settings" && <div className="global-dialog-content"><div className="identity-status"><span>学号</span><strong>未连接身份认证</strong></div><div className="setting-block"><strong>当前运行环境</strong><p>{isRealMode ? "Real · 使用已配置的真实后端" : "Demo · 本地演示数据"}</p></div>{!isRealMode && <div className="setting-block"><strong>本地演示情景</strong><p>仅影响 Demo 演示，不会在真实环境失败时切换数据。</p><div className="scenario-options">{scenarioOptions.map((option) => <label key={option.value} className={`scenario-option ${scenario === option.value ? "is-selected" : ""}`}><input type="radio" name="demo-scenario" value={option.value} checked={scenario === option.value} onChange={() => setScenario(option.value)} /><span className="radio-indicator" />{option.label}</label>)}</div></div>}<p className="setting-disclaimer">当前没有登录或学号认证功能。</p></div>}
        {dialog === "data" && <div className="global-dialog-content"><p>数据入口当前展示本次前端会话中的研究历史，不提供独立的数据分析服务，也不会云端保存。</p><button type="button" className="dialog-action" onClick={() => { closeDialog(); setCollapsed(false); if (window.matchMedia?.("(max-width: 768px)").matches) setMobileOpen(true); }}>{sessions.length ? `查看 ${sessions.length} 条本地研究记录` : "研究记录将在这里显示"}</button></div>}
        {dialog === "about" && <div className="global-dialog-content"><p>管策智研用于围绕政策文献开展问答、要素提取、文献梳理与比较分析。</p><p>研究范围与证据状态以本次后端返回内容为准。</p></div>}
    </dialog>}
  </div>;
}
