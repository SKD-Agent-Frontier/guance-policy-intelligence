import { ResearchForm } from "./ResearchForm";
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
  return <main className="home-page" id="home">
    <section className="home-main" aria-label="政策研究">
      <div className="home-content">
        <div className="intro-block">
          <h1>让政策更可理解<br />让决策更有依据</h1>
          <p className="home-subtitle">基于权威政策文献的智能研究助手</p>
        </div>
        <ResearchForm mode={mode} value={value} busy={busy} onChange={onChange} onModeChange={onModeChange} onSend={onSend} onAdvancedSubmit={onAdvancedSubmit} advancedDraft={advancedDraft} onAdvancedDraftChange={onAdvancedDraftChange} onOpenCatalog={onOpenCatalog} />
      </div>
      <div className="architecture-scene" aria-hidden="true">
        <img src="/research-architecture.png" alt="" />
      </div>
    </section>
  </main>;
}
