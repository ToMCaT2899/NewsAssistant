<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from "vue";
import { errorMessage, request, temporaryError } from "../api/client";
import type {
  ChatAction,
  ChatSession,
  ChatTurn,
  ServiceStatus,
  Subscription,
} from "../types/api";
import { formatTime, statusLabel } from "../types/api";

defineProps<{ status: ServiceStatus | null }>();
defineEmits<{ preferences: []; briefs: [] }>();
const threads = ref<ChatSession[]>([]);
const selected = ref("");
const turns = ref<ChatTurn[]>([]);
const message = ref("");
const action = ref<ChatAction>("chat");
const error = ref("");
const syncIssue = ref("");
const lastSyncedAt = ref<string | null>(null);
const sending = ref(false);
const subscription = ref<Subscription | null>(null);
const transcript = ref<HTMLElement | null>(null);
const pending = ref<{
  key: string;
  message: string;
  action: ChatAction;
} | null>(null);
const active = computed(() =>
  turns.value.some((turn) => ["queued", "running"].includes(turn.status)),
);
let timer: ReturnType<typeof setTimeout> | undefined;
let controller: AbortController | undefined;
let epoch = 0;
let disposed = false;
const toolNames: Record<string, string> = {
  fetch_feed: "读取新闻订阅源",
  search_news: "搜索相关新闻",
  fetch_article: "阅读新闻原文",
  get_recent_briefs: "检查历史简报",
  list_briefs: "查找你的简报",
  read_brief: "阅读简报与引用",
  read_preferences: "了解订阅偏好",
  list_dir: "查看任务文件",
  read_file: "读取任务文件",
  write_file: "保存整理结果",
  search_content: "检索任务文件",
  bash: "执行受限命令",
};
function toolStatus(status: string) {
  if (status === "running" && syncIssue.value)
    return "上次记录：调用中（待同步）";
  return (
    (
      {
        running: "正在调用",
        succeeded: "调用完成",
        failed: "调用失败",
        rejected: "已拦截",
      } as Record<string, string>
    )[status] ?? status
  );
}
function linkedText(text: string) {
  return text.split(/(https?:\/\/[^\s<>\])，。]+)/g).map((value) => ({
    value,
    link: /^https?:\/\//.test(value),
  }));
}
function stopPolling() {
  clearTimeout(timer);
  controller?.abort();
  epoch++;
}
async function loadTurns(scroll = false, retryAttempt = 0) {
  if (!selected.value || disposed) return;
  clearTimeout(timer);
  controller?.abort();
  const localEpoch = ++epoch;
  const id = selected.value;
  controller = new AbortController();
  const currentController = controller;
  const timeout = setTimeout(() => currentController.abort(), 15000);
  try {
    const data = await request<ChatTurn[]>(`/chat/sessions/${id}/turns`, {
      signal: controller.signal,
    });
    if (localEpoch !== epoch || disposed) return;
    const nearBottom =
      !transcript.value ||
      transcript.value.scrollHeight -
        transcript.value.scrollTop -
        transcript.value.clientHeight <
        100;
    turns.value = data;
    syncIssue.value = "";
    lastSyncedAt.value = new Date().toISOString();
    if (scroll || nearBottom) {
      await nextTick();
      transcript.value?.scrollTo({
        top: transcript.value.scrollHeight,
        behavior: "auto",
      });
    }
    if (
      data.some(
        (t) =>
          ["queued", "running"].includes(t.status) ||
          (t.brief?.delivery &&
            ["pending", "sending", "retry_wait"].includes(
              t.brief.delivery.status,
            )),
      )
    ) {
      timer = setTimeout(() => loadTurns(), 1200);
    }
  } catch (reason) {
    if (localEpoch !== epoch || disposed) return;
    // 只重试读取进度；后台任务可能已完成，不能自动再次提交生成或发信。
    if (temporaryError(reason) && retryAttempt < 6) {
      syncIssue.value = `进度连接暂时中断，正在自动重连（${retryAttempt + 1}/6）。后台任务可能仍在运行或已完成，请勿重复提交。`;
      const delay = Math.min(1200 * 2 ** retryAttempt, 8000);
      timer = setTimeout(() => loadTurns(false, retryAttempt + 1), delay);
    } else {
      syncIssue.value =
        "暂未恢复进度连接，请点击“刷新对话”。后台任务与邮件结果尚未同步，请勿重复提交。";
    }
  } finally {
    clearTimeout(timeout);
  }
}
async function chooseThread(id: string) {
  stopPolling();
  selected.value = id;
  turns.value = [];
  pending.value = null;
  error.value = "";
  syncIssue.value = "";
  lastSyncedAt.value = null;
  if (id) await loadTurns(true);
}
async function send() {
  if (
    sending.value ||
    syncIssue.value ||
    active.value ||
    (!message.value.trim() && !pending.value)
  )
    return;
  sending.value = true;
  error.value = "";
  pending.value ??= {
    key: crypto.randomUUID(),
    message: message.value.trim(),
    action: action.value,
  };
  const body = pending.value;
  try {
    if (!selected.value) {
      const thread = await request<ChatSession>("/chat/sessions", {
        method: "POST",
      });
      if (disposed) return;
      selected.value = thread.id;
      threads.value.unshift(thread);
    }
    await request(`/chat/sessions/${selected.value}/turns`, {
      method: "POST",
      headers: { "Idempotency-Key": body.key },
      body: JSON.stringify({ message: body.message, action: body.action }),
    });
    if (disposed) return;
    const thread = threads.value.find((t) => t.id === selected.value);
    if (thread) thread.title = body.message.slice(0, 40);
    pending.value = null;
    message.value = "";
    action.value = "chat";
    await loadTurns(true);
  } catch (reason) {
    if (!disposed) error.value = errorMessage(reason);
  } finally {
    sending.value = false;
  }
}
function suggest(mode: ChatAction, text: string) {
  if (active.value || pending.value || sending.value || syncIssue.value) return;
  action.value = mode;
  message.value = text;
}
function reconnect() {
  if (selected.value && (syncIssue.value || active.value)) void loadTurns();
}
function visibleAgain() {
  if (window.document.visibilityState === "visible") reconnect();
}
onMounted(async () => {
  window.addEventListener("online", reconnect);
  window.document.addEventListener("visibilitychange", visibleAgain);
  try {
    const [list, prefs] = await Promise.all([
      request<ChatSession[]>("/chat/sessions"),
      request<Subscription>("/subscription"),
    ]);
    if (disposed) return;
    threads.value = list;
    subscription.value = prefs;
    if (list[0]) await chooseThread(list[0].id);
  } catch (reason) {
    if (!disposed) error.value = errorMessage(reason);
  }
});
onBeforeUnmount(() => {
  disposed = true;
  stopPolling();
  window.removeEventListener("online", reconnect);
  window.document.removeEventListener("visibilitychange", visibleAgain);
});
</script>

<template>
  <section class="page chat-page">
    <header class="page-heading">
      <div>
        <h1>新闻助手</h1>
        <p class="muted">聊聊新闻，或重新整理一份值得读的简报。</p>
      </div>
      <button :disabled="sending" @click="chooseThread('')">新对话</button>
    </header>
    <div class="chat-toolbar">
      <label
        >历史对话<select
          :value="selected"
          :disabled="sending"
          @change="chooseThread(($event.target as HTMLSelectElement).value)"
        >
          <option value="">新的新闻对话</option>
          <option v-for="thread in threads" :key="thread.id" :value="thread.id">
            {{ thread.title }}
          </option>
        </select></label
      >
      <button :disabled="!selected || sending" @click="loadTurns()">
        刷新对话
      </button>
    </div>
    <p v-if="error" class="notice error" role="alert">{{ error }}</p>
    <div v-if="syncIssue" class="notice chat-sync-notice" role="status">
      <p>{{ syncIssue }}</p>
      <small v-if="lastSyncedAt"
        >上次成功同步：{{ formatTime(lastSyncedAt) }}。下方为当时记录。</small
      >
    </div>
    <div
      ref="transcript"
      class="chat-transcript"
      role="region"
      aria-label="对话记录"
      tabindex="0"
    >
      <div v-if="!turns.length" class="chat-welcome">
        <span class="chat-emblem" aria-hidden="true">报</span>
        <h2>从一条新闻开始聊</h2>
        <p>
          我可以解读已有简报，也可以按你的要求重新收集新闻。工具进度会在对话里展开。
        </p>
        <div class="chat-suggestions">
          <button
            @click="
              suggest(
                'chat',
                '请帮我解读最近一份简报，哪些消息最值得开发者关注？',
              )
            "
          >
            解读最近简报
          </button>
          <button
            @click="
              suggest(
                'generate',
                '重新整理一份 AI 新闻简报，重点关注开源模型与开发工具。',
              )
            "
          >
            重新生成简报
          </button>
          <button
            @click="
              suggest(
                'generate_send',
                '按我的订阅重新生成 AI 新闻简报，并推送到已验证邮箱。',
              )
            "
          >
            生成并推送
          </button>
        </div>
      </div>
      <article v-for="turn in turns" :key="turn.id" class="chat-turn">
        <div class="chat-user">
          <span class="chat-speaker">你</span>
          <p>{{ turn.question }}</p>
          <small v-if="turn.action !== 'chat'">{{
            turn.action === "generate_send" ? "重新生成并推送" : "仅重新生成"
          }}</small>
        </div>
        <div class="chat-assistant">
          <div class="chat-speaker">
            新闻助手 <time>{{ formatTime(turn.created_at) }}</time>
          </div>
          <details v-if="turn.tools.length" class="chat-tools" open>
            <summary>工具执行 · {{ turn.tools.length }} 次调用</summary>
            <ol>
              <li
                v-for="tool in turn.tools"
                :key="tool.id"
                :data-state="
                  syncIssue && tool.status === 'running' ? 'stale' : tool.status
                "
              >
                <span class="tool-dot" aria-hidden="true"></span
                ><strong>{{
                  toolNames[tool.tool_name] ?? tool.tool_name
                }}</strong>
                <span>{{ toolStatus(tool.status) }}</span><small
                  v-if="tool.duration_ms"
                  >{{ (tool.duration_ms / 1000).toFixed(1) }} 秒</small
                >
                <small v-if="tool.error_code" class="error-text">{{
                  tool.error_code
                }}</small>
              </li>
            </ol>
          </details>
          <p
            v-if="['queued', 'running'].includes(turn.status)"
            class="chat-progress"
            role="status"
          >
            {{
              syncIssue
                ? "进度暂未同步，正在等待恢复连接；这不代表后台任务失败。"
                : turn.status === "queued"
                  ? "任务已排队，等待助手处理…"
                  : "正在整理资料，工具进度会自动更新…"
            }}
          </p>
          <p v-if="turn.status === 'failed'" class="notice error">
            本轮未完成（{{
              turn.error_code || "未知错误"
            }}）。可查看工具记录后重新发起。
          </p>
          <p v-if="turn.answer" class="chat-answer">
            <template
              v-for="(part, index) in linkedText(turn.answer)"
              :key="index"
              ><a
                v-if="part.link"
                :href="part.value"
                target="_blank"
                rel="noopener noreferrer"
                >{{ part.value }}</a
              ><template v-else>{{ part.value }}</template></template
            >
          </p>
          <section v-if="turn.brief" class="chat-brief">
            <div class="chat-brief-heading">
              <span>新版本 {{ turn.brief.edition }}</span
              ><button @click="$emit('briefs')">查看简报历史</button>
            </div>
            <h2>{{ turn.brief.title }}</h2>
            <p>{{ turn.brief.overview }}</p>
            <article
              v-for="(item, index) in turn.brief.items"
              :key="index"
              class="chat-news-item"
            >
              <h3>{{ item.headline }}</h3>
              <p>{{ item.summary }}</p>
              <p class="muted">与你有关：{{ item.why_it_matters }}</p>
              <a
                v-for="source in item.sources"
                :key="source.url"
                :href="source.url"
                target="_blank"
                rel="noopener noreferrer"
                >{{ source.title }}
                <small
                  >（{{
                    source.evidence_level === "full_text"
                      ? "已读取原文"
                      : "仅摘要证据"
                  }}）</small
                ></a
              >
            </article>
            <p v-if="turn.brief.no_news" class="muted">
              本次来源与时间窗口内没有合适新闻，未编造条目。
            </p>
            <div v-if="turn.brief.delivery" class="chat-delivery" role="status">
              QQ 邮件：{{ statusLabel(turn.brief.delivery.status)
              }}<small v-if="turn.brief.delivery.status === 'accepted'"
                >服务器已接收，请查看收件箱。</small
              ><small v-if="turn.brief.delivery.status === 'unknown'"
                >请先核查邮箱，勿重复生成来重试投递。</small
              >
            </div>
            <p v-else-if="turn.action === 'generate_send'" class="notice error">
              未创建邮件投递，请核查收件邮箱是否在生成期间被修改或取消验证。
            </p>
          </section>
        </div>
      </article>
    </div>
    <form class="chat-composer" @submit.prevent="send">
      <label for="chat-message">发送给新闻助手</label>
      <textarea
        id="chat-message"
        v-model="message"
        maxlength="2000"
        rows="3"
        :disabled="sending || active || !!pending || !!syncIssue"
        placeholder="例如：重点关注开源模型，帮我重新整理一份简报…"
      ></textarea>
      <div class="chat-composer-actions">
        <label for="chat-action"
          >本次操作<select
            id="chat-action"
            v-model="action"
            aria-label="本次操作"
            :disabled="sending || active || !!pending || !!syncIssue"
          >
            <option value="chat">聊聊简报</option>
            <option value="generate">重新生成</option>
            <option
              value="generate_send"
              :disabled="
                !subscription?.recipient_verified || !status?.mail_configured
              "
            >
              生成并推送 QQ 邮箱
            </option>
          </select></label
        >
        <button
          v-if="pending && !sending"
          type="button"
          @click="pending = null"
        >
          编辑消息
        </button>
        <button
          type="submit"
          class="primary"
          :disabled="
            sending ||
            !!syncIssue ||
            active ||
            !status?.model_configured ||
            (!message.trim() && !pending) ||
            (action === 'generate_send' &&
              (!subscription?.recipient_verified || !status?.mail_configured))
          "
        >
          {{
            sending
              ? "正在提交…"
              : pending
                ? "重试同一请求"
                : action === "generate_send"
                  ? "生成并推送"
                  : action === "generate"
                    ? "生成新简报"
                    : "发送消息"
          }}
        </button>
      </div>
      <p class="chat-hint">
        {{
          action === "generate_send"
            ? "本次会收集新闻、保存新版本并实际发送一封邮件；不会开启每日订阅。"
            : action === "generate"
              ? "本次会调用模型和新闻工具，保存一个新版本，不发送邮件。"
              : "聊天只读取你的简报，不会自动发送邮件或更改订阅。"
        }}<button
          v-if="!subscription?.recipient_verified"
          type="button"
          class="text-button"
          @click="$emit('preferences')"
        >
          推送前请先验证收件邮箱
        </button>
      </p>
    </form>
  </section>
</template>
