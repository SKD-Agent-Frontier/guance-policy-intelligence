import type { PolicyListItem } from "./types";

export interface BuiltInTheme {
  id: string;
  name: string;
  description: string;
  count: number;
}
/** Static classification rules; policy records always come from the published catalog. */
export type ClassifiedPolicy = {
  planid: string;
  title: string;
  area: string | null;
  fyp: string | null;
  tags: string[];
};

export const builtInThemes: BuiltInTheme[] = [
  { id: "comprehensive", name: "综合发展与政府工作", description: "经济社会发展纲要、政府工作与总体部署", count: 0 },
  { id: "industry", name: "产业经济与营商", description: "工业、商贸、服务业、营商环境", count: 0 },
  { id: "digital", name: "数字经济与科技", description: "数字化、科技创新、信息产业", count: 0 },
  { id: "ecology", name: "生态环境与低碳", description: "环境保护、节能减排、生态修复", count: 0 },
  { id: "rural", name: "农业农村与乡村振兴", description: "农业现代化、农村建设、乡村振兴", count: 0 },
  { id: "urban", name: "城乡建设与区域协调", description: "城镇化、国土空间、城乡规划", count: 0 },
  { id: "health", name: "医疗卫生与健康", description: "医疗服务、公共卫生、健康保障", count: 0 },
  { id: "education", name: "教育人才与技能", description: "教育、人才与职业技能", count: 0 },
  { id: "welfare", name: "民生保障与养老", description: "就业、养老、社会保障、民政", count: 0 },
  { id: "culture", name: "文化旅游与体育", description: "文化、旅游、体育与地方志", count: 0 },
  { id: "infrastructure", name: "交通能源与水利", description: "交通、能源、水利与市政基础设施", count: 0 },
  { id: "governance", name: "政府治理与法治", description: "政府治理、司法与市场监管", count: 0 },
  { id: "safety", name: "公共安全与应急", description: "消防、灾害防治与应急管理", count: 0 },
];

const rules: Record<string, RegExp> = {
  comprehensive: /五年规划纲要|经济和社会发展|国民经济和社会发展|政府工作报告|总体规划/,
  industry: /产业|工业|服务业|金融|营商|经济|贸易|招商|商务|商贸/,
  digital: /数字|信息|科技|创新|数据|通信|互联网|智慧|知识产权/,
  ecology: /生态|环保|环境|低碳|污染|节能|减排|自然保护/,
  rural: /农业|农村|农民|乡村|畜牧|养殖|林业|渔业|粮食/,
  urban: /城乡|城市|城镇|住房|国土|土地|区域协调|建设规划/,
  health: /医疗|卫生|健康|医药|医院|公共卫生|医保/,
  education: /教育|人才|技能|职业学校|高校|培训/,
  welfare: /社会保障|养老|就业|民政|残疾|妇女|儿童|福利/,
  culture: /文化|旅游|体育|全民健身|文物|历史|地方志/,
  infrastructure: /交通|铁路|公路|港口|能源|电力|水利|水务|基础设施/,
  governance: /政务|治理|法治|司法|审计|市场监管|档案|行政/,
  safety: /安全|应急|消防|灾害|防灾|防汛|事故/,
};

export function tagsForPolicy(title: string): string[] {
  return builtInThemes.filter((theme) => rules[theme.id].test(title)).map((theme) => theme.id);
}

export function themedPolicies(id: string, policies: PolicyListItem[]): ClassifiedPolicy[] {
  return policies.filter((item) => tagsForPolicy(item.title || "").includes(id))
    .map((item) => ({ planid: item.planid, title: item.title || "未命名政策",
      area: item.area, fyp: item.fyp, tags: tagsForPolicy(item.title || "") }));
}

export function themeCounts(policies: PolicyListItem[]): BuiltInTheme[] {
  return builtInThemes.map((theme) => ({
    ...theme,
    count: policies.filter((item) => tagsForPolicy(item.title || "").includes(theme.id)).length,
  })).filter((theme) => theme.count > 0);
}
