import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { ArrowLeft, ArrowUp, BookMarked, ChevronRight, FileText, LayoutTemplate, Plus, X } from "lucide-react";
import type { CitationReference, ResearchRequest, TaskType } from "../types";
import { CitationPicker } from "./CitationPicker";
import type { AdvancedDraft } from "../conversationTypes";
import { promptTemplates } from "../promptTemplates";

type Props = {
  mode: TaskType;
  value: string;
  busy: boolean;
  onChange: (value: string) => void;
  onModeChange: (mode: TaskType) => void;
  onSend: (message: string, citations?: CitationReference[]) => void;
  /** Legacy props remain accepted while callers migrate; no advanced fields are rendered. */
  onAdvancedSubmit?: (request: ResearchRequest) => void;
  advancedDraft?: AdvancedDraft;
  onAdvancedDraftChange?: (patch: Partial<AdvancedDraft>) => void;
  onOpenCatalog?: () => void;
  /** The toolbar remains available in home and conversation composers. */
  hideModeTabs?: boolean;
  enableTemplates?: boolean;
  homeVariant?: boolean;
};

const modes: { id: TaskType; label: string }[] = [
  { id: "qa", label: "政策问答" },
  { id: "organize", label: "文献梳理" },
  { id: "extract", label: "要素提取" },
  { id: "compare", label: "政策比较" },
  { id: "compare_plans", label: "多方案分析" },
];

type MenuPanel = "root" | "citations" | "templates" | null;

export function ResearchForm({
  mode,
  value,
  busy,
  onChange,
  onModeChange,
  onSend,
  onAdvancedSubmit,
  homeVariant = false,
}: Props) {
  const [panel, setPanel] = useState<MenuPanel>(null);
  const [templateMode, setTemplateMode] = useState<TaskType>(mode);
  const [selectedCitations, setSelectedCitations] = useState<CitationReference[]>([]);
  const composerRef = useRef<HTMLElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const isOpen = panel !== null;

  const closeMenu = () => setPanel(null);
  const resize = () => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    textarea.style.height = "auto";
    const height = Math.min(Math.max(textarea.scrollHeight, 36), 200);
    textarea.style.height = `${height}px`;
    textarea.style.overflowY = textarea.scrollHeight > 200 ? "auto" : "hidden";
  };

  useEffect(resize, [value]);
  useEffect(() => {
    // External mode tabs may replace an untouched template with mode-specific input.
    if (value && Object.values(promptTemplates).some(list => list.some(item => item.prompt === value))) onChange("");
    closeMenu();
    setTemplateMode(mode);
  }, [mode]);
  useEffect(() => {
    if (!isOpen) return;
    const onPointerDown = (event: PointerEvent) => {
      if (event.target instanceof Node && !composerRef.current?.contains(event.target)) closeMenu();
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") closeMenu();
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [isOpen]);

  // Popovers float over the background instead of pushing the page down.
  useLayoutEffect(() => {
    if (!isOpen || !menuRef.current) return;
    const element = menuRef.current;
    const fitViewport = () => {
      const rect = element.getBoundingClientRect();
      const inConversation = !!composerRef.current?.closest(".conversation-composer-wrap");
      const available = inConversation ? rect.bottom - 16 : window.innerHeight - rect.top - 16;
      element.style.maxHeight = `${Math.max(100, Math.floor(available))}px`;
    };
    fitViewport();
    window.addEventListener("resize", fitViewport);
    return () => window.removeEventListener("resize", fitViewport);
  }, [isOpen, panel]);

  const send = () => {
    const message = value.trim();
    if (!busy && message && Array.from(message).length <= 4000) {
      const citations = selectedCitations.length > 0 ? selectedCitations : undefined;
      if (citations && (mode === "compare" || mode === "compare_plans") && onAdvancedSubmit) {
        closeMenu();
        onAdvancedSubmit({
          task_type: mode,
          question: message,
          targets: citations.map(item => item.planid),
        });
      } else {
        const citedMessage = citations
          ? `${message}\n\n已引用文献：\n${citations.map(item => `- planid=${item.planid} 《${item.title ?? "未命名政策文献"}》`).join("\n")}`
          : message;
        closeMenu();
        onSend(citedMessage, citations);
      }
      setSelectedCitations([]);
    }
  };

  const resetMode = () => {
    // Unedited examples are transient helper text, never part of a mode constraint.
    if (mode !== "qa" && promptTemplates[templateMode].some((item) => item.prompt === value)) onChange("");
    onModeChange("qa");
    textareaRef.current?.focus();
  };

  const messageLength = Array.from(value).length;
  const currentLabel = modes.find((item) => item.id === mode)?.label ?? "政策问答";

  return (
    <section className="research-composer" ref={composerRef} aria-label="研究需求">
      {selectedCitations.length > 0 && (
        <div className="citation-selected" role="group" aria-label="已引用文献">
          {selectedCitations.map(citation => (
            <span key={citation.planid} className="citation-chip">
              <BookMarked size={13} aria-hidden="true" />
              <span>{citation.title ?? "未命名政策文献"}</span>
              <button type="button" aria-label={`移除引用 ${citation.title ?? "未命名政策文献"}`} disabled={busy} onClick={() => setSelectedCitations(current => current.filter(item => item.planid !== citation.planid))}>
                <X size={11} aria-hidden="true" />
              </button>
            </span>
          ))}
        </div>
      )}
      <div className="composer-fields composer-fields--with-templates">
        <button
          type="button"
          className={`prompt-template-trigger${isOpen ? " is-open" : ""}`}
          aria-label="更多功能"
          title="更多功能"
          aria-haspopup="true"
          aria-expanded={isOpen}
          aria-controls="research-extra-panel"
          disabled={busy}
          onClick={() => setPanel((current) => current ? null : "root")}
        >
          <Plus size={21} strokeWidth={1.9} aria-hidden="true" />
        </button>
        <textarea
          ref={textareaRef}
          className="composer-question"
          aria-label="研究需求"
          placeholder={homeVariant ? "输入你想研究的政策问题…" : "今天想研究些什么呢？"}
          rows={1}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={(event) => {
            if (event.key !== "Enter" || event.shiftKey) return;
            if (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229) return;
            event.preventDefault();
            send();
          }}
          disabled={busy}
        />
        <button type="button" className="submit-button" onClick={send} disabled={busy || !value.trim() || messageLength > 4000} aria-label="发送消息" title="发送">
          {homeVariant ? <><span>开始研究</span><ArrowUp size={17} strokeWidth={2.4} /></> : <ArrowUp size={17} strokeWidth={2.4} />}
        </button>
      </div>
      {mode !== "qa" && !homeVariant && (
        <div className="composer-current-mode">
          <span>{currentLabel}</span>
          <button type="button" aria-label="恢复默认模式" title="恢复政策问答" disabled={busy} onClick={resetMode}><X size={12} /></button>
        </div>
      )}
      {messageLength > 4000 && <div className="composer-count is-over-limit" aria-live="polite">
        输入过长，请精简后发送。
      </div>}
      {panel && (
        <div
          className={`research-extra-panel research-extra-panel--${panel}`}
          id="research-extra-panel"
          ref={menuRef}
          role="region"
          aria-label={panel === "root" ? "更多功能菜单" : panel === "citations" ? "引用文献选择" : "指令模板列表"}
        >
          {panel === "root" ? (
            <div className="extra-menu-items">
              <button type="button" className="extra-menu-item" onClick={() => setPanel("citations")}>
                <BookMarked size={18} aria-hidden="true" /><span>引用文献</span>
                {selectedCitations.length > 0 && <span className="extra-menu-current">{selectedCitations.length} 篇</span>}
                <ChevronRight className="extra-menu-chevron" size={16} aria-hidden="true" />
              </button>
              <button type="button" className="extra-menu-item" onClick={() => { setTemplateMode(mode); setPanel("templates"); }}>
                <LayoutTemplate size={18} aria-hidden="true" /><span>指令模板</span><ChevronRight className="extra-menu-chevron" size={16} aria-hidden="true" />
              </button>
            </div>
          ) : panel === "citations" ? (
            <>
              <header className="extra-panel-header">
                <button type="button" aria-label="返回更多功能" onClick={() => setPanel("root")}><ArrowLeft size={17} /></button>
                <strong>引用文献</strong>
                <button type="button" aria-label="关闭更多功能" onClick={closeMenu}><X size={17} /></button>
              </header>
              <CitationPicker
                selected={selectedCitations}
                onToggle={(citation) => setSelectedCitations(current => (
                  current.some(item => item.planid === citation.planid)
                    ? current.filter(item => item.planid !== citation.planid)
                    : current.length >= 8 ? current : [...current, citation]
                ))}
              />
            </>
          ) : (
            <>
              <header className="extra-panel-header">
                <button type="button" aria-label="返回更多功能" onClick={() => setPanel("root")}><ArrowLeft size={17} /></button>
                <strong>指令模板</strong>
                <button type="button" aria-label="关闭更多功能" onClick={closeMenu}><X size={17} /></button>
              </header>
              <div className="extra-template-modes" role="group" aria-label="模板分类">
                {modes.map(({ id, label }) => (
                  <button type="button" key={id} aria-pressed={templateMode === id} className={templateMode === id ? "is-active" : ""} onClick={() => setTemplateMode(id)}>{label}</button>
                ))}
              </div>
              <div className="prompt-template-grid">
                {promptTemplates[templateMode].map((template) => (
                  <button
                    type="button"
                    key={template.id}
                    className={`prompt-template-card${value === template.prompt ? " is-selected" : ""}`}
                    aria-pressed={value === template.prompt}
                    onClick={() => { onChange(template.prompt); closeMenu(); textareaRef.current?.focus(); }}
                  >
                    <span className="prompt-template-card-icon"><FileText size={17} aria-hidden="true" /></span>
                    <span className="prompt-template-card-content"><strong>{template.title}</strong><span>{template.prompt}</span></span>
                  </button>
                ))}
              </div>
            </>
          )}
        </div>
      )}
    </section>
  );
}
