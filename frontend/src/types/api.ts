export interface Subscription {
  id: string;
  version: number;
  persona: string;
  topics: string[];
  include_keywords: string[];
  exclude_keywords: string[];
  recipient_email: string;
  recipient_verified: boolean;
  timezone: string;
  local_send_time: string;
  max_items: number;
  lookback_hours: number;
  enabled: boolean;
  next_run_at: string | null;
}
export interface ServiceStatus {
  model_configured: boolean;
  mail_configured: boolean;
  news_provider: string;
}
export interface Run {
  id: string;
  run_id: string;
  status: string;
  business_date: string;
  trigger_type: string;
  model_calls: number;
  tool_calls: number;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  error_code: string | null;
  error_message: string | null;
  brief_id: string | null;
}
export interface ToolCall {
  id: string;
  sequence: number;
  tool_name: string;
  arguments: Record<string, string>;
  status: string;
  duration_ms: number;
  error_code: string | null;
}
export interface BriefSummary {
  edition?: number;
  id: string;
  title: string;
  overview: string;
  business_date: string;
  no_news: boolean;
}
export interface Source {
  title: string;
  url: string;
  source_name: string;
  published_at: string | null;
  evidence_level: string;
}
export interface Brief extends BriefSummary {
  window_start: string;
  window_end: string;
  items: {
    headline: string;
    summary: string;
    why_it_matters: string;
    sources: Source[];
  }[];
  delivery: {
    id: string;
    status: string;
    attempt_count: number;
    last_error_code: string | null;
  } | null;
}
export const statuses: Record<string, string> = {
  queued: "等待生成",
  running: "正在整理",
  succeeded: "简报已生成",
  incomplete: "生成未完成",
  failed: "失败",
  cancelled: "已取消",
  pending: "等待发送",
  sending: "发送中",
  accepted: "邮件服务器已接受",
  retry_wait: "等待重试",
  unknown: "发送结果待核查",
};
export function statusLabel(value: string): string {
  return statuses[value] ?? value;
}
export function formatTime(value: string | null): string {
  return value
    ? new Date(value).toLocaleString("zh-CN", { hour12: false })
    : "—";
}

export interface ChatSession {
  id: string;
  title: string;
  created_at: string;
}
export type ChatAction = "chat" | "generate" | "generate_send";
export interface ChatTurn {
  id: string;
  question: string;
  answer: string | null;
  action: ChatAction;
  status: string;
  error_code: string | null;
  run_id: string | null;
  created_at: string;
  tools: ToolCall[];
  brief: Brief | null;
}
