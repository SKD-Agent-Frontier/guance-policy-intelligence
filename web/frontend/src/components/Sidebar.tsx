import { FilePlus2, History, Home, MessageSquare, Search, Settings2, UserRound, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { Conversation } from "../conversationTypes";

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
  onRename: (id: string, title: string) => void;
  onDelete: (id: string) => void;
  onSettings: () => void;
};

export function Sidebar({ conversations, activeId, collapsed, mobileOpen, onExpand, onToggle, onCloseMobile, onHome, onNew, onSelect, onRename, onDelete, onSettings }: Props) {
  const [isMobileViewport, setIsMobileViewport] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [contextMenu, setContextMenu] = useState<{ id: string; title: string; x: number; y: number } | null>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const menuTriggerRef = useRef<HTMLButtonElement | null>(null);
  const searchDialogRef = useRef<HTMLDialogElement>(null);
  const searchButtonRef = useRef<HTMLButtonElement>(null);
  const historyHeadingRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (searchOpen && searchDialogRef.current && !searchDialogRef.current.open) {
      searchDialogRef.current.showModal();
      searchDialogRef.current.querySelector<HTMLInputElement>("input")?.focus();
    }
  }, [searchOpen]);
  useEffect(() => {
    if (!contextMenu) return;
    menuRef.current?.querySelector<HTMLButtonElement>("button")?.focus();
    const dismiss = (event: PointerEvent) => { if (!menuRef.current?.contains(event.target as Node)) setContextMenu(null); };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); setContextMenu(null); menuTriggerRef.current?.focus(); }
    };
    const onViewportChange = () => setContextMenu(null);
    document.addEventListener("pointerdown", dismiss);
    document.addEventListener("keydown", onKeyDown);
    window.addEventListener("resize", onViewportChange);
    window.addEventListener("scroll", onViewportChange, true);
    return () => { document.removeEventListener("pointerdown", dismiss); document.removeEventListener("keydown", onKeyDown); window.removeEventListener("resize", onViewportChange); window.removeEventListener("scroll", onViewportChange, true); };
  }, [contextMenu]);
  useEffect(() => {
    const media = window.matchMedia("(max-width: 768px)");
    const updateViewport = () => setIsMobileViewport(media.matches);
    updateViewport();
    media.addEventListener("change", updateViewport);
    return () => media.removeEventListener("change", updateViewport);
  }, []);
  // Empty drafts (including the initial placeholder) are not conversation history.
  const history = conversations.filter((conversation) => conversation.messages.length > 0);
  const searchTerm = query.trim().toLocaleLowerCase();
  const searchResults = history.filter((conversation) => !searchTerm || conversation.title.toLocaleLowerCase().includes(searchTerm) || conversation.messages.some((message) => message.kind === "user" && message.text.toLocaleLowerCase().includes(searchTerm)));
  const isCollapsed = (collapsed || isMobileViewport) && !mobileOpen;
  const closeAnd = (action: () => void) => {
    const shouldCloseMobile = mobileOpen;
    if (isCollapsed) onExpand();
    action();
    if (shouldCloseMobile) onCloseMobile();
  };
  const showHistory = () => {
    if (isCollapsed) onExpand();
    else historyHeadingRef.current?.focus();
  };
  const closeSearch = () => {
    if (searchDialogRef.current?.open) searchDialogRef.current.close();
    setSearchOpen(false);
    setQuery("");
    searchButtonRef.current?.focus();
  };
  return <>
    {mobileOpen && <button className="sidebar-scrim" aria-label="关闭侧栏" type="button" onClick={onCloseMobile} />}
    <aside className={`app-sidebar ${isCollapsed ? "is-collapsed" : ""} ${mobileOpen ? "is-mobile-open" : ""}`} aria-label="全局侧栏">
      <div className="sidebar-brand-row" title="管策智研 · 政策智能研究助手">
        <button type="button" className="sidebar-brand-toggle" title={isCollapsed ? "展开侧栏" : "收起侧栏"} aria-label={isCollapsed ? "展开侧栏" : "收起侧栏"} onClick={isCollapsed ? onExpand : onToggle}><img className="sidebar-brand-mark" src="/guance-mark.svg" alt="" /></button>
        {!isCollapsed && <><strong>管策智研</strong><button ref={searchButtonRef} type="button" className="history-search-toggle sidebar-search-button" aria-label="搜索研究记录" title="搜索研究记录" aria-haspopup="dialog" onClick={() => setSearchOpen(true)}><Search size={19} /></button></>}
      </div>
      {!isCollapsed && <nav className="sidebar-primary" aria-label="工作台导航">
        <button type="button" className="side-link sidebar-home-link" title="返回首页" aria-label="返回首页" onClick={() => closeAnd(onHome)}><Home size={24} /></button>
        <button type="button" className="side-link new-link" title="新建研究" aria-label="新建研究" onClick={() => closeAnd(onNew)}><FilePlus2 size={20} />{!isCollapsed && <span>新建研究</span>}</button>
        <button type="button" className="side-link sidebar-history-shortcut" title="历史记录" aria-label="历史记录" onClick={showHistory}><History size={24} /></button>
      </nav>}
      {!isCollapsed && <>
        <div className="sidebar-section-title" ref={historyHeadingRef} tabIndex={-1}><span>研究记录</span></div>
        <div className="sidebar-history">
          {history.length === 0 && <p className="sidebar-empty">研究记录将在这里显示</p>}
          {history.map((conversation) => <div className="history-row" key={conversation.id}><button type="button" className={`history-link ${conversation.id === activeId ? "is-current" : ""}`} aria-current={conversation.id === activeId ? "true" : undefined} title={conversation.title} onClick={() => closeAnd(() => onSelect(conversation.id))} onContextMenu={(event) => { event.preventDefault(); menuTriggerRef.current = event.currentTarget; setContextMenu({ id: conversation.id, title: conversation.title, x: Math.max(0, Math.min(event.clientX, window.innerWidth - 130)), y: Math.max(0, Math.min(event.clientY, window.innerHeight - 90)) }); }}>
            <span className="history-question">{conversation.title || "新建研究"}</span>
            {conversation.busy !== "idle" && <span className="history-busy-spinner" role="status" aria-label="研究中" />}
          </button></div>)}
        </div>
        <div className="sidebar-bottom">
          <button type="button" className="side-link" title="环境设置" aria-label="环境设置" onClick={onSettings}><Settings2 size={24} /><span>环境设置</span></button>
          <button type="button" className="sidebar-login" onClick={onSettings}><UserRound size={18} /><span>登录 / 注册（暂未开放）</span></button>
        </div>
      </>}
    </aside>
    {searchOpen && <dialog ref={searchDialogRef} className="history-search-dialog" aria-labelledby="history-search-title" onCancel={(event) => { event.preventDefault(); closeSearch(); }} onClick={(event) => { if (event.target === event.currentTarget) closeSearch(); }}>
      <header><h2 id="history-search-title">搜索对话</h2><button type="button" aria-label="关闭搜索" onClick={closeSearch}><X size={19} /></button></header>
      <label className="history-search-field"><Search size={19} aria-hidden="true" /><input aria-label="搜索研究记录" placeholder="搜索对话标题或内容" value={query} onChange={(event) => setQuery(event.target.value)} /></label>
      <section className="history-search-results" aria-label="搜索结果"><h3>{searchTerm ? "搜索结果" : "最近对话"}</h3>
        {searchResults.length ? searchResults.map((conversation) => <button key={conversation.id} type="button" title={conversation.title} onClick={() => { closeSearch(); closeAnd(() => onSelect(conversation.id)); }}><MessageSquare size={17} aria-hidden="true" /><span>{conversation.title}</span></button>) : <p>{searchTerm ? "没有找到匹配的对话" : "暂无对话记录"}</p>}
      </section>
    </dialog>}
    {contextMenu && createPortal(<div ref={menuRef} className="history-context-menu" role="group" aria-label="研究记录操作" style={{ left: contextMenu.x, top: contextMenu.y }}><button type="button" onClick={() => { const title = window.prompt("重命名研究", contextMenu.title); if (title?.trim()) onRename(contextMenu.id, title); setContextMenu(null); menuTriggerRef.current?.focus(); }}>重命名</button><button type="button" onClick={() => { if (window.confirm("删除这条研究记录？")) onDelete(contextMenu.id); setContextMenu(null); }}>删除</button></div>, document.body)}
  </>;
}
