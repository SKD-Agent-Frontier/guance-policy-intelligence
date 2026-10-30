import { BookOpenText, ChevronLeft, FilePlus2, Home, Library, Settings2 } from "lucide-react";
import type { Conversation } from "../conversationTypes";
import type { TaskType } from "../types";

type Props = {
  conversations: Conversation[];
  activeId: string | null;
  collapsed: boolean;
  mobileOpen: boolean;
  onToggle: () => void;
  onCloseMobile: () => void;
  onHome: () => void;
  onNew: () => void;
  onCatalog: () => void;
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

export function Sidebar({ conversations, activeId, collapsed, mobileOpen, onToggle, onCloseMobile, onHome, onNew, onCatalog, onSelect, onSettings }: Props) {
  const isCollapsed = collapsed && !mobileOpen;
  const closeAnd = (action: () => void) => {
    action();
    onCloseMobile();
  };
  return <>
    {mobileOpen && <button className="sidebar-scrim" aria-label="关闭侧栏" type="button" onClick={onCloseMobile} />}
    <aside className={`app-sidebar ${isCollapsed ? "is-collapsed" : ""} ${mobileOpen ? "is-mobile-open" : ""}`} aria-label="全局侧栏" aria-hidden={!mobileOpen && typeof window !== "undefined" && window.matchMedia?.("(max-width: 768px)").matches}>
      <div className="sidebar-brand-row">
        <BookOpenText size={18} aria-hidden="true" />
        {!isCollapsed && <><strong>研究工作台</strong><button type="button" className="sidebar-collapse" title="收起侧栏" aria-label="收起侧栏" onClick={onToggle}><ChevronLeft size={16} /></button></>}
      </div>
      <nav className="sidebar-primary" aria-label="工作台导航">
        <button type="button" className="side-link sidebar-home-link" title="返回首页" aria-label="返回首页" onClick={() => closeAnd(onHome)}><Home size={16} /></button>
        <button type="button" className="side-link new-link" title="新建研究" onClick={() => closeAnd(onNew)}><FilePlus2 size={16} />{!isCollapsed && <span>新建研究</span>}</button>
        <button type="button" className="side-link" title="文献目录" onClick={() => closeAnd(onCatalog)}><Library size={16} />{!isCollapsed && <span>文献目录</span>}</button>
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
          <button type="button" className="side-link" title="演示设置" onClick={onSettings}><Settings2 size={15} /><span>环境设置</span></button>
        </div>
      </>}
      {isCollapsed && <button type="button" className="sidebar-expand" aria-label="展开侧栏" title="展开侧栏" onClick={onToggle}>›</button>}
    </aside>
  </>;
}
