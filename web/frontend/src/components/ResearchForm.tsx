import { useState } from "react";
import {
  ArrowRight,
  ArrowUp,
  FileSearch,
  GitCompareArrows,
  ListTree,
  Plus,
  Sparkles,
  Waypoints,
} from "lucide-react";
import type { ResearchRequest, TaskType } from "../types";

type Props = {
  onSubmit: (request: ResearchRequest) => void;
  initialRequest?: ResearchRequest;
};

const modes: { id: TaskType; label: string; icon: typeof Sparkles }[] = [
  { id: "qa", label: "政策问答", icon: Sparkles },
  { id: "extract", label: "要素提取", icon: FileSearch },
  { id: "organize", label: "文献梳理", icon: ListTree },
  { id: "compare", label: "政策比较", icon: GitCompareArrows },
  { id: "compare_plans", label: "多方案分析", icon: Waypoints },
];

const dimensions = [
  "支持对象",
  "申报条件",
  "支持措施",
  "资金标准",
  "办理流程",
  "实施期限",
];
const firstQuestion = "梳理十四五时期北京数字经济相关政策";
const isPlanId = (value: string) => /^[A-Za-z0-9_-]{1,64}$/.test(value);

function initialDrafts(request?: ResearchRequest) {
  const targets = request?.targets ?? [];
  return {
    qa: request?.task_type === "qa" ? request.question : "",
    extract: {
      object: targets[0] ?? "",
      custom: request?.task_type === "extract" ? request.question : "",
      dimensions:
        request?.task_type === "extract" ? (request.dimensions ?? []) : [],
    },
    organize: {
      topic: request?.task_type === "organize" ? request.question : "",
      scope: "",
    },
    compare: {
      targets:
        request?.task_type === "compare" && targets.length >= 2
          ? targets
          : ["", ""],
      dimensions:
        request?.task_type === "compare" ? (request.dimensions ?? []) : [],
    },
    compare_plans: {
      targets:
        request?.task_type === "compare_plans" && targets.length >= 2
          ? targets
          : ["", ""],
      dimensions:
        request?.task_type === "compare_plans"
          ? (request.dimensions ?? [])
          : [],
      custom: request?.task_type === "compare_plans" ? request.question : "",
    },
  };
}

export function ResearchForm({ onSubmit, initialRequest }: Props) {
  const [mode, setMode] = useState<TaskType>(initialRequest?.task_type ?? "qa");
  const [drafts, setDrafts] = useState(() => initialDrafts(initialRequest));
  const setQA = (value: string) =>
    setDrafts((current) => ({ ...current, qa: value }));
  const toggleDimension = (
    key: "extract" | "compare" | "compare_plans",
    value: string,
  ) => {
    setDrafts((current) => {
      const selected = current[key].dimensions;
      return {
        ...current,
        [key]: {
          ...current[key],
          dimensions: selected.includes(value)
            ? selected.filter((item) => item !== value)
            : [...selected, value],
        },
      };
    });
  };
  const updateTarget = (
    key: "compare" | "compare_plans",
    index: number,
    value: string,
  ) => {
    setDrafts((current) => {
      const targets = [...current[key].targets];
      targets[index] = value;
      return { ...current, [key]: { ...current[key], targets } };
    });
  };
  const addTarget = (key: "compare" | "compare_plans") => {
    setDrafts((current) =>
      current[key].targets.length >= 8
        ? current
        : {
            ...current,
            [key]: { ...current[key], targets: [...current[key].targets, ""] },
          },
    );
  };
  const removeTarget = (key: "compare" | "compare_plans", index: number) => {
    setDrafts((current) => ({
      ...current,
      [key]: {
        ...current[key],
        targets: current[key].targets.filter(
          (_, position) => position !== index,
        ),
      },
    }));
  };

  const request = (): ResearchRequest | null => {
    if (mode === "qa") {
      const question = drafts.qa.trim();
      return question ? { question, task_type: mode } : null;
    }
    if (mode === "extract") {
      const target = drafts.extract.object.trim();
      if (!isPlanId(target)) return null;
      const question =
        drafts.extract.custom.trim() || `请提取“${target}”的相关政策要素。`;
      return {
        question,
        task_type: mode,
        targets: [target],
        dimensions: drafts.extract.dimensions,
      };
    }
    if (mode === "organize") {
      const topic = drafts.organize.topic.trim();
      if (!topic) return null;
      const question = drafts.organize.scope.trim()
        ? `${topic}。研究范围：${drafts.organize.scope.trim()}`
        : topic;
      return { question, task_type: mode };
    }
    const form = drafts[mode];
    const targets = form.targets.map((target) => target.trim());
    if (
      targets.length < 2 ||
      targets.length > 8 ||
      targets.some((target) => !isPlanId(target))
    )
      return null;
    const custom =
      mode === "compare_plans" ? drafts.compare_plans.custom.trim() : "";
    const question =
      custom ||
      `${mode === "compare" ? "比较" : "分析"}以下政策对象：${targets.join("；")}`;
    return { question, task_type: mode, targets, dimensions: form.dimensions };
  };

  const submit = () => {
    const value = request();
    if (
      value &&
      uniqueTargets &&
      targetCountValid &&
      extractTargetValid &&
      activeTargetIdsValid
    )
      onSubmit(value);
  };
  const value = request();
  const activeTargets =
    mode === "compare" || mode === "compare_plans"
      ? drafts[mode].targets.map((target) => target.trim())
      : [];
  const extractTarget = drafts.extract.object.trim();
  const hasExtractTarget = Boolean(extractTarget);
  const extractTargetValid = !hasExtractTarget || isPlanId(extractTarget);
  const activeTargetIdsValid = activeTargets.every(
    (target) => !target || isPlanId(target),
  );
  const hasEnteredTargets = activeTargets.some(Boolean);
  const uniqueTargets = activeTargets.length
    ? new Set(activeTargets.filter(Boolean)).size ===
      activeTargets.filter(Boolean).length
    : true;
  const targetCountValid = activeTargets.length
    ? activeTargets.length >= 2 &&
      activeTargets.length <= 8 &&
      activeTargets.every(Boolean)
    : true;
  const changeMode = (nextMode: TaskType) => {
    setDrafts((current) => {
      const reusableQuestion =
        mode === "qa"
          ? current.qa
          : mode === "extract"
            ? current.extract.custom
            : mode === "organize"
              ? current.organize.topic
              : current.compare_plans.custom;
      const copy = { ...current };
      if (nextMode === "qa" && !copy.qa.trim()) copy.qa = reusableQuestion;
      if (nextMode === "organize" && !copy.organize.topic.trim())
        copy.organize = { ...copy.organize, topic: reusableQuestion };
      if (nextMode === "extract" && !copy.extract.custom.trim())
        copy.extract = { ...copy.extract, custom: reusableQuestion };
      if (
        nextMode === "compare" &&
        copy.compare.targets.every((item) => !item.trim()) &&
        mode === "compare_plans"
      )
        copy.compare = {
          ...copy.compare,
          targets: [...copy.compare_plans.targets],
        };
      if (
        nextMode === "compare_plans" &&
        copy.compare_plans.targets.every((item) => !item.trim()) &&
        mode === "compare"
      )
        copy.compare_plans = {
          ...copy.compare_plans,
          targets: [...copy.compare.targets],
        };
      return copy;
    });
    setMode(nextMode);
  };

  return (
    <section
      className="research-composer"
      aria-label="研究需求"
      onKeyDown={(event) => {
        if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
          event.preventDefault();
          submit();
        }
      }}
    >
      <div className="composer-topline">
        <span className="composer-dot" /> 开始一次政策研究
      </div>
      <div className="composer-fields" key={mode}>
        {mode === "qa" && (
          <textarea
            aria-label="研究问题"
            className="composer-question"
            placeholder="输入你想研究的政策问题……"
            rows={3}
            value={drafts.qa}
            onChange={(event) => setQA(event.target.value)}
          />
        )}

        {mode === "extract" && (
          <div className="mode-form">
            <label className="field-label" htmlFor="extract-object">
              政策编号 <span>planid，如 DEMO-A</span>
            </label>
            <input
              id="extract-object"
              className="text-input"
              placeholder="输入政策编号，例如 DEMO-A"
              value={drafts.extract.object}
              onChange={(event) =>
                setDrafts((current) => ({
                  ...current,
                  extract: { ...current.extract, object: event.target.value },
                }))
              }
            />
            <div className="field-label dimension-label">
              提取维度 <span>可多选</span>
            </div>
            <div className="choice-chips">
              {dimensions.map((item) => (
                <button
                  type="button"
                  className={`choice-chip ${drafts.extract.dimensions.includes(item) ? "is-selected" : ""}`}
                  aria-pressed={drafts.extract.dimensions.includes(item)}
                  key={item}
                  onClick={() => toggleDimension("extract", item)}
                >
                  {item}
                </button>
              ))}
            </div>
            <label className="field-label" htmlFor="extract-custom">
              补充要求 <span>选填</span>
            </label>
            <textarea
              id="extract-custom"
              className="text-input form-textarea"
              placeholder="例如：重点关注面向中小企业的资金支持"
              rows={2}
              value={drafts.extract.custom}
              onChange={(event) =>
                setDrafts((current) => ({
                  ...current,
                  extract: { ...current.extract, custom: event.target.value },
                }))
              }
            />
          </div>
        )}

        {mode === "organize" && (
          <div className="mode-form">
            <label className="field-label" htmlFor="organize-topic">
              研究主题
            </label>
            <textarea
              id="organize-topic"
              className="text-input form-textarea"
              placeholder="例如：梳理支持中小企业数字化转型的政策"
              rows={2}
              value={drafts.organize.topic}
              onChange={(event) =>
                setDrafts((current) => ({
                  ...current,
                  organize: { ...current.organize, topic: event.target.value },
                }))
              }
            />
            <label className="field-label" htmlFor="organize-scope">
              研究范围 <span>选填 · 用自然语言描述</span>
            </label>
            <input
              id="organize-scope"
              className="text-input"
              placeholder="例如：北京市，重点关注近五年发布的文件"
              value={drafts.organize.scope}
              onChange={(event) =>
                setDrafts((current) => ({
                  ...current,
                  organize: { ...current.organize, scope: event.target.value },
                }))
              }
            />
          </div>
        )}

        {(mode === "compare" || mode === "compare_plans") && (
          <div className="mode-form">
            <div className="field-label">
              {mode === "compare" ? "待比较政策编号" : "方案或政策编号"}{" "}
              <span>填写 2—8 个不同的 planid，如 DEMO-A</span>
            </div>
            <div className="target-list">
              {drafts[mode].targets.map((target, index) => (
                <div className="target-row" key={index}>
                  <span className="target-index">
                    {String(index + 1).padStart(2, "0")}
                  </span>
                  <input
                    className="text-input"
                    aria-label={`研究对象编号 ${index + 1}`}
                    placeholder={
                      index < 2
                        ? `政策编号，例如 DEMO-${index === 0 ? "A" : "B"}`
                        : "输入政策编号，例如 DEMO-C"
                    }
                    value={target}
                    onChange={(event) =>
                      updateTarget(mode, index, event.target.value)
                    }
                  />
                  {drafts[mode].targets.length > 2 && (
                    <button
                      type="button"
                      className="icon-button remove-target"
                      aria-label={`移除研究对象 ${index + 1}`}
                      onClick={() => removeTarget(mode, index)}
                    >
                      <span aria-hidden="true">×</span>
                    </button>
                  )}
                </div>
              ))}
            </div>
            <button
              type="button"
              className="text-button add-target"
              onClick={() => addTarget(mode)}
              disabled={drafts[mode].targets.length >= 8}
            >
              <Plus size={15} /> 添加对象
            </button>
            <div className="field-label dimension-label">
              {mode === "compare" ? "比较维度" : "分析维度"} <span>可多选</span>
            </div>
            <div className="choice-chips">
              {dimensions.map((item) => (
                <button
                  type="button"
                  className={`choice-chip ${drafts[mode].dimensions.includes(item) ? "is-selected" : ""}`}
                  aria-pressed={drafts[mode].dimensions.includes(item)}
                  key={item}
                  onClick={() => toggleDimension(mode, item)}
                >
                  {item}
                </button>
              ))}
            </div>
            {mode === "compare_plans" && (
              <>
                <label className="field-label" htmlFor="plans-custom">
                  自定义分析要求 <span>选填</span>
                </label>
                <textarea
                  id="plans-custom"
                  className="text-input form-textarea"
                  placeholder="描述你希望重点分析的内容"
                  rows={2}
                  value={drafts.compare_plans.custom}
                  onChange={(event) =>
                    setDrafts((current) => ({
                      ...current,
                      compare_plans: {
                        ...current.compare_plans,
                        custom: event.target.value,
                      },
                    }))
                  }
                />
              </>
            )}
          </div>
        )}
      </div>
      {hasEnteredTargets && !uniqueTargets && (
        <div className="form-validation" role="alert">
          请填写不同的政策编号，重复对象无法进行比较。
        </div>
      )}
      {hasEnteredTargets && !activeTargetIdsValid && (
        <div className="form-validation" role="alert">
          政策编号仅可包含字母、数字、下划线或短横线，长度为 1—64 个字符。
        </div>
      )}
      {hasEnteredTargets && !targetCountValid && (
        <div className="form-validation" role="alert">
          请完整填写 2—8 个政策编号后再开始研究。
        </div>
      )}
      {mode === "extract" && hasExtractTarget && !extractTargetValid && (
        <div className="form-validation" role="alert">
          政策编号仅可包含字母、数字、下划线或短横线，长度为 1—64 个字符。
        </div>
      )}
      <div className="composer-footer">
        <div className="active-mode-label">
          <span className="active-mode-mark" />
          {modes.find((item) => item.id === mode)?.label}
          <span className="footer-divider" />
          {mode === "organize"
            ? "开放主题研究"
            : mode === "qa"
              ? "基于政策文献"
              : "明确研究对象"}
        </div>
        <button
          type="button"
          className="submit-button"
          onClick={submit}
          disabled={
            !value ||
            !uniqueTargets ||
            !targetCountValid ||
            !extractTargetValid ||
            !activeTargetIdsValid
          }
          aria-label="开始研究"
        >
          开始研究 <ArrowUp size={16} strokeWidth={2.2} />
        </button>
      </div>
      <div className="composer-hint">
        按 <kbd>⌘</kbd> / <kbd>Ctrl</kbd> + <kbd>Enter</kbd> 开始研究
      </div>
      <div className="mode-tabs" role="tablist" aria-label="研究模式">
        {modes.map(({ id, label, icon: Icon }) => (
          <button
            type="button"
            role="tab"
            aria-selected={mode === id}
            className={`mode-tab ${mode === id ? "is-active" : ""}`}
            key={id}
            onClick={() => changeMode(id)}
          >
            <Icon size={15} strokeWidth={1.8} />
            <span>{label}</span>
            {mode === id && <ArrowRight size={12} className="mode-tab-arrow" />}
          </button>
        ))}
      </div>
      {mode === "qa" && (
        <div className="quick-start">
          <span>试试</span>
          {[
            firstQuestion,
            "北京市高层次人才有哪些支持措施？",
            "比较两份人才政策的主要差异",
          ].map((question) => (
            <button
              type="button"
              key={question}
              onClick={() => setQA(question)}
            >
              {question}
            </button>
          ))}
        </div>
      )}
    </section>
  );
}
