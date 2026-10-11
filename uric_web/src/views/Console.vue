<template>
  <div class="console-page">
    <div class="console-bar">
      <div class="console-info">
        <cloud-server-outlined/>
        <template v-if="host">
          <strong>{{ host.name }}</strong>
          <a-tag>{{ host.username }}@{{ host.ip_addr }}:{{ host.port }}</a-tag>
        </template>
        <a-spin v-else size="small"/>
      </div>
      <div class="console-actions">
        <a-tag :color="statusColor">{{ statusText }}</a-tag>
        <a-button size="small" :disabled="connecting" @click="reconnect">重连</a-button>
        <a-button size="small" @click="goBack">返回资产</a-button>
      </div>
    </div>
    <div ref="terminalRef" class="terminal"></div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useStore } from 'vuex'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import { AttachAddon } from '@xterm/addon-attach'
import { CloudServerOutlined } from '@ant-design/icons-vue'
import '@xterm/xterm/css/xterm.css'

import api from '@/api'
import settings from '@/settings'

const route = useRoute()
const router = useRouter()
const store = useStore()

const hostId = route.params.host_id
const host = ref(null)
const status = ref('connecting')
const terminalRef = ref(null)

let term = null
let fitAddon = null
let socket = null
let attachAddon = null

const statusText = computed(() => ({
  connecting: '连接中',
  connected: '已连接',
  closed: '已断开',
  error: '连接失败',
}[status.value] || status.value))

const statusColor = computed(() => ({
  connecting: 'processing',
  connected: 'green',
  closed: 'default',
  error: 'red',
}[status.value] || 'default'))

const connecting = computed(() => status.value === 'connecting')

function buildWsUrl() {
  // 后端地址来自 settings.host，http -> ws，并带上 JWT 供后端鉴权
  const base = settings.host.replace(/^http/, 'ws')
  const token = encodeURIComponent(store.getters.token || '')
  return `${base}/ws/ssh/${hostId}/?token=${token}`
}

function write(text) {
  term?.write(text)
}

function cleanupSocket() {
  if (attachAddon) {
    attachAddon.dispose()
    attachAddon = null
  }
  if (socket) {
    socket.onopen = socket.onmessage = socket.onerror = socket.onclose = null
    if (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING) {
      socket.close()
    }
    socket = null
  }
}

function connect() {
  cleanupSocket()
  status.value = 'connecting'
  write('\r\n\x1b[33m正在连接主机...\x1b[0m\r\n')

  socket = new WebSocket(buildWsUrl())
  attachAddon = new AttachAddon(socket)
  term.loadAddon(attachAddon)

  socket.onopen = () => {
    status.value = 'connected'
    fitAddon.fit()
    term.focus()
    // 发一个换行，让远端 shell 立即输出一次提示符
    socket.send('\n')
  }

  socket.onerror = () => {
    status.value = 'error'
    write('\r\n\x1b[31mWebSocket 连接失败，请确认后端服务与登录状态。\x1b[0m\r\n')
  }

  socket.onclose = () => {
    if (status.value !== 'error') {
      status.value = 'closed'
    }
    write('\r\n\x1b[33m连接已关闭。\x1b[0m\r\n')
  }
}

function reconnect() {
  connect()
}

function goBack() {
  router.push({ name: 'Host' })
}

function onResize() {
  fitAddon?.fit()
}

async function loadHost() {
  try {
    const { data } = await api.get(`/host/${hostId}`)
    host.value = data
  } catch (e) {
    write('\x1b[31m无法读取主机信息。\x1b[0m\r\n')
  }
}

onMounted(async () => {
  term = new Terminal({
    convertEol: true,
    cursorBlink: true,
    scrollback: 2000,
    fontSize: 14,
    fontFamily: 'Consolas, "Courier New", monospace',
    theme: {
      foreground: '#d9d9d9',
      background: '#1e1e1e',
      cursor: '#ffffff',
    },
  })
  fitAddon = new FitAddon()
  term.loadAddon(fitAddon)
  term.open(terminalRef.value)
  fitAddon.fit()
  window.addEventListener('resize', onResize)

  await loadHost()
  connect()
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
  cleanupSocket()
  term?.dispose()
  term = null
})
</script>

<style scoped>
.console-page {
  display: flex;
  flex-direction: column;
  height: calc(100vh - 200px);
  min-height: 320px;
}

.console-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding-bottom: 12px;
}

.console-info {
  display: flex;
  align-items: center;
  gap: 8px;
}

.console-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.terminal {
  flex: 1;
  min-height: 0;
  padding: 6px;
  border-radius: 6px;
  background: #1e1e1e;
}
</style>
