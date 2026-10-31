import type { TaskType } from "./types";

/**
 * V1 仅提供可编辑的自然语言示例，不改变 Agent 路由或承诺所有任务均能完成。
 * 发布前应使用真实政策库逐项回归；需要指定 planid 的模式仍沿用高级入口。
 */
export interface PromptTemplate {
  id: string;
  title: string;
  prompt: string;
}

export const promptTemplates: Record<TaskType, readonly PromptTemplate[]> = {
  qa: [
    {
      id: "qa-facts",
      title: "政策事实查询",
      prompt: "浙江省十四五数字经济相关政策提出了哪些发展目标？",
    },
    {
      id: "qa-direction",
      title: "施策方向分析",
      prompt: "分析十四五时期数字经济相关政策的主要施策方向，并说明原文依据。",
    },
    {
      id: "qa-measures",
      title: "支持措施查询",
      prompt: "十四五时期有关人工智能产业发展的政策提出了哪些支持措施？",
    },
  ],
  extract: [
    {
      id: "extract-goals",
      title: "发展目标提取",
      prompt: "提取所选政策文献中的主要发展目标，并注明对应原文。",
    },
    {
      id: "extract-tools",
      title: "政策工具提取",
      prompt: "提取所选政策文献涉及的主要政策工具及支持对象。",
    },
    {
      id: "extract-measures",
      title: "支持措施提取",
      prompt: "提取所选政策文献中的产业支持措施、适用条件和资金标准。",
    },
  ],
  organize: [
    {
      id: "organize-theme",
      title: "主题政策梳理",
      prompt: "梳理十四五时期数字经济相关政策文献，并按主题归类。",
    },
    {
      id: "organize-region",
      title: "地区政策梳理",
      prompt: "梳理浙江省十四五时期人工智能产业相关政策文献。",
    },
    {
      id: "organize-focus",
      title: "重点议题梳理",
      prompt: "梳理本轮检索到的数字经济政策主要关注哪些议题，并标注出处。",
    },
  ],
  compare: [
    {
      id: "compare-goals",
      title: "发展目标比较",
      prompt: "比较浙江和江苏十四五数字经济发展目标的异同",
    },
    {
      id: "compare-tools",
      title: "政策工具比较",
      prompt: "比较浙江和江苏十四五数字经济相关政策在政策工具方面的异同",
    },
    {
      id: "compare-path",
      title: "实施路径比较",
      prompt: "比较浙江和江苏十四五数字经济相关政策在实施路径方面的异同",
    },
  ],
  compare_plans: [
    {
      id: "plans-advantages",
      title: "优势与局限分析",
      prompt: "分析所选产业扶持政策方案的主要优势与局限，并标注原文依据。",
    },
    {
      id: "plans-applicability",
      title: "适用条件分析",
      prompt: "比较所选政策方案在适用条件和支持对象方面的异同。",
    },
    {
      id: "plans-dimensions",
      title: "多维方案比较",
      prompt: "从支持措施、资金标准与实施期限等维度比较所选政策方案。",
    },
  ],
};
