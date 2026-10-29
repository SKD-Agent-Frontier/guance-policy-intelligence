import { BookOpenText, Clock3, Settings2, ShieldCheck } from "lucide-react";
import { ResearchForm } from "./ResearchForm";
import type { ResearchRequest } from "../types";

type Props = {
  onSubmit: (request: ResearchRequest) => void;
  initialRequest?: ResearchRequest;
  onHistory: () => void;
  onSettings: () => void;
};

export function Home({
  onSubmit,
  initialRequest,
  onHistory,
  onSettings,
}: Props) {
  return (
    <main className="home-page">
      <header className="topbar home-topbar">
        <a className="brand" href="#home" aria-label="管策智研首页">
          <span className="brand-mark">
            <BookOpenText size={18} strokeWidth={1.7} />
          </span>
          <span>管策智研</span>
          <span className="brand-divider" />
          <span className="brand-caption">政策研究工作台</span>
        </a>
        <nav className="top-actions" aria-label="主导航">
          <button className="top-action" type="button" onClick={onHistory}>
            <Clock3 size={15} />
            历史记录
          </button>
          <button
            className="top-action settings-action"
            type="button"
            onClick={onSettings}
          >
            <Settings2 size={15} />
            设置
          </button>
          <span className="top-status">
            <ShieldCheck size={14} />
            证据可溯源
          </span>
        </nav>
      </header>

      <div className="home-content">
        <div className="intro-block">
          <div className="eyebrow">
            <span /> POLICY INTELLIGENCE
          </div>
          <h1>
            让政策研究<span>更智能</span>
          </h1>
          <p>基于权威政策文献，探索、梳理与理解政策</p>
        </div>
        <ResearchForm onSubmit={onSubmit} initialRequest={initialRequest} />
        <div className="architecture-scene" aria-hidden="true">
          <img src="/research-architecture.svg" alt="" />
        </div>
        <div className="home-note">
          <span className="note-line" />
          从一个问题开始，循着证据深入研究
          <span className="note-line" />
        </div>
      </div>
      <footer className="home-footer">
        <span>管策智研 · 政策智能研究平台</span>
        <span>本地演示环境</span>
      </footer>
    </main>
  );
}
