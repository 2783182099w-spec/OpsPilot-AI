<template>
  <div class="chat-page">
    <div class="chat-bar">
      <div class="chat-title">
        <robot-outlined/>
        <span>AI 助手</span>
        <a-tag color="blue">deepseek-flash</a-tag>
      </div>
      <a-button size="small" :disabled="!messages.length" @click="clear">清空对话</a-button>
    </div>

    <div ref="listRef" class="chat-list">
      <a-empty v-if="!messages.length" description="问我任何运维或编程相关的问题">
        <div class="examples">
          <a-tag
              v-for="example in examples"
              :key="example"
              class="example"
              @click="send(example)"
          >
            {{ example }}
          </a-tag>
        </div>
      </a-empty>

      <div
          v-for="(message, index) in messages"
          :key="index"
          class="message"
          :class="message.role === 'user' ? 'message-user' : 'message-ai'"
      >
        <div class="avatar">
          <user-outlined v-if="message.role === 'user'"/>
          <robot-outlined v-else/>
        </div>
        <div class="bubble" :class="{ 'bubble-error': message.error }">{{ message.content }}</div>
      </div>

      <div v-if="loading" class="message message-ai">
        <div class="avatar">
          <robot-outlined/>
        </div>
        <div class="bubble"><a-spin size="small"/> 正在思考...</div>
      </div>
    </div>

    <div class="chat-input">
      <a-textarea
          v-model:value="input"
          :auto-size="{ minRows: 2, maxRows: 5 }"
          placeholder="输入内容，Enter 发送，Shift + Enter 换行"
          :disabled="loading"
          @keydown="onKeydown"
      />
      <a-button type="primary" :loading="loading" :disabled="!input.trim()" @click="send()">
        发送
      </a-button>
    </div>
  </div>
</template>

<script setup>
import { nextTick, ref } from 'vue'
import { RobotOutlined, UserOutlined } from '@ant-design/icons-vue'

import api from '@/api'

const messages = ref([])
const input = ref('')
const loading = ref(false)
const listRef = ref(null)

const examples = [
  '查看磁盘占用的 Linux 命令',
  '解释一下 SSH 公钥认证的原理',
  '写一个 Nginx 日志排查脚本',
]

async function scrollToBottom() {
  await nextTick()
  const el = listRef.value
  if (el) {
    el.scrollTop = el.scrollHeight
  }
}

async function send(preset) {
  const text = (preset ?? input.value).trim()
  if (!text || loading.value) return

  // 发送前把已有对话作为上下文传给后端
  const history = messages.value
      .filter((message) => !message.error)
      .map((message) => ({ role: message.role, content: message.content }))

  messages.value.push({ role: 'user', content: text })
  input.value = ''
  loading.value = true
  await scrollToBottom()

  try {
    const { data } = await api.post('/ai/chat', { message: text, history }, { timeout: 120000 })
    messages.value.push({ role: 'assistant', content: data.reply })
  } catch (err) {
    const detail = err?.response?.data?.error || '请求失败，请稍后重试'
    messages.value.push({ role: 'assistant', content: detail, error: true })
  } finally {
    loading.value = false
    await scrollToBottom()
  }
}

function onKeydown(event) {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault()
    send()
  }
}

function clear() {
  messages.value = []
}
</script>

<style scoped>
.chat-page {
  display: flex;
  flex-direction: column;
  height: calc(100vh - 200px);
  min-height: 420px;
}

.chat-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-bottom: 12px;
  border-bottom: 1px solid #f0f0f0;
}

.chat-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 16px;
  font-weight: 500;
}

.chat-list {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 16px 4px;
}

.examples {
  display: flex;
  flex-direction: column;
  gap: 8px;
  align-items: center;
}

.example {
  cursor: pointer;
}

.message {
  display: flex;
  gap: 10px;
  margin-bottom: 16px;
}

.message-user {
  flex-direction: row-reverse;
}

.avatar {
  flex: none;
  width: 32px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  background: #f0f2f5;
  color: #2a5298;
}

.bubble {
  max-width: 76%;
  padding: 10px 14px;
  border-radius: 8px;
  background: #f5f5f5;
  white-space: pre-wrap;
  word-break: break-word;
  line-height: 1.6;
}

.message-user .bubble {
  background: #2a5298;
  color: #fff;
}

.bubble-error {
  background: #fff1f0;
  color: #cf1322;
}

.chat-input {
  display: flex;
  gap: 10px;
  align-items: flex-end;
  padding-top: 12px;
  border-top: 1px solid #f0f0f0;
}
</style>
