<template>
  <div>
    <div class="toolbar">
      <a-button type="primary" @click="openHostModal">
        <template #icon>
          <plus-outlined/>
        </template>
        新建主机
      </a-button>
      <a-button @click="openCategoryModal">
        <template #icon>
          <folder-add-outlined/>
        </template>
        新建类别
      </a-button>
      <a-button @click="openImportModal">
        <template #icon>
          <upload-outlined/>
        </template>
        批量导入
      </a-button>
      <a-button @click="load">
        <template #icon>
          <reload-outlined/>
        </template>
        刷新
      </a-button>
    </div>

    <a-table :data-source="hosts" :columns="columns" :loading="loading" row-key="id" size="middle">
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'console'">
          <router-link :to="{ name: 'Console', params: { host_id: record.id } }">
            <a-button type="link" size="small">Console</a-button>
          </router-link>
        </template>
        <template v-else-if="column.key === 'action'">
          <a-button type="link" size="small" @click="openKeyModal(record)">同步密钥</a-button>
          <a-popconfirm
              title="确定要删除该主机吗？"
              ok-text="删除"
              cancel-text="取消"
              @confirm="removeHost(record)"
          >
            <a-button type="link" danger size="small">删除</a-button>
          </a-popconfirm>
        </template>
      </template>
    </a-table>

    <a-modal
        v-model:open="hostModalOpen"
        title="新建主机"
        width="720px"
        :confirm-loading="submitting"
        @ok="submitHost"
    >
      <a-alert
          v-if="!categories.length"
          type="warning"
          show-icon
          banner
          message="还没有主机类别，请先点击「新建类别」创建一个。"
          class="modal-alert"
      />
      <a-form
          ref="hostFormRef"
          :model="hostForm"
          :rules="hostRules"
          :label-col="{ span: 5 }"
          :wrapper-col="{ span: 18 }"
      >
        <a-form-item label="主机类别" name="category">
          <a-select
              v-model:value="hostForm.category"
              placeholder="请选择主机类别"
              :options="categoryOptions"
              allow-clear
          />
        </a-form-item>
        <a-form-item label="主机名称" name="name">
          <a-input v-model:value="hostForm.name" placeholder="例如 web-01"/>
        </a-form-item>
        <a-form-item label="登录用户" name="username">
          <a-input v-model:value="hostForm.username" placeholder="例如 root"/>
        </a-form-item>
        <a-form-item label="连接地址" name="ip_addr">
          <a-input v-model:value="hostForm.ip_addr" placeholder="例如 192.168.1.10"/>
        </a-form-item>
        <a-form-item label="端口" name="port">
          <a-input-number v-model:value="hostForm.port" :min="1" :max="65535" style="width: 160px"/>
        </a-form-item>
        <a-form-item label="连接密码" name="password">
          <a-input-password v-model:value="hostForm.password" placeholder="仅用于首次连接校验，不会保存"/>
        </a-form-item>
        <a-form-item label="备注" name="description">
          <a-textarea v-model:value="hostForm.description" :rows="3" placeholder="选填"/>
        </a-form-item>
      </a-form>
    </a-modal>

    <a-modal
        v-model:open="categoryModalOpen"
        title="新建主机类别"
        :confirm-loading="submitting"
        @ok="submitCategory"
    >
      <a-form
          ref="categoryFormRef"
          :model="categoryForm"
          :rules="categoryRules"
          :label-col="{ span: 5 }"
          :wrapper-col="{ span: 18 }"
      >
        <a-form-item label="类别名称" name="name">
          <a-input v-model:value="categoryForm.name" placeholder="例如 生产环境"/>
        </a-form-item>
      </a-form>
    </a-modal>

    <a-modal
        v-model:open="importModalOpen"
        title="批量导入主机"
        :confirm-loading="submitting"
        width="640px"
        @ok="submitImport"
    >
      <a-alert
          type="info"
          show-icon
          banner
          class="modal-alert"
          message="Excel 中填写的密码只用于首次连接校验，系统不会保存密码。"
          description="模板列顺序：类别名称、主机名称、连接地址、端口、登录用户、连接密码、备注。"
      />
      <a-upload
          :file-list="fileList"
          :max-count="1"
          :before-upload="beforeUpload"
          @remove="() => (fileList = [])"
      >
        <a-button>
          <template #icon>
            <upload-outlined/>
          </template>
          选择 Excel 文件
        </a-button>
      </a-upload>
      <div v-if="importErrors.length" class="import-errors">
        <p v-for="(item, index) in importErrors" :key="index">{{ item.error }}</p>
      </div>
    </a-modal>

    <a-modal
        v-model:open="keyModalOpen"
        title="同步全局公钥到主机"
        :confirm-loading="submitting"
        @ok="submitKey"
    >
      <a-alert
          type="info"
          show-icon
          banner
          class="modal-alert"
          message="系统会用该密码连接一次目标主机，把全局 SSH 公钥写入 ~/.ssh/authorized_keys。完成后 Console 与批量执行才能免密登录。"
      />
      <a-form
          ref="keyFormRef"
          :model="keyForm"
          :rules="keyRules"
          :label-col="{ span: 5 }"
          :wrapper-col="{ span: 18 }"
      >
        <a-form-item label="登录密码" name="password">
          <a-input-password v-model:value="keyForm.password" placeholder="请输入该主机的登录密码"/>
        </a-form-item>
      </a-form>
    </a-modal>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { message } from 'ant-design-vue'
import {
  FolderAddOutlined,
  PlusOutlined,
  ReloadOutlined,
  UploadOutlined,
} from '@ant-design/icons-vue'

import api from '@/api'

const loading = ref(false)
const submitting = ref(false)
const hosts = ref([])
const categories = ref([])

const hostModalOpen = ref(false)
const categoryModalOpen = ref(false)
const importModalOpen = ref(false)

const hostFormRef = ref(null)
const categoryFormRef = ref(null)

const hostForm = reactive({
  category: undefined,
  name: '',
  username: 'root',
  ip_addr: '',
  port: 22,
  password: '',
  description: '',
})

const categoryForm = reactive({ name: '' })

const hostRules = {
  category: [{ required: true, message: '请选择主机类别' }],
  name: [{ required: true, message: '请输入主机名称' }],
  username: [{ required: true, message: '请输入登录用户' }],
  ip_addr: [{ required: true, message: '请输入连接地址' }],
  port: [{ required: true, message: '请输入端口' }],
  password: [{ required: true, message: '请输入连接密码' }],
}

const categoryRules = {
  name: [{ required: true, message: '请输入类别名称' }],
}

const columns = [
  { title: '类别', dataIndex: 'category_name', key: 'category_name' },
  { title: '主机名称', dataIndex: 'name', key: 'name' },
  { title: '连接地址', dataIndex: 'ip_addr', key: 'ip_addr' },
  { title: '端口', dataIndex: 'port', key: 'port', width: 90 },
  { title: '备注', dataIndex: 'description', key: 'description' },
  { title: 'Console', key: 'console', width: 110 },
  { title: '操作', key: 'action', width: 180 },
]

const keyModalOpen = ref(false)
const keyFormRef = ref(null)
const keyForm = reactive({ password: '' })
const keyTarget = ref(null)

const keyRules = {
  password: [{ required: true, message: '请输入主机登录密码' }],
}

const categoryOptions = computed(() =>
  categories.value.map((item) => ({ label: item.name, value: item.id }))
)

const fileList = ref([])
const importErrors = ref([])

async function load() {
  loading.value = true
  try {
    const [hostRes, categoryRes] = await Promise.all([
      api.get('/host/'),
      api.get('/host/category'),
    ])
    hosts.value = hostRes.data
    categories.value = categoryRes.data
  } finally {
    loading.value = false
  }
}

function openHostModal() {
  Object.assign(hostForm, {
    category: undefined,
    name: '',
    username: 'root',
    ip_addr: '',
    port: 22,
    password: '',
    description: '',
  })
  hostModalOpen.value = true
}

function openCategoryModal() {
  categoryForm.name = ''
  categoryModalOpen.value = true
}

function openImportModal() {
  fileList.value = []
  importErrors.value = []
  importModalOpen.value = true
}

async function submitHost() {
  try {
    await hostFormRef.value.validate()
  } catch (e) {
    return
  }
  submitting.value = true
  try {
    const formData = new FormData()
    Object.keys(hostForm).forEach((key) => formData.append(key, hostForm[key]))
    const { data } = await api.post('/host/', formData)
    hosts.value = [data, ...hosts.value]
    hostModalOpen.value = false
    message.success('主机添加成功！')
  } catch (e) {
    // 错误提示由 api 拦截器统一处理
  } finally {
    submitting.value = false
  }
}

async function submitCategory() {
  try {
    await categoryFormRef.value.validate()
  } catch (e) {
    return
  }
  submitting.value = true
  try {
    const { data } = await api.post('/host/category', { name: categoryForm.name })
    categories.value = [data, ...categories.value]
    categoryModalOpen.value = false
    message.success('主机类别创建成功！')
  } catch (e) {
    // 错误提示由 api 拦截器统一处理
  } finally {
    submitting.value = false
  }
}

async function removeHost(record) {
  try {
    await api.delete(`/host/${record.id}`)
    hosts.value = hosts.value.filter((item) => item.id !== record.id)
    message.success('主机已删除')
  } catch (e) {
    // 错误提示由 api 拦截器统一处理
  }
}

function openKeyModal(record) {
  keyTarget.value = record
  keyForm.password = ''
  keyModalOpen.value = true
}

async function submitKey() {
  try {
    await keyFormRef.value.validate()
  } catch (e) {
    return
  }
  submitting.value = true
  try {
    await api.post(`/host/${keyTarget.value.id}/upload_key`, { password: keyForm.password })
    keyModalOpen.value = false
    message.success('公钥同步成功，现在可以打开 Console 了')
  } catch (e) {
    // 错误提示由 api 拦截器统一处理
  } finally {
    submitting.value = false
  }
}

function beforeUpload(file) {
  fileList.value = [file]
  return false
}

async function submitImport() {
  if (!fileList.value.length) {
    message.warning('请先选择要导入的 Excel 文件')
    return
  }
  submitting.value = true
  try {
    const formData = new FormData()
    fileList.value.forEach((file) => formData.append('host_excel', file))
    const { data } = await api.post('/host/excel_host', formData)
    importErrors.value = data.error || []
    importModalOpen.value = false
    await load()
    message.success(`批量导入完成，成功 ${(data.data || []).length} 台主机`)
  } catch (e) {
    // 错误提示由 api 拦截器统一处理
  } finally {
    submitting.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 16px;
}

.modal-alert {
  margin-bottom: 16px;
}

.import-errors {
  margin-top: 12px;
  color: #cf1322;
}
</style>
