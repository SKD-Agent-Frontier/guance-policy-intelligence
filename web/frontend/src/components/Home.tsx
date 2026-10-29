import { useState } from "react";
import { BookOpenText } from "lucide-react";
import { ResearchForm } from "./ResearchForm";
import type { ResearchRequest, TaskType } from "../types";

type Props = {
  onSubmit: (request: ResearchRequest) => void;
  initialRequest?: ResearchRequest;
  onHistory: () => void;
  onSettings: () => void;
  onAbout: () => void;
};

export function Home({
  onSubmit,
  initialRequest,
  onHistory,
  onSettings,
  onAbout,
}: Props) {
  const [requestedMode, setRequestedMode] = useState<TaskType>("qa");

  return (
    <main className="home-page" id="home">
      <header className="topbar home-topbar">
        <a className="brand" href="#home" aria-label="管策智研首页">
          <span className="brand-mark">
            <BookOpenText size={19} strokeWidth={1.8} />
          </span>
          <span>管策智研</span>
        </a>
        <nav className="home-nav" aria-label="主导航">
          <button
            className={`home-nav-link ${requestedMode !== "organize" ? "is-current" : ""}`}
            type="button"
            onClick={() => setRequestedMode("qa")}
          >
            研究
          </button>
          <button
            className={`home-nav-link ${requestedMode === "organize" ? "is-current" : ""}`}
            type="button"
            onClick={() => setRequestedMode("organize")}
          >
            文献
          </button>
          <button className="home-nav-link" type="button" onClick={onHistory}>
            数据
          </button>
          <button className="home-nav-link" type="button" onClick={onAbout}>
            关于
          </button>
        </nav>
        <button
          className="home-profile"
          type="button"
          aria-label="打开演示设置"
          onClick={onSettings}
        >
          <img src="/demo-avatar.png" alt="" />
        </button>
      </header>

      <section className="home-main" aria-label="政策研究">
        <div className="home-content">
          <div className="intro-block">
            <h1>
              以政策研究之智
              <br />
              探索智能时代的公共治理
            </h1>
            <p>基于权威政策文献的专业分析平台</p>
          </div>
          <ResearchForm
            onSubmit={onSubmit}
            initialRequest={initialRequest}
            requestedMode={requestedMode}
            onModeSelect={setRequestedMode}
          />
        </div>

        <div className="architecture-scene" aria-hidden="true">
          <img src="/research-architecture.png" alt="" />
          <div className="architecture-title">
            <strong>政策智探</strong>
            <span>POLICY INTELLIGENCE</span>
          </div>
          <div className="architecture-mark">
            <span>POLICY<br />INTELLIGENCE</span>
          </div>
        </div>
      </section>
    </main>
  );
}
