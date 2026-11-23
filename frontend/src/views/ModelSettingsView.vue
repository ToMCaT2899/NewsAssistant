<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { errorMessage, request } from "../api/client";
import type { ModelConfiguration } from "../types/api";

const emit = defineEmits<{ saved: [] }>();
const config = ref<ModelConfiguration | null>(null);
const baseUrl = ref("");
const modelName = ref("");
const apiKey = ref("");
const loading = ref(true);
const busy = ref(false);
const error = ref("");
const notice = ref("");
let disposed = false;
const dirty = computed(
  () =>
    baseUrl.value !== config.value?.base_url ||
    modelName.value !== config.value?.model_name ||
    !!apiKey.value,
);
function apply(data: ModelConfiguration) {
  config.value = data;
  baseUrl.value = data.base_url;
  modelName.value = data.model_name;
  apiKey.value = "";
}
async function load() {
  loading.value = true;
  error.value = "";
  try {
    const data = await request<ModelConfiguration>("/model-config");
    if (!disposed) apply(data);
  } catch (reason) {
    if (!disposed) error.value = errorMessage(reason);
  } finally {
    if (!disposed) loading.value = false;
  }
}
async function save() {
  if (!config.value || busy.value) return;
  busy.value = true;
  error.value = "";
  notice.value = "";
  try {
    const data = await request<ModelConfiguration>("/model-config", {
      method: "PUT",
      body: JSON.stringify({
        version: config.value.version,
        base_url: baseUrl.value,
        model_name: modelName.value,
        api_key: apiKey.value,
      }),
    });
    if (disposed) return;
    apply(data);
    notice.value = "模型配置已保存，API Key 已加密保存。";
    emit("saved");
  } catch (reason) {
    if (!disposed) error.value = errorMessage(reason);
  } finally {
    apiKey.value = "";
    busy.value = false;
  }
}
async function testConnection() {
  if (!config.value?.configured || dirty.value || busy.value) return;
  busy.value = true;
  error.value = "";
  notice.value = "";
  try {
    const result = await request<{ message: string }>("/model-config/test", {
      method: "POST",
    });
    if (disposed) return;
    await load();
    notice.value = result.message;
  } catch (reason) {
    if (!disposed) error.value = errorMessage(reason);
  } finally {
    busy.value = false;
  }
}
async function clear() {
  if (!config.value || busy.value) return;
  busy.value = true;
  error.value = "";
  notice.value = "";
  try {
    const data = await request<ModelConfiguration>(
      `/model-config?version=${config.value.version}`,
      { method: "DELETE" },
    );
    if (disposed) return;
    apply(data);
    notice.value = "模型配置已清除，聊天与生成已暂停。";
    emit("saved");
  } catch (reason) {
    if (!disposed) error.value = errorMessage(reason);
  } finally {
    busy.value = false;
  }
}
onMounted(load);
onBeforeUnmount(() => {
  disposed = true;
  apiKey.value = "";
});
</script>

<template>
  <section class="page model-settings">
    <header class="page-heading">
      <div>
        <h1>模型设置</h1>
        <p>连接你自己的模型，让助手开始工作。</p>
      </div>
      <button :disabled="loading || busy" @click="load">重新加载</button>
    </header>
    <p v-if="loading" role="status">正在加载设置…</p>
    <p v-if="error" class="notice error" role="alert">{{ error }}</p>
    <p v-if="notice" class="notice model-settings-notice" role="status">
      {{ notice }}
    </p>
    <template v-if="config">
      <div class="model-settings-intro">
        <strong>{{
          config.configured ? "已保存个人模型配置" : "尚未配置模型"
        }}</strong>
        <p>聊天与简报生成使用你保存的模型。未配置时仍可查看历史简报。</p>
      </div>
      <p v-if="!config.storage_ready" class="notice error">
        模型密钥存储尚未初始化，请联系管理员配置服务端加密密钥。
      </p>
      <form class="model-settings-form" @submit.prevent="save">
        <label for="model-base-url">API 地址</label>
        <input
          id="model-base-url"
          v-model="baseUrl"
          type="url"
          maxlength="500"
          required
          placeholder="https://api.example.com/v1"
          :disabled="busy || !config.storage_ready"
        />
        <small
          >填写公网 HTTPS API 根地址，包含实际 /v1 等前缀，不要填写
          /chat/completions。</small
        >
        <label for="model-name">模型名称</label>
        <input
          id="model-name"
          v-model="modelName"
          maxlength="120"
          required
          placeholder="填写服务商提供的模型 ID"
          :disabled="busy || !config.storage_ready"
        />
        <small
          >使用支持 Chat Completions 和工具调用（function
          calling）的模型。</small
        >
        <label for="model-api-key">API Key</label>
        <input
          id="model-api-key"
          v-model="apiKey"
          type="password"
          autocomplete="new-password"
          maxlength="4096"
          :required="!config.has_api_key"
          :placeholder="
            config.has_api_key
              ? '已保存密钥；留空保留，更换地址时须重新填写'
              : '输入你的 API Key'
          "
          :disabled="busy || !config.storage_ready"
        />
        <small
          >API Key
          加密保存，不回显，也不进入对话内容。请勿将密钥发送到聊天框。</small
        >
        <div class="model-settings-actions">
          <button
            type="submit"
            class="primary"
            :disabled="busy || !dirty || !config.storage_ready"
          >
            {{ busy ? "正在处理…" : "保存模型配置" }}</button><button
            type="button"
            :disabled="busy || !config.configured || dirty"
            @click="testConnection"
          >
            测试连接
          </button>
        </div>
        <p class="muted small">
          测试连接会真实调用一次模型，验证工具调用能力，可能产生少量用量。{{
            dirty ? "请先保存更改。" : ""
          }}
        </p>
      </form>
      <div class="model-settings-footer">
        <p v-if="config.tested_at">
          最近验证通过：{{ new Date(config.tested_at).toLocaleString("zh-CN") }}
        </p>
        <p v-else class="muted">
          {{
            config.configured
              ? "已保存，尚未验证连接。"
              : "保存配置后即可开始聊天或整理新闻。"
          }}
        </p>
        <button v-if="config.has_api_key" :disabled="busy" @click="clear">
          清除模型配置</button
        ><small v-if="config.has_api_key"
          >清除后需要重新配置，历史简报保留。</small
        >
      </div>
    </template>
  </section>
</template>
