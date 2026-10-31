import { useEffect, useRef } from "react";
import {
  ArrowUp,
  Plus,
  X,
} from "lucide-react";
import type { ResearchRequest, TaskType } from "../types";
import type { AdvancedDraft } from "../conversationTypes";

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
};

const modes: { id: TaskType; label: string }[] = [
  { id: "qa", label: "政策问答" },
  { id: "extract", label: "要素提取" },
  { id: "organize", label: "文献梳理" },
  { id: "compare", label: "政策比较" },
  { id: "compare_plans", label: "多方案分析" },
];
const dimensions = ["支持对象", "申报条件", "支持措施", "资金标准", "办理流程", "实施期限"];
const isPlanId = (value: string) => /^[A-Za-z0-9_-]{1,64}$/.test(value);

export function ResearchForm({
  mode,
  value,
  busy,
  onChange,
  onModeChange,
  onSend,
  onAdvancedSubmit,
  advancedDraft,
  onAdvancedDraftChange,
  onOpenCatalog,
  hideModeTabs = false,
}: Props) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const targetRefs = useRef<Array<HTMLInputElement | null>>([]);
  const targetValuesRef = useRef<string[]>([]);
  targetValuesRef.current = mode === "extract" ? [advancedDraft.target] : advancedDraft.targets;

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
    if (!advancedDraft.open || advancedDraft.focusVersion === 0) return;
    const index = targetValuesRef.current.findIndex((item) => !isPlanId(item.trim()));
    if (index >= 0) targetRefs.current[index]?.focus();
  }, [advancedDraft.focusVersion, advancedDraft.open, mode]);

  const submitAdvanced = () => {
    if (busy || !onAdvancedSubmit) return;
    if (mode === "extract") {
      const planid = advancedDraft.target.trim();
      if (!isPlanId(planid)) return;
      onAdvancedSubmit({
        task_type: mode,
        question: advancedDraft.question.trim() || `请提取“${planid}”的相关政策要素。`,
        targets: [planid],
        dimensions: advancedDraft.dimensions,
      });
      return;
    }
    if (mode !== "compare" && mode !== "compare_plans") return;
    const cleanTargets = advancedDraft.targets.map((item) => item.trim());
    if (cleanTargets.length < 2 || cleanTargets.length > 8 || cleanTargets.some((item) => !isPlanId(item))) return;
    if (new Set(cleanTargets).size !== cleanTargets.length) return;
    onAdvancedSubmit({
      task_type: mode,
      question: advancedDraft.question.trim() || `${mode === "compare" ? "比较" : "分析"}以下政策对象：${cleanTargets.join("；")}`,
      targets: cleanTargets,
      dimensions: advancedDraft.dimensions,
    });
  };

  const send = () => {
    const message = value.trim();
    if (!busy && message && Array.from(message).length <= 4000) onSend(message);
  };

  const advancedAvailable = mode === "extract" || mode === "compare" || mode === "compare_plans";
  const messageLength = Array.from(value).length;
  const advancedTargets = advancedDraft.targets.map((item) => item.trim());
  const advancedTargetValid = mode === "extract"
    ? isPlanId(advancedDraft.target.trim())
    : advancedTargets.length >= 2 && advancedTargets.length <= 8 && advancedTargets.every(isPlanId) && new Set(advancedTargets).size === advancedTargets.length;

  return (
    <section className="research-composer" aria-label="研究需求">
      {!hideModeTabs && <div className="mode-tabs" role="tablist" aria-label="研究模式">
        {modes.map(({ id, label }) => (
          <button
            key={id}
            className={`mode-tab ${mode === id ? "is-active" : ""}`}
            type="button"
            role="tab"
            aria-selected={mode === id}
            onClick={() => onModeChange(id)}
          >
            <span>{label}</span>
          </button>
        ))}
      </div>}
      <div className="composer-fields">
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
      {advancedAvailable && (
        <div className="advanced-entry">
          <button
            type="button"
            className="advanced-toggle"
            aria-expanded={advancedDraft.open}
            onClick={() => onAdvancedDraftChange({ open: !advancedDraft.open, ...(!advancedDraft.open ? { focusVersion: advancedDraft.focusVersion + 1 } : {}) })}
            disabled={busy}
          >
            {advancedDraft.open ? "收起高级选择" : "高级选择 · 指定政策编号"}
          </button>
          {advancedDraft.open && (
            <div className="advanced-fields">
              {mode === "extract" ? (
                <div className="advanced-field">
                  <label htmlFor="extract-planid">政策编号（planid）</label>
                  <input ref={(node) => { targetRefs.current[0] = node; }} id="extract-planid" value={advancedDraft.target} onChange={(event) => onAdvancedDraftChange({ target: event.target.value })} aria-label="政策编号" />
                  {!advancedTargetValid && <p className="advanced-validation" role="alert">请填写有效政策编号（字母、数字、下划线或短横线，长度为 1—64 个字符）。</p>}
                </div>
              ) : (
                <div className="advanced-targets">
                  <span>研究对象编号（2—8 个 planid）</span>
                  {advancedDraft.targets.map((item, index) => (
                    <div className="advanced-target-row" key={index}>
                      <span>政策对象 {String.fromCharCode(65 + index)}</span>
                      <input
                        ref={(node) => { targetRefs.current[index] = node; }}
                        aria-label={`研究对象编号 ${index + 1}`}
                        value={item}
                        onChange={(event) => onAdvancedDraftChange({ targets: advancedDraft.targets.map((targetValue, position) => position === index ? event.target.value : targetValue) })}
                      />
                      {advancedDraft.targets.length > 2 && <button type="button" className="advanced-remove-target" aria-label={`移除研究对象 ${index + 1}`} onClick={() => onAdvancedDraftChange({ targets: advancedDraft.targets.filter((_, position) => position !== index) })}><X size={14} /></button>}
                    </div>
                  ))}
                  <button type="button" className="text-button" aria-label="添加对象" onClick={() => onAdvancedDraftChange({ targets: advancedDraft.targets.length < 8 ? [...advancedDraft.targets, ""] : advancedDraft.targets })} disabled={advancedDraft.targets.length >= 8}>
                    <Plus size={14} /> 添加政策对象
                  </button>
                  {!advancedTargetValid && <p className="advanced-validation" role="alert">请填写 2—8 个有效政策编号。</p>}
                  {advancedDraft.targets.filter((item) => item.trim()).length > 1 && new Set(advancedDraft.targets.map((item) => item.trim()).filter(Boolean)).size !== advancedDraft.targets.filter((item) => item.trim()).length && <p className="advanced-validation" role="alert">请填写不同的政策编号，重复对象无法进行比较。</p>}
                </div>
              )}
              <label className="advanced-field">
                <span>研究问题</span>
                <textarea rows={2} value={advancedDraft.question} onChange={(event) => onAdvancedDraftChange({ question: event.target.value })} />
              </label>
              <div className="advanced-dimensions" aria-label="研究维度">
                <span>研究维度</span>
                <div className="choice-chips">
                  {dimensions.map((dimension) => (
                    <button
                      type="button"
                      key={dimension}
                      className={`choice-chip ${advancedDraft.dimensions.includes(dimension) ? "is-selected" : ""}`}
                      aria-pressed={advancedDraft.dimensions.includes(dimension)}
                      onClick={() => onAdvancedDraftChange({ dimensions: advancedDraft.dimensions.includes(dimension) ? advancedDraft.dimensions.filter((item) => item !== dimension) : [...advancedDraft.dimensions, dimension] })}
                    >{dimension}</button>
                  ))}
                </div>
              </div>
              <label className="advanced-field">
                <span>自定义维度（选填）</span>
                <input value={advancedDraft.customDimension} onChange={(event) => onAdvancedDraftChange({ customDimension: event.target.value })} onKeyDown={(event) => { if (event.key === "Enter" && advancedDraft.customDimension.trim()) { event.preventDefault(); const item = advancedDraft.customDimension.trim(); onAdvancedDraftChange({ dimensions: advancedDraft.dimensions.includes(item) ? advancedDraft.dimensions : [...advancedDraft.dimensions, item], customDimension: "" }); } }} />
              </label>
              <button type="button" className="text-button" onClick={() => { const item = advancedDraft.customDimension.trim(); if (!item) return; onAdvancedDraftChange({ dimensions: advancedDraft.dimensions.includes(item) ? advancedDraft.dimensions : [...advancedDraft.dimensions, item], customDimension: "" }); }} disabled={!advancedDraft.customDimension.trim()}>添加自定义维度</button>
              <p className="advanced-note">输入后按 Enter 或点击“添加自定义维度”，研究请求会携带该维度。</p>
              {advancedDraft.dimensions.some((item) => !dimensions.includes(item)) && <div className="custom-dimensions">自定义：{advancedDraft.dimensions.filter((item) => !dimensions.includes(item)).map((item) => <button type="button" key={item} onClick={() => onAdvancedDraftChange({ dimensions: advancedDraft.dimensions.filter((value) => value !== item) })}>{item} ×</button>)}</div>}
              {onOpenCatalog && <button type="button" className="text-button" onClick={onOpenCatalog}>前往文献目录查找编号</button>}
              <button type="button" className="advanced-submit" onClick={submitAdvanced} disabled={busy || !advancedTargetValid}>
                使用高级入口执行
              </button>
              <p className="advanced-note">{mode === "compare" ? "一般比较问题会自动检索政策库；只有需要手动覆盖政策选择时才使用本入口。" : "高级入口会按你指定的编号研究。"}{!advancedTargetValid ? "填写有效编号后即可执行。" : ""}</p>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
