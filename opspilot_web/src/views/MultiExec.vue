<template>
  <div>
    <h4>已选主机</h4>
    <a-empty v-if="!selectedHosts.length" description="还没有选择主机，请先点击「选择主机」"/>
    <div v-else class="tags">
      <a-tag
          v-for="(host, index) in selectedHosts"
          :key="host.id"
          closable
          color="blue"
          @close="removeHost(index)"
      >
        {{ host.name }}（{{ host.ip_addr }}:{{ host.port }}）
      </a-tag>
    </div>

    <div class="toolbar">
      <a-button type="primary" @click="openHostModal">
        <template #icon>
          <plus-outlined/>
        </template>
        选择主机
      </a-button>
      <a-button @click="openTemplateModal">
        <template #icon>
          <file-text-outlined/>
        </template>
        从模板导入
      </a-button>
      <a-button :disabled="!content" @click="content = ''">清空命令</a-button>
    </div>

    <v-ace-editor
        v-model:value="content"
        lang="sh"
        theme="chrome"
        :options="{ fontSize: 14, showPrintMargin: false, useWorker: false }"
        style="height: 220px; border: 1px solid #d9d9d9; border-radius: 6px;"
    />

    <div class="toolbar">
      <a-button
          type="primary"
          :loading="executing"
          :disabled="!selectedHosts.length || !content"
          @click="execute"
      >
        <template #icon>
          <thunderbolt-outlined/>
        </template>
        开始执行
      </a-button>
    </div>

    <a-table
        v-if="results.length"
        :data-source="results"
        :columns="resultColumns"
        row-key="key"
        size="middle"
    >
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'status'">
          <a-tag :color="record.res_code === 0 ? 'green' : 'red'">
            {{ record.res_code === 0 ? '成功' : `退出码 ${record.res_code}` }}
          </a-tag>
        </template>
        <template v-else-if="column.key === 'output'">
          <pre class="output">{{ record.res_data || '(无输出)' }}</pre>
        </template>
      </template>
    </a-table>

    <a-modal v-model:open="hostModalOpen" title="选择主机" width="900px" @ok="confirmHosts">
      <div class="toolbar">
        <a-select
            v-model:value="categoryFilter"
            style="width: 200px"
            placeholder="按类别筛选"
            allow-clear
            :options="categoryOptions"
            @change="loadHosts"
        />
        <a-button @click="loadHosts()">
          <template #icon>
            <reload-outlined/>
          </template>
          刷新
        </a-button>
      </div>
      <a-table
          :data-source="hosts"
          :columns="hostColumns"
          :loading="hostLoading"
          :row-selection="{ selectedRowKeys, onChange: onSelectChange }"
          row-key="id"
          size="small"
      />
    </a-modal>

    <a-modal v-model:open="templateModalOpen" title="选择指令模板" width="800px" @ok="confirmTemplate">
      <a-table
          :data-source="templates"
          :columns="templateColumns"
          :row-selection="{ selectedRowKeys: templateSelectedKeys, onChange: onTemplateSelectChange, type: 'radio' }"
          row-key="id"
          size="small"
      />
    </a-modal>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { message, Modal } from 'ant-design-vue'
import { FileTextOutlined, PlusOutlined, ReloadOutlined, ThunderboltOutlined } from '@ant-design/icons-vue'
import { VAceEditor } from 'vue3-ace-editor'
import 'ace-builds/src-noconflict/mode-sh'
import 'ace-builds/src-noconflict/theme-chrome'

import api from '@/api'

const content = ref('')
const executing = ref(false)
const results = ref([])

const hosts = ref([])
const categories = ref([])
const templates = ref([])
const hostLoading = ref(false)

const hostModalOpen = ref(false)
const templateModalOpen = ref(false)
const categoryFilter = ref(undefined)
const selectedRowKeys = ref([])
const templateSelectedKeys = ref([])

const selectedHosts = ref([])

const hostColumns = [
  { title: '类别', dataIndex: 'category_name', key: 'category_name' },
  { title: '主机名称', dataIndex: 'name', key: 'name' },
  { title: '连接地址', dataIndex: 'ip_addr', key: 'ip_addr' },
  { title: '端口', dataIndex: 'port', key: 'port' },
]

const templateColumns = [
  { title: '模板名称', dataIndex: 'name', key: 'name' },
  { title: '模板类型', dataIndex: 'category_name', key: 'category_name' },
  { title: '模板内容', dataIndex: 'cmd', key: 'cmd' },
]

const resultColumns = [
  { title: '主机', dataIndex: 'host_name', key: 'host_name' },
  { title: '连接地址', dataIndex: 'ip_addr', key: 'ip_addr' },
  { title: '状态', key: 'status', width: 140 },
  { title: '输出', key: 'output' },
]

const categoryOptions = computed(() =>
  categories.value.map((item) => ({ label: item.name, value: item.id }))
)

function onSelectChange(keys) {
  selectedRowKeys.value = keys
}

function onTemplateSelectChange(keys) {
  templateSelectedKeys.value = keys
}

function openHostModal() {
  selectedRowKeys.value = selectedHosts.value.map((host) => host.id)
  hostModalOpen.value = true
  loadHosts()
}

function confirmHosts() {
  selectedHosts.value = hosts.value.filter((host) => selectedRowKeys.value.includes(host.id))
  hostModalOpen.value = false
}

function removeHost(index) {
  selectedHosts.value.splice(index, 1)
}

function openTemplateModal() {
  templateSelectedKeys.value = []
  templateModalOpen.value = true
}

function confirmTemplate() {
  const id = templateSelectedKeys.value[0]
  const template = templates.value.find((item) => item.id === id)
  if (!template) {
    message.warning('请先选择一个模板')
    return
  }
  content.value = template.cmd
  templateModalOpen.value = false
}

async function loadHosts() {
  hostLoading.value = true
  try {
    const params = categoryFilter.value ? { category: categoryFilter.value } : {}
    const { data } = await api.get('/host/', { params })
    hosts.value = data
  } finally {
    hostLoading.value = false
  }
}

async function loadMeta() {
  const [categoryRes, templateRes] = await Promise.all([
    api.get('/host/category'),
    api.get('/mtask/templates'),
  ])
  categories.value = categoryRes.data
  templates.value = templateRes.data
}

async function execute() {
  Modal.confirm({ title: '确认执行任务', content: `将向 ${selectedHosts.value.length} 台主机提交命令。请确认命令和目标环境。`, onOk: submitTask })
}

async function submitTask() {
  executing.value = true
  try {
    const { data } = await api.post('/mtask/tasks', {
      host_ids: selectedHosts.value.map((host) => host.id),
      cmd: content.value,
      confirmed: true,
    })
    message.success(`任务 ${data.id} 已提交，请在任务中心查看进度或审批状态`)
  } catch (e) {
    // 错误提示由 api 拦截器统一处理
  } finally {
    executing.value = false
  }
}

onMounted(() => {
  loadHosts()
  loadMeta()
})
</script>

<style scoped>
.toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: 12px 0;
}

.tags {
  margin-bottom: 8px;
}

.output {
  margin: 0;
  max-height: 240px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-all;
}
</style>
