<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from "vue";
import { request, errorMessage } from "../api/client";
import { formatTime, statusLabel } from "../types/api";
import type { Run, ToolCall } from "../types/api";

const runs = ref<Run[]>([]);
const tools = ref<ToolCall[]>([]);
const selected = ref("");
const error = ref("");
const loading = ref(true);
const more = ref(false);
let timer: ReturnType<typeof setTimeout> | undefined;
let alive = true;
let sequence = 0;
async function load(moreRows = false) {
  try {
    const rows = await request<Run[]>(
      "/runs?offset=" + (moreRows ? runs.value.length : 0),
    );
    if (!alive) return;
    runs.value = moreRows ? [...runs.value, ...rows] : rows;
    more.value = rows.length === 20;
    if (selected.value) await inspect(selected.value);
    error.value = "";
  } catch (cause) {
    if (alive) error.value = errorMessage(cause);
  } finally {
    if (alive) loading.value = false;
  }
}
async function inspect(id: string) {
  selected.value = id;
  const current = ++sequence;
  try {
    const result = await request<ToolCall[]>("/runs/" + id + "/tool-calls");
    if (alive && current === sequence) tools.value = result;
  } catch (cause) {
    if (alive && current === sequence) error.value = errorMessage(cause);
  }
}
async function poll() {
  await load();
  if (alive) timer = setTimeout(poll, 5000);
}
onMounted(poll);
onBeforeUnmount(() => {
  alive = false;
  sequence++;
  if (timer) clearTimeout(timer);
});
</script>

<template>
  <section class="page">
    <header class="page-heading">
      <div>
        <h1>整理进度</h1>
        <p>查看每次任务的状态与实际工具调用。</p>
      </div>
      <button @click="load()">刷新</button>
    </header>
    <p v-if="error" class="notice error" role="alert">
      {{ error }}
    </p>
    <p v-if="loading" class="muted">正在读取任务…</p>
    <div v-if="!loading && !runs.length" class="empty">
      <h2>还没有整理任务</h2>
      <p>点击“立即整理”，或开启订阅等待每日自动任务。</p>
    </div>
    <div v-if="runs.length" class="task-list">
      <button
        v-for="run in runs"
        :key="run.id"
        class="task-row"
        :class="{ selected: selected === run.id }"
        @click="inspect(run.id)"
      >
        <span
          ><strong>{{ run.business_date }} 的简报</strong
          ><small
            >{{ formatTime(run.created_at) }} ·
            {{
              run.trigger_type === "scheduled" ? "每日任务" : "手动任务"
            }}</small
          ><small v-if="run.error_code" class="error-text"
            >{{ run.error_code }}：{{ run.error_message }}</small
          ></span
        >
        <span class="status" :data-state="run.status">{{
          statusLabel(run.status)
        }}</span><span class="muted">{{ run.tool_calls }} 次工具调用</span>
      </button>
      <button v-if="more" @click="load(true)">加载更早的任务</button>
    </div>
    <section v-if="selected" class="tool-panel">
      <h2>执行记录</h2>
      <p v-if="!tools.length" class="muted">
        尚无工具记录，任务可能仍在等待或模型请求未完成。
      </p>
      <ol>
        <li v-for="tool in tools" :key="tool.id">
          <div>
            <strong>{{ tool.tool_name }}</strong><span class="muted"
              >{{ statusLabel(tool.status) }} · {{ tool.duration_ms }} ms</span
            >
          </div>
          <code>{{ JSON.stringify(tool.arguments) }}</code>
          <p v-if="tool.error_code" class="error-text">
            {{ tool.error_code }}
          </p>
        </li>
      </ol>
    </section>
  </section>
</template>
