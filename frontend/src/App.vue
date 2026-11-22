<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from "vue";
import { request, session, errorMessage } from "./api/client";
import type { ServiceStatus } from "./types/api";
import LoginView from "./views/LoginView.vue";
import SubscriptionView from "./views/SubscriptionView.vue";
import BriefsView from "./views/BriefsView.vue";
import TasksView from "./views/TasksView.vue";

type Tab = "briefs" | "subscription" | "tasks";
const loggedIn = ref(Boolean(session.get()));
const tab = ref<Tab>("briefs");
const busy = ref(false);
const error = ref("");
const status = ref<ServiceStatus | null>(null);
let idempotencyKey = "";
function signOut() {
  session.clear();
  loggedIn.value = false;
  status.value = null;
}
async function loadStatus() {
  try {
    status.value = await request<ServiceStatus>("/status");
  } catch (reason) {
    error.value = errorMessage(reason);
  }
}
function signedIn() {
  loggedIn.value = true;
  error.value = "";
  loadStatus();
}
async function generate() {
  busy.value = true;
  error.value = "";
  idempotencyKey ||= crypto.randomUUID();
  try {
    await request("/runs", {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey },
    });
    idempotencyKey = "";
    tab.value = "tasks";
  } catch (reason) {
    error.value = errorMessage(reason);
  } finally {
    busy.value = false;
  }
}
onMounted(() => {
  window.addEventListener("session-expired", signOut);
  if (loggedIn.value) loadStatus();
});
onBeforeUnmount(() => window.removeEventListener("session-expired", signOut));
</script>

<template>
  <LoginView v-if="!loggedIn" @signed-in="signedIn" />
  <div v-else class="app-layout">
    <aside class="sidebar">
      <a class="brand-mark" href="#" @click.prevent="tab = 'briefs'">AI 晨报</a>
      <p class="sidebar-caption">你的每日 AI 新闻助手</p>
      <nav aria-label="主导航">
        <button
          :aria-current="tab === 'briefs' ? 'page' : undefined"
          @click="tab = 'briefs'"
        >
          我的简报</button
        ><button
          :aria-current="tab === 'subscription' ? 'page' : undefined"
          @click="tab = 'subscription'"
        >
          订阅偏好</button
        ><button
          :aria-current="tab === 'tasks' ? 'page' : undefined"
          @click="tab = 'tasks'"
        >
          整理进度
        </button>
      </nav>
      <div class="sidebar-footer">
        <p>读新闻，也看出处。</p>
        <button @click="signOut">退出登录</button>
      </div>
    </aside>
    <main class="main-content">
      <div class="topbar">
        <span>让每天的阅读更有重点</span
        ><button
          class="primary"
          :disabled="busy || !status?.model_configured"
          @click="generate"
        >
          {{ busy ? "正在创建任务…" : "立即整理" }}
        </button>
      </div>
      <div
        v-if="status && (!status.model_configured || !status.mail_configured)"
        class="setup-notice"
      >
        <p v-if="!status.model_configured">
          模型尚未配置，暂时无法生成简报。请联系管理员完成配置。
        </p>
        <p v-if="!status.mail_configured">
          邮件通道尚未配置，简报生成后仍可在这里查看。
        </p>
      </div>
      <p v-if="error" class="notice error global-notice" role="alert">
        {{ error }}
      </p>
      <BriefsView v-if="tab === 'briefs'" /><SubscriptionView
        v-else-if="tab === 'subscription'"
        @saved="loadStatus"
      /><TasksView v-else />
    </main>
  </div>
</template>
