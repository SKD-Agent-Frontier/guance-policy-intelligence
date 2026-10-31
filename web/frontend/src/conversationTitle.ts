import type { TaskType } from "./types";

/**
 * A fast, local, evidence-neutral title for the FIRST user turn.
 *
 * No network/model call: title creation must not delay a policy research request.
 * Only the sidebar label is affected; the original question is kept verbatim in history.
 * Prefer explicit place/period/topic + intent over copying the first N characters.
 */
const provinces = [
  "北京", "天津", "河北", "山西", "内蒙古", "辽宁", "吉林", "黑龙江",
  "上海", "江苏", "浙江", "安徽", "福建", "江西", "山东", "河南",
  "湖北", "湖南", "广东", "广西", "海南", "重庆", "四川", "贵州",
  "云南", "西藏", "陕西", "甘肃", "青海", "宁夏", "新疆", "台湾",
  "香港", "澳门",
];

const PRELUDE = /^(?:我想了解|我想知道|我想问|请帮我|帮我|请问|请帮忙|我想|麻烦|能否|能不能|请|想了解)+/;

function identifyPlaces(question: string): string[] {
  const normalized = question.replace(PRELUDE, "");
  const found: Array<{ index: number; name: string; city: boolean }> = [];

  // City and county labels outrank a province mentioned only as comparison context.
  for (const match of normalized.matchAll(/[\u4e00-\u9fff]{2,5}(?:市|县|区)(?=[在于的与和第十一二三四五，、。；：！？”“‘’"\s]|$)/g)) {
    const name = match[0];
    if (/^(?:其他地市|其它地市|各地市|发展城市)$/.test(name)) continue;
    found.push({ index: match.index ?? 0, name, city: true });
  }
  for (const province of provinces) {
    const at = normalized.indexOf(province);
    if (at < 0) continue;
    // A city already containing the province's name is the same geographic object.
    if (found.some((candidate) => candidate.name.startsWith(province))) continue;
    found.push({ index: at, name: province, city: false });
  }

  const cities = found.filter((item) => item.city).sort((a, b) => a.index - b.index);
  const genericProvinceComparison = /其他地市|其它地市|全省其他|省内其他|省内其余/.test(question);
  const all = (cities.length === 1 && genericProvinceComparison
    ? cities
    : found.sort((a, b) => a.index - b.index));
  return [...new Set(all.map((item) => item.name))].slice(0, 2);
}

function identifyPeriod(question: string): string {
  const direct = question.match(/(十一|十二|十三|十四|十五|十)五/);
  const expanded = question.match(/第?(十一|十二|十三|十四|十五|十)个五年/);
  const period = direct?.[1] ?? expanded?.[1];
  return period ? `“${period}五”` : "";
}

function identifySubject(question: string): { subject: string; complete: boolean } | null {
  if (/(?:多少|几条|几篇|总数|数量|统计|一共有|共计).*(?:政策|文献)|(?:政策|文献).*(?:多少|几条|几篇|总数|数量)/.test(question)) {
    return { subject: "政策文献数量统计", complete: true };
  }
  if (question.includes("经济社会发展") && /(?:成就|成果|成绩|取得|变化)/.test(question)) {
    return { subject: "经济社会发展成就", complete: true };
  }
  if (/发展环境|发展机遇|面临的挑战|机遇和挑战|机遇与挑战/.test(question)) {
    return { subject: "发展环境", complete: false };
  }
  if (question.includes("数字经济") && /发展目标|主要目标|规划目标/.test(question)) {
    return { subject: "数字经济目标", complete: false };
  }
  if (question.includes("产业结构调整")) return { subject: "产业结构调整", complete: false };
  if (question.includes("产业布局")) return { subject: "产业布局", complete: false };
  if (question.includes("数字经济")) return { subject: "数字经济政策", complete: false };
  if (/人工智能|AI产业/.test(question)) return { subject: "人工智能政策", complete: false };
  if (question.includes("产业") && /规划|政策|布局|方向/.test(question)) {
    return { subject: "产业规划", complete: false };
  }
  if (/发展目标|规划目标/.test(question)) return { subject: "发展目标", complete: false };
  if (/政策工具/.test(question)) return { subject: "政策工具", complete: false };
  if (/支持措施|扶持措施|扶持政策|支持政策/.test(question)) {
    return { subject: "政策支持措施", complete: false };
  }
  if (/实施路径|推进路径/.test(question)) return { subject: "实施路径", complete: false };
  if (/重点任务|主要任务|重点工作/.test(question)) return { subject: "重点任务", complete: false };
  if (/申报条件|申请条件/.test(question)) return { subject: "政策申报条件", complete: false };
  if (/经济社会发展/.test(question)) return { subject: "经济社会发展", complete: false };
  return null;
}

function labelIntent(question: string, mode: TaskType, subject: string): string {
  if (subject === "政策文献数量统计" || subject === "经济社会发展成就") return "";
  if (mode === "compare" || /比较|对比|异同|差异|区别|相比/.test(question)) return "比较";
  if (mode === "compare_plans") return "方案分析";
  if (mode === "extract" || /提取|抽取/.test(question)) return "提取";
  if (mode === "organize" || /梳理|整理|汇总/.test(question)) return "梳理";
  if (subject === "发展环境" || /分析|影响|原因|如何|怎样|为什么/.test(question)) return "分析";
  if (subject === "政策支持措施" || subject === "政策申报条件") return "查询";
  return "研究";
}

export function summarizeConversationTitle(firstQuestion: string, mode: TaskType): string {
  const question = firstQuestion.replace(/\s+/g, " ").trim();
  if (!question) return "新对话";
  // A short explicit label needs no artificial suffix.
  if (question.length <= 12 && !/[？?！!]/.test(question) && !/^(?:比较|分析|提取|梳理|查询|请)/.test(question)) {
    return question;
  }

  const places = identifyPlaces(question);
  const period = identifyPeriod(question);
  const topic = identifySubject(question);
  if (topic) {
    const comparing = mode === "compare" || /比较|对比|异同|差异|区别|相比/.test(question);
    const location = comparing && places.length === 2
      ? `${places[0]}与${places[1]}`
      : (places.find((place) => /(?:市|县|区)$/.test(place)) ?? places[0] ?? "");
    const suffix = topic.complete ? "" : labelIntent(question, mode, topic.subject);
    const core = `${location}${period}${topic.subject}`;
    const title = `${core}${suffix}`;
    return [...title].slice(0, 28).join("");
  }

  // For questions outside the known policy topics, remove conversational phrasing
  // and use a bounded subject; never show a raw 32-character truncation.
  const cleaned = question.replace(PRELUDE, "")
    .replace(/^(?:关于|对|针对|有关)/, "")
    .replace(/[？?！!。]+$/, "")
    .replace(/(?:有哪些|是什么|怎么样|如何|为什么|为何|吗|呢)$/, "")
    .trim();
  if (cleaned.length <= 18) return cleaned || "政策研究";
  const phrase = cleaned.split(/[，,；;？?。]/)[0] || cleaned;
  const excerpt = [...phrase].slice(0, 18).join("").replace(/(?:的|在|与|和|有|哪|是)$/, "");
  return `${excerpt}研究`;
}
