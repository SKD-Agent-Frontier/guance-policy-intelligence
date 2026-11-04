import { ResearchForm } from "./ResearchForm";
import { ArrowRight, FileSearch, GitCompareArrows, Layers, MessageCircleQuestion, Network } from "lucide-react";
import type { AdvancedDraft } from "../conversationTypes";
import type { ResearchRequest, TaskType } from "../types";

type Props = {
  mode: TaskType;
  value: string;
  busy: boolean;
  onChange: (value: string) => void;
  onModeChange: (mode: TaskType) => void;
  onSend: (message: string) => void;
  onAdvancedSubmit: (request: ResearchRequest) => void;
  advancedDraft: AdvancedDraft;
  onAdvancedDraftChange: (patch: Partial<AdvancedDraft>) => void;
  onOpenCatalog: () => void;
};

export function Home({ mode, value, busy, onChange, onModeChange, onSend, onAdvancedSubmit, advancedDraft, onAdvancedDraftChange, onOpenCatalog }: Props) {
  const modes = [
    { id: "qa" as const, label: "政策问答", Icon: MessageCircleQuestion },
    { id: "extract" as const, label: "要素提取", Icon: Layers },
    { id: "organize" as const, label: "文献梳理", Icon: FileSearch },
    { id: "compare" as const, label: "政策比较", Icon: GitCompareArrows },
    { id: "compare_plans" as const, label: "多方案分析", Icon: Network },
  ];
  const examples = ["“专精特新”中小企业培育的主要政策依据有哪些？", "各地在促进高校毕业生就业方面有哪些政策措施，可以如何比较？"];
  return <main className="home-page" id="home">
    <section className="home-main" aria-label="政策研究">
      <div className="home-content">
        <div className="intro-block">
          <h1>今天想研究什么？</h1>
          <p>从政策原文出发，梳理问题、发现依据。</p>
        </div>
        <div className="home-mode-tabs" role="group" aria-label="研究模式">
        {modes.map(({ id, label, Icon }) => <button key={id} type="button" aria-label={`选择${label}`} aria-pressed={mode === id} className={mode === id ? "is-selected" : ""} onClick={() => onModeChange(id)}><Icon size={19} aria-hidden="true" /><span>{label}</span></button>)}
        </div>
        <ResearchForm mode={mode} value={value} busy={busy} onChange={onChange} onModeChange={onModeChange} onSend={onSend} onAdvancedSubmit={onAdvancedSubmit} advancedDraft={advancedDraft} onAdvancedDraftChange={onAdvancedDraftChange} onOpenCatalog={onOpenCatalog} enableTemplates homeVariant />
        <section className="home-examples" aria-label="示例问题"><h2>试试这样提问</h2>{examples.map((example) => <button key={example} type="button" onClick={() => { onModeChange("qa"); onChange(example); }}><span>{example}</span><ArrowRight size={18} aria-hidden="true" /></button>)}</section>
      </div>
    </section>
  </main>;
}
