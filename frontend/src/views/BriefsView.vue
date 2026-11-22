<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from "vue";
import { request, errorMessage } from "../api/client";
import { formatTime, statusLabel } from "../types/api";
import type { Brief, BriefSummary } from "../types/api";

const summaries = ref<BriefSummary[]>([]);
const selected = ref<Brief | null>(null);
const loading = ref(true);
const error = ref("");
const more = ref(true);
const busy = ref(false);
const reason = ref("");
const confirmUnknown = ref(false);
let sequence = 0;
async function load(reset = false) {
  loading.value = true;
  try {
    const rows = await request<BriefSummary[]>(
      "/briefs?offset=" + (reset ? 0 : summaries.value.length),
    );
    summaries.value = reset ? rows : [...summaries.value, ...rows];
    more.value = rows.length === 20;
    if (!selected.value && summaries.value[0])
      await select(summaries.value[0].id);
    error.value = "";
  } catch (cause) {
    error.value = errorMessage(cause);
  } finally {
    loading.value = false;
  }
}
async function select(id: string) {
  const current = ++sequence;
  reason.value = "";
  confirmUnknown.value = false;
  try {
    const result = await request<Brief>("/briefs/" + id);
    if (current === sequence) selected.value = result;
  } catch (cause) {
    if (current === sequence) error.value = errorMessage(cause);
  }
}
async function retry() {
  const delivery = selected.value?.delivery;
  if (!delivery || !selected.value) return;
  busy.value = true;
  try {
    await request("/deliveries/" + delivery.id + "/retry", {
      method: "POST",
      body: JSON.stringify({
        confirm_unknown: confirmUnknown.value,
        reason: reason.value,
      }),
    });
    await select(selected.value.id);
  } catch (cause) {
    error.value = errorMessage(cause);
  } finally {
    busy.value = false;
  }
}
onMounted(() => load(true));
onBeforeUnmount(() => {
  sequence++;
});
</script>

<template>
  <section class="page">
    <header class="page-heading">
      <div>
        <h1>我的简报</h1>
        <p>每个重点，都有可以回看的出处。</p>
      </div>
      <button :disabled="loading" @click="load(true)">刷新</button>
    </header>
    <p v-if="error" class="notice error" role="alert">
      {{ error }}
    </p>
    <div v-if="!loading && !summaries.length" class="empty">
      <h2>你的第一份简报，等待开始。</h2>
      <p>先保存订阅偏好，再点击“立即整理”。完成后会保存在这里。</p>
    </div>
    <p v-if="loading && !summaries.length" class="muted">正在加载简报…</p>
    <div v-if="summaries.length" class="reading-layout">
      <aside class="brief-index" aria-label="历史简报">
        <button
          v-for="brief in summaries"
          :key="brief.id"
          :class="{ selected: selected?.id === brief.id }"
          @click="select(brief.id)"
        >
          <time>{{ brief.business_date }}</time><strong>{{
            brief.title
          }}</strong
          ><span v-if="brief.no_news">本期暂无可靠新闻</span>
        </button>
        <button v-if="more" :disabled="loading" @click="load()">
          加载更早的简报
        </button>
      </aside>
      <article v-if="selected" class="brief-paper">
        <header>
          <time class="muted">{{ selected.business_date }}</time>
          <h2>{{ selected.title }}</h2>
          <p class="overview">
            {{ selected.overview }}
          </p>
          <p class="muted small">
            新闻范围：{{ formatTime(selected.window_start) }} 至
            {{ formatTime(selected.window_end) }}
          </p>
        </header>
        <section
          v-for="(item, index) in selected.items"
          :key="index"
          class="news-item"
        >
          <h3>{{ item.headline }}</h3>
          <p>{{ item.summary }}</p>
          <p class="relevance">
            <strong>与你有关</strong>{{ item.why_it_matters }}
          </p>
          <ul class="sources">
            <li v-for="source in item.sources" :key="source.url">
              <a :href="source.url" target="_blank" rel="noopener noreferrer">{{
                source.title || source.source_name
              }}</a><span>{{
                source.evidence_level === "full_text"
                  ? "已获取原文"
                  : "仅摘要证据"
              }}</span>
            </li>
          </ul>
        </section>
        <footer class="delivery-box">
          <template v-if="selected.delivery">
            <strong
              >邮件状态：{{ statusLabel(selected.delivery.status) }}</strong
            >
            <p v-if="selected.delivery.status === 'accepted'" class="muted">
              邮件服务器已接受，不代表已经送达或阅读。
            </p>
            <p v-if="selected.delivery.last_error_code" class="muted">
              {{ selected.delivery.last_error_code }}
            </p>
            <template v-if="selected.delivery.status === 'unknown'">
              <p>请先核查邮箱。重新发送可能产生重复邮件。</p>
              <label class="checkbox"
                ><input
                  v-model="confirmUnknown"
                  type="checkbox"
                />我已核查，并确认重新发送</label
              ><label
                >核查与重试原因<textarea
                  v-model="reason"
                  minlength="10"
                  maxlength="500"
                ></textarea>
              </label>
            </template>
            <button
              v-if="['failed', 'unknown'].includes(selected.delivery.status)"
              :disabled="
                busy ||
                (selected.delivery.status === 'unknown' &&
                  (!confirmUnknown || reason.trim().length < 10))
              "
              @click="retry"
            >
              重新发送
            </button>
          </template>
          <p v-else class="muted">
            这份简报未创建邮件投递。开启已验证邮箱的每日推送后，后续简报将自动发送。
          </p>
        </footer>
      </article>
    </div>
  </section>
</template>
