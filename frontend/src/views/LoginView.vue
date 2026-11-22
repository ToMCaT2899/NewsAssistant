<script setup lang="ts">
import { ref } from "vue";
import { request, session, errorMessage } from "../api/client";

const emit = defineEmits<{ signedIn: [] }>();
const email = ref("");
const password = ref("");
const busy = ref(false);
const error = ref("");
async function signIn() {
  busy.value = true;
  error.value = "";
  try {
    const result = await request<{ access_token: string }>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ email: email.value, password: password.value }),
    });
    session.set(result.access_token);
    password.value = "";
    emit("signedIn");
  } catch (reason) {
    error.value = errorMessage(reason);
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <main class="login-layout">
    <section class="login-story">
      <div class="brand-mark">AI 晨报</div>
      <h1>每天，读到与你有关的 AI 新闻。</h1>
      <p>
        从真实来源中筛选重点，保留每条新闻的出处。按你的兴趣整理，送到你的邮箱。
      </p>
      <div class="story-note">
        由你决定关注什么。<br />让助手完成每天的整理。
      </div>
    </section>
    <section class="login-panel">
      <form class="login-form" @submit.prevent="signIn">
        <h2>登录你的晨报</h2>
        <p class="muted">使用管理员为你创建的账号。</p>
        <label
          >邮箱<input
            v-model="email"
            type="email"
            autocomplete="username"
            required
        /></label>
        <label
          >密码<input
            v-model="password"
            type="password"
            autocomplete="current-password"
            required
        /></label>
        <p v-if="error" role="alert" class="notice error">
          {{ error }}
        </p>
        <button class="primary" :disabled="busy">
          {{ busy ? "登录中…" : "登录" }}
        </button>
      </form>
    </section>
  </main>
</template>
