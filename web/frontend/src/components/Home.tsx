import { ResearchForm } from "./ResearchForm";
import type { ResearchRequest, TaskType } from "../types";

type Props = {
  mode: TaskType;
  value: string;
  busy: boolean;
  onChange: (value: string) => void;
  onModeChange: (mode: TaskType) => void;
  onSend: (message: string) => void;
  onAdvancedSubmit: (request: ResearchRequest) => void;
};

export function Home({ mode, value, busy, onChange, onModeChange, onSend, onAdvancedSubmit }: Props) {
  return <main className="home-page" id="home">
    <section className="home-main" aria-label="政策研究">
      <div className="home-content">
        <div className="intro-block">
          <h1>以政策研究之智<br />探索智能时代的公共治理</h1>
          <p>基于权威政策文献的专业分析平台</p>
        </div>
        <ResearchForm mode={mode} value={value} busy={busy} onChange={onChange} onModeChange={onModeChange} onSend={onSend} onAdvancedSubmit={onAdvancedSubmit} />
      </div>
      <div className="architecture-scene" aria-hidden="true">
        <img src="/research-architecture.png" alt="" />
        <div className="architecture-title"><strong>政策智探</strong><span>POLICY INTELLIGENCE</span></div>
        <div className="architecture-mark"><span>POLICY<br />INTELLIGENCE</span></div>
      </div>
    </section>
  </main>;
}
