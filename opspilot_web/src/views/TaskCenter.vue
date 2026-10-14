<template>
  <div>
    <a-alert message="普通用户提交的任务需管理员审批。执行器每个任务最多并发 4 台主机；失败任务不自动重试。" type="info" show-icon />
    <a-button @click="load" style="margin: 12px 0">刷新任务历史</a-button>
    <a-table :data-source="tasks" row-key="id" :columns="columns" :loading="loading">
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'progress'">{{ record.done }} / {{ record.total }}</template>
        <template v-if="column.key === 'action'">
          <a-button @click="selected = record">查看结果</a-button>
          <a-button v-if="record.status === 'awaiting_approval'" @click="approve(record)">管理员审批</a-button>
        </template>
      </template>
    </a-table>
    <a-modal :open="!!selected" title="任务结果" width="900px" @cancel="selected = null" @ok="selected = null">
      <pre style="white-space: pre-wrap; max-height: 60vh; overflow: auto">{{ selected?.command }}
主机 ID：{{ selected?.host_ids }}
{{ JSON.stringify(selected?.results, null, 2) }}</pre>
    </a-modal>
  </div>
</template>
<script setup>
import { ref, onMounted, onBeforeUnmount } from 'vue'
import api from '@/api'
import { Modal } from 'ant-design-vue'
const tasks = ref([]), selected = ref(null), loading = ref(false)
let timer
const columns = [{ title: 'ID', dataIndex: 'id' }, { title: '状态', dataIndex: 'status' }, { title: '进度', key: 'progress' }, { title: '提交时间', dataIndex: 'created_at' }, { title: '操作', key: 'action' }]
async function load() {
  if (loading.value) return
  loading.value = true
  try { tasks.value = (await api.get('/mtask/tasks')).data } finally { loading.value = false }
}
async function approve(task) { Modal.confirm({ title: '审批执行命令', content: `目标主机 ID：${task.host_ids.join(', ')}；命令：${task.command}`, onOk: async () => { await api.post(`/mtask/tasks/${task.id}`); await load() } }) }
onMounted(() => { load(); timer = setInterval(load, 5000) })
onBeforeUnmount(() => clearInterval(timer))
</script>
