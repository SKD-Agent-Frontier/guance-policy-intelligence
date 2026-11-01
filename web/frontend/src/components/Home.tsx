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
          <div className="home-brand-lockup" aria-label="管策智研 · 政策智能研究助手">
            <img src="/guance-mark.svg" alt="" />
            <div className="home-brand-copy"><strong>管策智研</strong><span>政策智能研究助手</span></div>
          </div>
          <h1>让政策更可理解<br />让决策更有依据</h1>
        </div>
        <ResearchForm mode={mode} value={value} busy={busy} onChange={onChange} onModeChange={onModeChange} onSend={onSend} onAdvancedSubmit={onAdvancedSubmit} advancedDraft={advancedDraft} onAdvancedDraftChange={onAdvancedDraftChange} onOpenCatalog={onOpenCatalog} enableTemplates />
      </div>
      <div className="architecture-scene" aria-hidden="true">
        <img src="/research-mountain-architecture.webp" alt="" />
      </div>
    </section>
  </main>;
}
