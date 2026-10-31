import { ChevronLeft, FilePlus2, Home, Settings2 } from "lucide-react";
import { useEffect, useState } from "react";
import type { Conversation } from "../conversationTypes";
import type { TaskType } from "../types";

type Props = {
  conversations: Conversation[];
  activeId: string | null;
  collapsed: boolean;
  mobileOpen: boolean;
  onExpand: () => void;
  onToggle: () => void;
  onCloseMobile: () => void;
  onHome: () => void;
  onNew: () => void;
  onSelect: (id: string) => void;
  onSettings: () => void;
};

const names: Record<TaskType, string> = {
  qa: "政策问答",
  extract: "要素提取",
  organize: "文献梳理",
  compare: "政策比较",
  compare_plans: "多方案分析",
};

export function Sidebar({ conversations, activeId, collapsed, mobileOpen, onExpand, onToggle, onCloseMobile, onHome, onNew, onSelect, onSettings }: Props) {
  const [isMobileViewport, setIsMobileViewport] = useState(false);
  useEffect(() => {
    const media = window.matchMedia("(max-width: 768px)");
    const updateViewport = () => setIsMobileViewport(media.matches);
    updateViewport();
    media.addEventListener("change", updateViewport);
    return () => media.removeEventListener("change", updateViewport);
  }, []);
  const isCollapsed = (collapsed || isMobileViewport) && !mobileOpen;
  const closeAnd = (action: () => void) => {
    const shouldCloseMobile = mobileOpen;
    if (isCollapsed) onExpand();
    action();
    if (shouldCloseMobile) onCloseMobile();
  };
  return <>
    {mobileOpen && <button className="sidebar-scrim" aria-label="关闭侧栏" type="button" onClick={onCloseMobile} />}
    <aside className={`app-sidebar ${isCollapsed ? "is-collapsed" : ""} ${mobileOpen ? "is-mobile-open" : ""}`} aria-label="全局侧栏">
      <div className="sidebar-brand-row" title="管策智研 · 政策智能研究助手">
        {isCollapsed ? <button type="button" className="sidebar-brand-toggle" title="展开侧栏" aria-label="展开侧栏" onClick={onExpand}><img className="sidebar-brand-mark" src="/guance-mark.svg" alt="" /></button> : <img className="sidebar-brand-mark" src="/guance-mark.svg" alt="" />}
        {!isCollapsed && <><strong>管策智研</strong><button type="button" className="sidebar-collapse" title="收起侧栏" aria-label="收起侧栏" onClick={onToggle}><ChevronLeft size={16} /></button></>}
      </div>
      <nav className="sidebar-primary" aria-label="工作台导航">
        <button type="button" className="side-link sidebar-home-link" title="返回首页" aria-label="返回首页" onClick={() => closeAnd(onHome)}><Home size={20} /></button>
        <button type="button" className="side-link new-link" title="新建研究" aria-label="新建研究" onClick={() => closeAnd(onNew)}><FilePlus2 size={20} /></button>
      </nav>
      {!isCollapsed && <>
        <div className="sidebar-section-title"><span>最近研究</span><span className="sidebar-session-count">{conversations.length}</span></div>
        <div className="sidebar-history">
          {conversations.length === 0 ? <div className="sidebar-empty">本次会话中的研究会显示在这里</div> : conversations.map((conversation) => <button key={conversation.id} type="button" className={`history-link ${conversation.id === activeId ? "is-current" : ""}`} title={conversation.title} onClick={() => closeAnd(() => onSelect(conversation.id))}>
            <span className="history-type">{names[conversation.mode]}{conversation.busy !== "idle" ? " · 处理中" : ""}</span>
            <span className="history-question">{conversation.title || "新建研究"}</span>
          </button>)}
        </div>
        <div className="sidebar-bottom">
          <p className="local-demo-note">研究记录仅保存在当前前端会话</p>
          <button type="button" className="side-link" title="环境设置" aria-label="环境设置" onClick={onSettings}><Settings2 size={20} /><span>环境设置</span></button>
        </div>
      </>}
    </aside>
  </>;
}
