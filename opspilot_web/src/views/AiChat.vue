<template>
  <section class="ai-page">
    <div class="page-intro">
      <div><p class="ops-eyebrow">平台管理 / AI ASSISTANT</p><h1 class="ops-heading">AI 助手</h1><p class="ops-muted">根据日志和报错梳理证据、可能原因与排查步骤。</p></div>
      <a-space wrap>
        <a-button @click="loadHistory"><template #icon><history-outlined/></template>调用记录</a-button>
        <a-button type="primary" :disabled="loading" @click="openConfig"><template #icon><setting-outlined/></template>模型设置</a-button>
      </a-space>
    </div>
    <div class="chat ops-panel">
      <header class="chat-header">
        <a-select v-model:value="mode" :options="modes" style="width:128px" :disabled="loading" aria-label="分析模式"/>
        <div class="model-label"><a-badge :status="personal ? 'processing' : defaultConfig.configured ? 'success' : 'default'"/>
          <strong>{{ personal?.model || defaultConfig.model || '平台默认' }}</strong><span>{{ personal ? '个人模型' : '平台默认' }}</span>
        </div>
        <a-button type="text" :disabled="loading || !messages.length" @click="messages=[]"><delete-outlined/> 清空对话</a-button>
      </header>
      <div ref="list" class="messages" role="log" aria-label="对话记录">
        <div v-if="!messages.length" class="start">
          <span class="assistant-icon"><robot-outlined/></span>
          <h2>今天要排查什么问题？</h2>
          <p class="ops-muted">描述故障现象，或导入已脱敏的日志片段。</p>
          <div class="prompts">
            <button v-for="example in examples" :key="example.mode" type="button" @click="startExample(example)"><strong>{{ example.title }}</strong><span>{{ example.description }}</span></button>
          </div>
        </div>
        <div v-for="(entry,index) in messages" :key="index" class="entry" :class="entry.role">
          <span class="entry-icon"><user-outlined v-if="entry.role === 'user'"/><robot-outlined v-else/></span>
          <div class="bubble" :class="{ failed:entry.error }">{{ entry.content }}</div>
        </div>
        <div v-if="loading" class="entry"><span class="entry-icon"><robot-outlined/></span><div class="bubble"><a-spin size="small"/> 正在分析</div></div>
      </div>
      <div class="composer">
        <a-textarea v-model:value="input" :auto-size="{minRows:3,maxRows:8}" :maxlength="20000" :disabled="loading"
          placeholder="输入报错、日志或排查问题" aria-label="输入问题或日志" @keydown="onKeydown"/>
        <div class="composer-actions">
          <input ref="fileInput" type="file" accept=".log,.txt" hidden @change="loadLog"/>
          <a-button type="text" :disabled="loading" @click="fileInput.click()"><paper-clip-outlined/> 导入日志</a-button>
          <small class="ops-muted hint">上传前请移除凭据与敏感数据</small>
          <a-button type="primary" :loading="loading" :disabled="!input.trim()" @click="send">发送 <send-outlined/></a-button>
        </div>
      </div>
    </div>
    <p class="disclaimer ops-muted">AI 提供排查建议，涉及变更的操作由运维人员核对和执行。</p>

    <a-drawer v-model:open="configOpen" title="模型设置" :width="'min(490px, 100vw)'" @after-open-change="onConfigOpen">
      <a-form layout="vertical">
        <a-form-item label="调用方式">
          <a-radio-group v-model:value="usePersonal">
            <a-radio :value="false">平台默认</a-radio><a-radio :value="true">使用个人 API Key</a-radio>
          </a-radio-group>
        </a-form-item>
        <template v-if="usePersonal">
          <a-form-item label="服务商"><a-select v-model:value="draft.provider" :options="providerOptions" @change="clearModels"/></a-form-item>
          <a-form-item label="API Key">
            <a-input-password v-model:value="draft.api_key" autocomplete="off" :maxlength="512" placeholder="输入该服务商签发的 API Key" @change="invalidateModelList"/>
          </a-form-item>
          <a-form-item label="模型 ID" extra="查询后选择可用模型，或手动输入支持 Chat Completions 的模型 ID。">
            <div class="model-picker">
              <a-auto-complete v-model:value="draft.model" :options="modelOptions" :filter-option="filterModel" placeholder="选择或输入模型" aria-label="模型 ID"/>
              <a-button :loading="loadingModels" :disabled="!draft.api_key.trim() || !draft.provider" @click="loadModels">查询模型</a-button>
            </div>
          </a-form-item>
          <a-alert type="info" show-icon message="个人 Key 仅保留在当前页面内存。离开页面、刷新或切回平台默认后会清除。"/>
        </template>
        <a-alert v-else :type="defaultConfig.configured ? 'info' : 'warning'" show-icon
          :message="defaultConfig.configured ? '默认模型 · ' + defaultConfig.model : '平台默认模型暂不可用，请联系管理员或配置个人 Key。'"/>
        <div class="form-actions"><a-button @click="configOpen=false">取消</a-button><a-button type="primary" :disabled="loadingModels" @click="applyConfig">应用设置</a-button></div>
      </a-form>
    </a-drawer>
    <a-modal v-model:open="historyOpen" title="最近 50 次调用" width="min(840px, 95vw)" :footer="null">
      <a-table :data-source="callHistory" row-key="id" :columns="columns" :loading="historyLoading" :scroll="{x:700}">
        <template #bodyCell="{column,record}">
          <template v-if="column.key === 'provider'">{{ providerOptions.find(option=>option.value===record.provider)?.label || '平台默认' }}</template>
          <template v-else-if="column.key === 'mode'">{{ modes.find(item=>item.value===record.mode)?.label }}</template>
          <template v-else-if="column.key === 'status'"><a-tag :color="record.success ? 'green' : 'red'">{{ record.success ? '成功' : '失败' }}</a-tag></template>
          <template v-else-if="column.key === 'date'">{{ new Date(record.created_at).toLocaleString('zh-CN',{hour12:false}) }}</template>
        </template>
      </a-table>
    </a-modal>
  </section>
</template>

<script setup>
import { computed, nextTick, onMounted, onUnmounted, reactive, ref } from 'vue'
import { DeleteOutlined, HistoryOutlined, PaperClipOutlined, RobotOutlined, SendOutlined, SettingOutlined, UserOutlined } from '@ant-design/icons-vue'
import { message } from 'ant-design-vue'
import api, { extractError } from '@/api'

const messages = ref([])
const input = ref('')
const loading = ref(false)
const list = ref(null)
const fileInput = ref(null)
const mode = ref('chat')
const defaultConfig = ref({model:'',configured:false})
const providerOptions = ref([])
const personal = ref(null)
const configOpen = ref(false)
const usePersonal = ref(false)
const draft = reactive({provider:'',api_key:'',model:''})
const modelOptions = ref([])
const loadingModels = ref(false)
const historyOpen = ref(false)
const historyLoading = ref(false)
const callHistory = ref([])
let modelRequest = 0
let chatRequest
const modes = [
  {value:'chat',label:'运维问答'}, {value:'logs',label:'日志分析'}, {value:'diagnosis',label:'故障诊断'},
]
const examples = [
  {mode:'logs',title:'分析报错日志',description:'提取异常、时间点和证据',prompt:'请分析以下日志中的异常、可能原因与证据：\n'},
  {mode:'diagnosis',title:'辅助故障诊断',description:'梳理优先排查步骤',prompt:'请按优先级给出以下故障的排查步骤：\n'},
  {mode:'chat',title:'运维问答',description:'查询命令用途或排查方法',prompt:''},
]
const columns = [
  {title:'来源',key:'provider'}, {title:'模型',dataIndex:'model'}, {title:'模式',key:'mode'},
  {title:'输入 Token',dataIndex:'input_tokens'}, {title:'输出 Token',dataIndex:'output_tokens'}, {title:'结果',key:'status'}, {title:'时间',key:'date'},
]
const personalProvider = computed(() => providerOptions.value[0]?.value || '')
onMounted(async () => {
  try {
    const {data} = await api.get('/ai/config')
    defaultConfig.value = data.default
    providerOptions.value = data.providers.map(({id,label}) => ({value:id,label}))
  } catch {
    // 统一接口提示。
  }
})
onUnmounted(() => {
  chatRequest?.abort()
  personal.value = null
  draft.api_key = ''
  invalidateModelList()
})
function invalidateModelList() {
  modelRequest++
  loadingModels.value = false
  modelOptions.value = []
}
function clearModels() { draft.model = ''; invalidateModelList() }
function openConfig() {
  usePersonal.value = !!personal.value
  Object.assign(draft, personal.value || {provider:personalProvider.value,api_key:'',model:''})
  configOpen.value = true
}
function onConfigOpen(open) {
  if (!open) {
    draft.api_key = ''
    clearModels()
  }
}
function filterModel(query, option) { return option.value.toLowerCase().includes(query.toLowerCase()) }
async function loadModels() {
  const request = ++modelRequest
  loadingModels.value = true
  try {
    const {data} = await api.post('/ai/models',{provider:draft.provider,api_key:draft.api_key.trim()},{timeout:25000})
    if (request === modelRequest) {
      modelOptions.value = data.models.map(value => ({value}))
      message.info('已获取 ' + data.models.length + ' 个模型 ID')
    }
  } catch {
    // 统一接口提示。
  } finally {
    if (request === modelRequest) loadingModels.value = false
  }
}
function applyConfig() {
  if (usePersonal.value) {
    const model = draft.model.trim()
    const key = draft.api_key.trim()
    if (!draft.provider || !/^[!-~]{8,512}$/.test(key) || !/^[A-Za-z0-9][A-Za-z0-9._:/-]{0,99}$/.test(model) || model.includes(key)) {
      message.warning('请选择服务商，并填写有效的 API Key 与模型 ID')
      return
    }
    personal.value = {provider:draft.provider,api_key:key,model}
  } else {
    personal.value = null
  }
  configOpen.value = false
}
async function loadHistory() {
  historyOpen.value = true
  historyLoading.value = true
  try { callHistory.value = (await api.get('/ai/history')).data }
  catch { callHistory.value = [] }
  finally { historyLoading.value = false }
}
async function loadLog(event) {
  const file = event.target.files?.[0]
  event.target.value = ''
  if (!file) return
  if (file.size > 20000) { message.warning('文件上限为 20 KB，请截取相关日志并脱敏'); return }
  try { input.value = await file.text(); mode.value = 'logs' }
  catch { message.error('无法读取日志文件') }
}
function startExample(example) { mode.value = example.mode; input.value = example.prompt }
function recentHistory() {
  const context = []
  let remaining = 40000
  for (let i=messages.value.length-2; i>=0 && context.length<20; i-=2) {
    const [question,answer] = messages.value.slice(i,i+2)
    if (answer.error) continue
    const size = question.content.length + answer.content.length
    if (size > remaining) break
    context.unshift(question,answer)
    remaining -= size
  }
  return context
}
async function scrollToBottom() {
  await nextTick()
  if (list.value) list.value.scrollTop = list.value.scrollHeight
}
async function send() {
  if (!input.value.trim() || loading.value) return
  const key = personal.value?.api_key
  const text = key ? input.value.trim().replaceAll(key,'[REDACTED]') : input.value.trim()
  const history = recentHistory()
  input.value = ''
  messages.value.push({role:'user',content:text})
  loading.value = true
  chatRequest = new AbortController()
  scrollToBottom()
  try {
    const {data} = await api.post('/ai/chat', {message:text,history,mode:mode.value,configuration:personal.value}, {timeout:75000,signal:chatRequest.signal})
    messages.value.push({role:'assistant',content:data.reply})
  } catch (err) {
    if (err.code !== 'ERR_CANCELED') messages.value.push({role:'assistant',content:extractError(err.response?.data),error:true})
  } finally {
    loading.value = false
    scrollToBottom()
  }
}
function onKeydown(event) {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing && event.keyCode !== 229) {
    event.preventDefault()
    send()
  }
}
</script>

<style scoped>
.ai-page { max-width:1220px; margin:auto; }
.page-intro { display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:12px; margin:3px 0 19px; }
.page-intro p:last-child { margin:7px 0 0; }
.chat { display:flex; flex-direction:column; min-height:460px; height:calc(100dvh - 205px); }
.chat-header { display:flex; align-items:center; flex-wrap:wrap; gap:9px; padding:12px 14px; border-bottom:1px solid var(--line); }
.model-label { display:flex; gap:7px; align-items:center; flex:1; min-width:0; }
.model-label strong { overflow:hidden; white-space:nowrap; text-overflow:ellipsis; }
.model-label>span:last-child { color:var(--muted); font-size:11px; white-space:nowrap; }
.messages { flex:1; min-height:0; overflow:auto; padding:18px; }
.start { max-width:700px; margin:clamp(8px,6vh,64px) auto 10px; text-align:center; }
.assistant-icon,.entry-icon { display:inline-grid; flex:none; place-items:center; width:34px; height:34px; border-radius:9px; background:var(--accent-soft); color:var(--accent); }
.assistant-icon { width:49px; height:49px; font-size:22px; }
.start h2 { margin:12px 0 6px; color:var(--text); font-size:21px; }
.prompts { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:9px; margin-top:22px; }
.prompts button { cursor:pointer; font:inherit; text-align:left; border:1px solid var(--line); border-radius:9px; background:var(--surface-alt); color:var(--text); padding:11px; }
.prompts button:hover { background:var(--surface-hover); }
.prompts span { display:block; margin-top:4px; font-size:12px; color:var(--muted); }
.entry { display:flex; align-items:flex-start; gap:9px; margin:0 0 15px; }
.entry.user { flex-direction:row-reverse; }
.bubble { max-width:min(90%,850px); border:1px solid var(--line); border-radius:10px; background:var(--surface-alt); color:var(--text); padding:9px 12px; line-height:1.68; white-space:pre-wrap; overflow-wrap:anywhere; }
.user .bubble { background:var(--accent-soft); }
.bubble.failed { color:var(--danger); }
.composer { padding:13px 14px 9px; border-top:1px solid var(--line); }
.composer-actions { display:flex; align-items:center; gap:8px; margin-top:7px; }
.composer-actions>button:last-child { margin-left:auto; }
.disclaimer { margin-top:9px; font-size:12px; }
.model-picker,.form-actions { display:flex; gap:8px; }
.model-picker>*:first-child { flex:1; min-width:0; }
.form-actions { justify-content:flex-end; margin-top:20px; }
@media(max-width:620px) { .prompts { grid-template-columns:1fr; margin-top:12px; } .hint,.model-label>span:last-child { display:none; } .chat { height:calc(100dvh - 234px); } }
</style>
