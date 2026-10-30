import { useEffect, useRef, useState } from "react";
import {
  ArrowUp,
  Plus,
  X,
} from "lucide-react";
import type { ResearchRequest, TaskType } from "../types";

type Props = {
  mode: TaskType;
  value: string;
  busy: boolean;
  onChange: (value: string) => void;
  onModeChange: (mode: TaskType) => void;
  onSend: (message: string) => void;
  onAdvancedSubmit?: (request: ResearchRequest) => void;
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
}: Props) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [target, setTarget] = useState("");
  const [targets, setTargets] = useState(["", ""]);
  const [selectedDimensions, setSelectedDimensions] = useState<string[]>([]);
  const [advancedQuestion, setAdvancedQuestion] = useState("");
  const [customDimension, setCustomDimension] = useState("");

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
    setAdvancedOpen(false);
    setTarget("");
    setTargets(["", ""]);
    setSelectedDimensions([]);
    setAdvancedQuestion("");
    setCustomDimension("");
  }, [mode]);

  const submitAdvanced = () => {
    if (busy || !onAdvancedSubmit) return;
    if (mode === "extract") {
      const planid = target.trim();
      if (!isPlanId(planid)) return;
      onAdvancedSubmit({
        task_type: mode,
        question: advancedQuestion.trim() || `请提取“${planid}”的相关政策要素。`,
        targets: [planid],
        dimensions: selectedDimensions,
      });
      return;
    }
    if (mode !== "compare" && mode !== "compare_plans") return;
    const cleanTargets = targets.map((item) => item.trim());
    if (cleanTargets.length < 2 || cleanTargets.length > 8 || cleanTargets.some((item) => !isPlanId(item))) return;
    if (new Set(cleanTargets).size !== cleanTargets.length) return;
    onAdvancedSubmit({
      task_type: mode,
      question: advancedQuestion.trim() || `${mode === "compare" ? "比较" : "分析"}以下政策对象：${cleanTargets.join("；")}`,
      targets: cleanTargets,
      dimensions: selectedDimensions,
    });
  };

  const send = () => {
    const message = value.trim();
    if (!busy && message && Array.from(message).length <= 4000) onSend(message);
  };

  const advancedAvailable = mode === "extract" || mode === "compare" || mode === "compare_plans";
  const messageLength = Array.from(value).length;
  const advancedTargets = targets.map((item) => item.trim());
  const advancedTargetValid = mode === "extract"
    ? isPlanId(target.trim())
    : advancedTargets.length >= 2 && advancedTargets.length <= 8 && advancedTargets.every(isPlanId) && new Set(advancedTargets).size === advancedTargets.length;

  return (
    <section className="research-composer" aria-label="研究需求">
      <div className="mode-tabs" role="tablist" aria-label="研究模式">
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
      </div>
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
            aria-expanded={advancedOpen}
            onClick={() => setAdvancedOpen((open) => !open)}
            disabled={busy}
          >
            {advancedOpen ? "收起高级选择" : "高级选择 · 指定政策编号"}
          </button>
          {advancedOpen && (
            <div className="advanced-fields">
              {mode === "extract" ? (
                <div className="advanced-field">
                  <label htmlFor="extract-planid">政策编号（planid）</label>
                  <input id="extract-planid" value={target} onChange={(event) => setTarget(event.target.value)} aria-label="政策编号" />
                  {target.trim() && !isPlanId(target.trim()) && <p className="advanced-validation" role="alert">编号只能包含字母、数字、下划线或短横线，长度为 1—64 个字符。</p>}
                </div>
              ) : (
                <div className="advanced-targets">
                  <span>研究对象编号（2—8 个 planid）</span>
                  {targets.map((item, index) => (
                    <div className="advanced-target-row" key={index}>
                      <input
                        aria-label={`研究对象编号 ${index + 1}`}
                        value={item}
                        onChange={(event) => setTargets((current) => current.map((targetValue, position) => position === index ? event.target.value : targetValue))}
                      />
                      {targets.length > 2 && <button type="button" className="advanced-remove-target" aria-label={`移除研究对象 ${index + 1}`} onClick={() => setTargets((current) => current.filter((_, position) => position !== index))}><X size={14} /></button>}
                    </div>
                  ))}
                  <button type="button" className="text-button" onClick={() => setTargets((current) => current.length < 8 ? [...current, ""] : current)} disabled={targets.length >= 8}>
                    <Plus size={14} /> 添加对象
                  </button>
                  {targets.some((item) => item.trim()) && (targets.some((item) => !isPlanId(item.trim())) || targets.length < 2 || targets.length > 8) && <p className="advanced-validation" role="alert">请填写 2—8 个有效政策编号。</p>}
                  {targets.filter((item) => item.trim()).length > 1 && new Set(targets.map((item) => item.trim()).filter(Boolean)).size !== targets.filter((item) => item.trim()).length && <p className="advanced-validation" role="alert">请填写不同的政策编号，重复对象无法进行比较。</p>}
                </div>
              )}
              <label className="advanced-field">
                <span>补充要求（选填）</span>
                <textarea rows={2} value={advancedQuestion} onChange={(event) => setAdvancedQuestion(event.target.value)} />
              </label>
              <div className="advanced-dimensions" aria-label="研究维度">
                <span>研究维度</span>
                <div className="choice-chips">
                  {dimensions.map((dimension) => (
                    <button
                      type="button"
                      key={dimension}
                      className={`choice-chip ${selectedDimensions.includes(dimension) ? "is-selected" : ""}`}
                      aria-pressed={selectedDimensions.includes(dimension)}
                      onClick={() => setSelectedDimensions((current) => current.includes(dimension) ? current.filter((item) => item !== dimension) : [...current, dimension])}
                    >{dimension}</button>
                  ))}
                </div>
              </div>
              <label className="advanced-field">
                <span>自定义维度（选填）</span>
                <input value={customDimension} onChange={(event) => setCustomDimension(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && customDimension.trim()) { event.preventDefault(); setSelectedDimensions((current) => current.includes(customDimension.trim()) ? current : [...current, customDimension.trim()]); setCustomDimension(""); } }} />
              </label>
              <button type="button" className="text-button" onClick={() => { if (!customDimension.trim()) return; setSelectedDimensions((current) => current.includes(customDimension.trim()) ? current : [...current, customDimension.trim()]); setCustomDimension(""); }} disabled={!customDimension.trim()}>添加自定义维度</button>
              <p className="advanced-note">输入后按 Enter 或点击“添加自定义维度”，研究请求会携带该维度。</p>
              {selectedDimensions.some((item) => !dimensions.includes(item)) && <div className="custom-dimensions">自定义：{selectedDimensions.filter((item) => !dimensions.includes(item)).map((item) => <button type="button" key={item} onClick={() => setSelectedDimensions((current) => current.filter((value) => value !== item))}>{item} ×</button>)}</div>}
              <button type="button" className="advanced-submit" onClick={submitAdvanced} disabled={busy || !advancedTargetValid}>
                使用高级入口执行
              </button>
              <p className="advanced-note">系统不会自动定位比较对象；研究会使用你指定的编号。{!advancedTargetValid ? "填写有效编号后即可执行。" : ""}</p>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
