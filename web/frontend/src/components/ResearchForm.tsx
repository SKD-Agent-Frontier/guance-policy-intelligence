import { useEffect, useRef, useState } from "react";
import {
  ArrowUp,
  FileText,
  LayoutTemplate,
  X,
} from "lucide-react";
import type { ResearchRequest, TaskType } from "../types";
import type { AdvancedDraft } from "../conversationTypes";
import { promptTemplates } from "../promptTemplates";

type Props = {
  mode: TaskType;
  value: string;
  busy: boolean;
  onChange: (value: string) => void;
  onModeChange: (mode: TaskType) => void;
  onSend: (message: string) => void;
  onAdvancedSubmit?: (request: ResearchRequest) => void;
  advancedDraft: AdvancedDraft;
  onAdvancedDraftChange: (patch: Partial<AdvancedDraft>) => void;
  onOpenCatalog?: () => void;
  hideModeTabs?: boolean;
  enableTemplates?: boolean;
};

const modes: { id: TaskType; label: string }[] = [
  { id: "qa", label: "政策问答" },
  { id: "extract", label: "要素提取" },
  { id: "organize", label: "文献梳理" },
  { id: "compare", label: "政策比较" },
  { id: "compare_plans", label: "多方案分析" },
];

export function ResearchForm({
  mode,
  value,
  busy,
  onChange,
  onModeChange,
  onSend,
  hideModeTabs = false,
  enableTemplates = false,
}: Props) {
  const [templatesOpen, setTemplatesOpen] = useState(false);
  const composerRef = useRef<HTMLElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const resize = () => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    textarea.style.height = "auto";
    const height = Math.min(Math.max(textarea.scrollHeight, 36), 200);
    textarea.style.height = `${height}px`;
    textarea.style.overflowY = textarea.scrollHeight > 200 ? "auto" : "hidden";
  };

  useEffect(resize, [value]);
  useEffect(() => { setTemplatesOpen(false); }, [mode, enableTemplates]);
  useEffect(() => {
    if (!templatesOpen) return;
    const onPointerDown = (event: PointerEvent) => {
      if (event.target instanceof Node && !composerRef.current?.contains(event.target)) {
        setTemplatesOpen(false);
      }
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setTemplatesOpen(false);
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [templatesOpen]);
  const send = () => {
    const message = value.trim();
    if (!busy && message && Array.from(message).length <= 4000) onSend(message);
  };

  const messageLength = Array.from(value).length;
  return (
    <section className="research-composer" ref={composerRef} aria-label="研究需求">
      {!hideModeTabs && <div className="mode-tabs" role="tablist" aria-label="研究模式">
        {modes.map(({ id, label }) => (
          <button
            key={id}
            className={`mode-tab ${mode === id ? "is-active" : ""}`}
            type="button"
            role="tab"
            aria-selected={mode === id}
            onClick={() => {
              if (id !== mode && enableTemplates && promptTemplates[mode].some((template) => template.prompt === value)) onChange("");
              setTemplatesOpen(false);
              onModeChange(id);
            }}
          >
            <span>{label}</span>
          </button>
        ))}
      </div>}
      <div className={`composer-fields${enableTemplates ? " composer-fields--with-templates" : ""}`}>
        {enableTemplates && (
          <button
            type="button"
            className={`prompt-template-trigger${templatesOpen ? " is-open" : ""}`}
            aria-label="指令模板"
            aria-haspopup="true"
            aria-expanded={templatesOpen}
            aria-controls="prompt-template-panel"
            onClick={() => setTemplatesOpen((open) => !open)}
          >
            <LayoutTemplate size={19} strokeWidth={1.8} aria-hidden="true" />
          </button>
        )}
        <textarea
          ref={textareaRef}
          className="composer-question"
          aria-label="研究需求"
          placeholder="今天想研究些什么呢？"
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
          <ArrowUp size={17} strokeWidth={2.4} />
        </button>
      </div>
      {messageLength > 0 && <div className={`composer-count ${messageLength > 4000 ? "is-over-limit" : ""}`} aria-live="polite">
        {messageLength > 4000 ? `输入过长（${messageLength}/4000 字符），请精简后发送。` : `${messageLength}/4000`}
      </div>}
      {enableTemplates && templatesOpen && (
        <div className="prompt-template-panel" id="prompt-template-panel" role="region" aria-label="指令模板列表">
          <header className="prompt-template-heading">
            <div className="prompt-template-heading-title"><FileText size={17} aria-hidden="true" /><strong>{modes.find((item) => item.id === mode)?.label} · 指令模板</strong></div>
            <button type="button" className="prompt-template-close" aria-label="关闭指令模板" onClick={() => setTemplatesOpen(false)}><X size={17} aria-hidden="true" /></button>
          </header>
          <div className="prompt-template-grid">
            {promptTemplates[mode].map((template) => (
              <button
                type="button"
                key={template.id}
                className={`prompt-template-card${value === template.prompt ? " is-selected" : ""}`}
                aria-pressed={value === template.prompt}
                onClick={() => { onChange(template.prompt); textareaRef.current?.focus(); }}
              >
                <span className="prompt-template-card-icon"><FileText size={17} aria-hidden="true" /></span>
                <span className="prompt-template-card-content">
                  <strong>{template.title}</strong>
                  <span>{template.prompt}</span>
                </span>
                {value === template.prompt && <span className="prompt-template-selected" aria-hidden="true">✓</span>}
              </button>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}
