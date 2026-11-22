<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { request, errorMessage } from "../api/client";
import type { Subscription } from "../types/api";

const emit = defineEmits<{ saved: [] }>();
const data = ref<Subscription | null>(null);
const topics = ref("");
const included = ref("");
const excluded = ref("");
const verifiedEmail = ref("");
const busy = ref(false);
const loading = ref(true);
const error = ref("");
const saved = ref(false);
const verified = computed(
  () =>
    data.value?.recipient_verified &&
    data.value.recipient_email === verifiedEmail.value,
);
const words = (value: string) =>
  value
    .split(/[,，\n]/)
    .map((s) => s.trim())
    .filter(Boolean);
async function load() {
  loading.value = true;
  try {
    data.value = await request<Subscription>("/subscription");
    topics.value = data.value.topics.join("，");
    included.value = data.value.include_keywords.join("，");
    excluded.value = data.value.exclude_keywords.join("，");
    verifiedEmail.value = data.value.recipient_verified
      ? data.value.recipient_email
      : "";
    error.value = "";
  } catch (reason) {
    error.value = errorMessage(reason);
  } finally {
    loading.value = false;
  }
}
async function save() {
  if (!data.value) return;
  busy.value = true;
  error.value = "";
  saved.value = false;
  const {
    version,
    persona,
    recipient_email,
    timezone,
    local_send_time,
    max_items,
    lookback_hours,
    enabled,
  } = data.value;
  try {
    data.value = await request<Subscription>("/subscription", {
      method: "PUT",
      body: JSON.stringify({
        version,
        persona,
        recipient_email,
        timezone,
        local_send_time,
        max_items,
        lookback_hours,
        enabled: enabled && verified.value,
        topics: words(topics.value),
        include_keywords: words(included.value),
        exclude_keywords: words(excluded.value),
      }),
    });
    verifiedEmail.value = data.value.recipient_verified
      ? data.value.recipient_email
      : "";
    saved.value = true;
    emit("saved");
  } catch (reason) {
    error.value = errorMessage(reason);
  } finally {
    busy.value = false;
  }
}
onMounted(load);
</script>

<template>
  <section class="page">
    <header class="page-heading">
      <div>
        <h1>订阅偏好</h1>
        <p>告诉助手，你希望每天关注什么。</p>
      </div>
    </header>
    <p v-if="loading" class="muted">正在加载订阅…</p>
    <p v-if="error" role="alert" class="notice error">
      {{ error }} <button class="text-button" @click="load">重新加载</button>
    </p>
    <form
      v-if="data && !loading"
      class="subscription-form"
      @submit.prevent="save"
    >
      <fieldset>
        <legend>你的关注方向</legend>
        <label
          >身份与关注背景<input
            v-model="data.persona"
            maxlength="200"
            required
            placeholder="例如：关注开源模型的 Python 开发者"
        /></label>
        <label
          >感兴趣的话题<input
            v-model="topics"
            placeholder="大模型，AI 编程，研究论文"
          /><small>多个词语用逗号分隔，最多 10 个。</small></label
        >
        <div class="form-columns">
          <label
            >优先关注的关键词<textarea
              v-model="included"
              rows="3"
              placeholder="可选，多个词用逗号分隔"
            ></textarea>
          </label>
          <label
            >不想看到的关键词<textarea
              v-model="excluded"
              rows="3"
              placeholder="可选，排除词优先"
            ></textarea>
          </label>
        </div>
      </fieldset>
      <fieldset>
        <legend>简报与投递</legend>
        <div class="form-columns">
          <label
            >每份最多条数<input
              v-model.number="data.max_items"
              type="number"
              min="1"
              max="10"
              required
          /></label>
          <label
            >检索最近多少小时<input
              v-model.number="data.lookback_hours"
              type="number"
              min="1"
              max="72"
              required
          /></label>
          <label
            >每天开始整理的时间<input
              v-model="data.local_send_time"
              type="time"
              required
            /><small>完成整理后发送，不保证精确到达时间。</small></label
          >
          <label
            >时区<input
              v-model="data.timezone"
              required
              placeholder="Asia/Shanghai"
          /></label>
        </div>
        <label
          >收件邮箱<input
            v-model="data.recipient_email"
            type="email"
            required
          /><small>{{
            verified
              ? "收件邮箱已验证"
              : "需要管理员验证收件邮箱，才能开启每日推送。"
          }}</small></label
        >
        <label class="checkbox"
          ><input
            v-model="data.enabled"
            type="checkbox"
            :disabled="!verified"
          />每天自动整理并推送</label
        >
      </fieldset>
      <footer class="form-footer">
        <button class="primary" :disabled="busy">
          {{ busy ? "保存中…" : "保存订阅" }}</button><span
          v-if="saved"
          role="status"
          >订阅已保存</span
        >
      </footer>
    </form>
  </section>
</template>
