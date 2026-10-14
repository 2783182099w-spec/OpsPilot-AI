<template>
  <div class="chat-page">
    <div class="chat-bar">
      <div class="chat-title">
        <a-select v-model:value="mode" :disabled="loading" style="width: 150px" :options="[{value:'chat',label:'运维问答'},{value:'logs',label:'日志分析'},{value:'diagnosis',label:'故障诊断'}]" />
        <robot-outlined/>
        <span>AI 助手</span>
        <a-tag color="blue">deepseek-flash</a-tag>
      </div>
      <a-button size="small" :disabled="!messages.length" @click="clear">清空对话</a-button>
      <a-button size="small" @click="loadHistory">调用记录</a-button>
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
      <input type="file" accept=".log,.txt" @change="loadLog" />
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
    <a-modal v-model:open="historyOpen" title="AI 调用记录（最近 50 次）" width="800px" @ok="historyOpen = false">
      <a-table :data-source="callHistory" row-key="id" :columns="[{title:'模型',dataIndex:'model'},{title:'模式',dataIndex:'mode'},{title:'输入 Token',dataIndex:'input_tokens'},{title:'输出 Token',dataIndex:'output_tokens'},{title:'成功',dataIndex:'success'},{title:'时间',dataIndex:'created_at'}]" />
    </a-modal>
  </div>
</template>

<script setup>
import { nextTick, ref } from 'vue'
import { RobotOutlined, UserOutlined } from '@ant-design/icons-vue'

import api from '@/api'

const messages = ref([])
const mode = ref('chat')
const historyOpen = ref(false), callHistory = ref([])
async function loadHistory() { callHistory.value = (await api.get('/ai/history')).data; historyOpen.value = true }
async function loadLog(event) {
  const file = event.target.files?.[0]
  if (!file) return
  if (file.size > 20000) { window.alert('日志文件不能超过 20KB，请截取相关片段并脱敏'); return }
  input.value = await file.text()
  mode.value = 'logs'
}
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
    const { data } = await api.post('/ai/chat', { message: text, history, mode: mode.value }, { timeout: 120000 })
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
