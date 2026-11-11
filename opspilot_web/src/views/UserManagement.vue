<template>
  <section>
    <div class="page-intro">
      <div><p class="ops-eyebrow">平台管理 / ACCESS CONTROL</p><h1 class="ops-heading">用户管理</h1><p class="ops-muted">管理平台账号、角色及主机访问授权，查询权限变更记录。</p></div>
      <a-button type="primary" @click="openAccount()"><template #icon><plus-outlined/></template>新建用户</a-button>
    </div>
    <div class="roles">
      <article v-for="role in roles" :key="role.value" class="ops-panel role-card">
        <div class="role-icon"><component :is="role.icon"/></div>
        <div><strong>{{ role.label }}</strong><p>{{ role.description }}</p></div>
      </article>
    </div>

    <div class="ops-panel directory">
      <a-tabs v-model:active-key="tab" @change="onTab">
        <a-tab-pane key="accounts" tab="账号列表">
          <a-form layout="inline" class="filters" @finish="search">
            <a-form-item><a-input v-model:value="filters.search" allow-clear placeholder="搜索账号、姓名或邮箱" aria-label="搜索账号" :maxlength="100"/></a-form-item>
            <a-form-item><a-select v-model:value="filters.role" allow-clear placeholder="全部角色" aria-label="筛选角色" :options="roles" style="width:120px"/></a-form-item>
            <a-form-item><a-select v-model:value="filters.active" allow-clear placeholder="全部状态" aria-label="筛选状态" :options="states" style="width:120px"/></a-form-item>
            <a-form-item><a-button html-type="submit">查询</a-button></a-form-item>
          </a-form>
          <a-table :data-source="accounts" :columns="accountColumns" :loading="loading" row-key="id" :pagination="pagination"
            :scroll="{x:730}" @change="onPage">
            <template #bodyCell="{column,record}">
              <template v-if="column.key === 'name'">
                <div class="account-label"><span class="account-initial">{{ record.username.slice(0,1).toUpperCase() }}</span>
                  <div><strong>{{ record.username }}</strong><small>{{ record.first_name || record.email || '—' }}</small></div>
                </div>
              </template>
              <template v-else-if="column.key === 'role'"><a-tag :color="roleColor(record.role)">{{ roleName(record.role) }}</a-tag></template>
              <template v-else-if="column.key === 'status'"><a-badge :status="record.is_active ? 'success' : 'default'" :text="record.is_active ? '启用' : '停用'"/></template>
              <template v-else-if="column.key === 'login'">{{ formatTime(record.last_login, '从未登录') }}</template>
              <template v-else-if="column.key === 'actions'">
                <a-button type="link" size="small" @click="openAccount(record.id)">编辑与授权</a-button>
                <a-button v-if="record.id !== selfId" type="link" size="small" @click="openReset(record)">重置密码</a-button>
              </template>
            </template>
          </a-table>
        </a-tab-pane>
        <a-tab-pane key="audit" tab="变更审计">
          <a-table :data-source="events" :columns="auditColumns" :loading="loading" row-key="id" :pagination="pagination"
            :scroll="{x:670}" @change="onPage">
            <template #bodyCell="{column,record}">
              <template v-if="column.key === 'action'">{{ actionLabels[record.action] || record.action }}</template>
              <template v-else-if="column.key === 'changes'">
                <span class="change-line" v-for="(value,key) in record.changes" :key="key">{{ fieldLabels[key] || key }} · {{ formatChange(key, value) }}</span>
                <span v-if="!Object.keys(record.changes).length" class="ops-muted">—</span>
              </template>
              <template v-else-if="column.key === 'date'">{{ formatTime(record.created_at) }}</template>
            </template>
          </a-table>
        </a-tab-pane>
      </a-tabs>
    </div>

    <a-drawer v-model:open="drawer" :title="editingId ? '编辑用户与主机授权' : '新建用户'" :width="'min(540px, 100vw)'" @after-open-change="onDrawerChange">
      <a-spin :spinning="formLoading">
        <a-form :model="form" layout="vertical" @finish="save">
          <a-form-item label="用户名" name="username" :rules="[{required:true,message:'请输入用户名'}]"><a-input v-model:value="form.username" :disabled="Boolean(editingId)" :maxlength="150" autocomplete="off"/></a-form-item>
          <a-form-item label="显示名称" name="first_name"><a-input v-model:value="form.first_name" :maxlength="100"/></a-form-item>
          <a-form-item label="邮箱" name="email" :rules="[{type:'email',message:'请输入有效邮箱'}]"><a-input v-model:value="form.email" :maxlength="254"/></a-form-item>
          <a-form-item v-if="!editingId" label="初始密码" name="password" extra="至少 12 个字符，避免使用常见密码或与账号相似的密码。"
            :rules="[{required:true,min:12,message:'请输入至少 12 个字符'}]"><a-input-password v-model:value="form.password" :maxlength="128" autocomplete="new-password"/></a-form-item>
          <a-form-item label="平台角色" name="role"><a-select v-model:value="form.role" :options="roles" :disabled="editingId === selfId"/></a-form-item>
          <a-form-item label="启用账号" name="is_active"><a-switch v-model:checked="form.is_active" :disabled="editingId === selfId"/></a-form-item>
          <a-form-item v-if="form.role !== 'admin'" label="允许访问的主机" name="host_ids" extra="留空时账号无法访问任何主机。">
            <a-select v-model:value="form.host_ids" mode="multiple" placement="topLeft" :options="hostOptions" option-filter-prop="label" placeholder="按主机名称或地址搜索" show-search/>
          </a-form-item>
          <a-alert v-else message="管理员可访问全部资产和用户管理。" type="warning" show-icon/>
          <a-alert v-if="editingId" message="更改角色、主机授权或账号状态后，相关账号需重新登录。" type="info" show-icon class="edit-note"/>
          <div class="form-actions"><a-button @click="drawer=false">取消</a-button><a-button type="primary" html-type="submit" :loading="saving" :disabled="formLoading || !formReady">保存</a-button></div>
        </a-form>
      </a-spin>
    </a-drawer>

    <a-modal v-model:open="resetOpen" :title="'重置密码 · ' + resetTarget?.username" ok-text="重置密码" :confirm-loading="saving" @ok="resetPassword" @after-close="newPassword=''">
      <a-form layout="vertical">
        <a-form-item label="新密码" extra="至少 12 个字符。提交后，该用户的旧登录会话将失效。"><a-input-password v-model:value="newPassword" :maxlength="128" autocomplete="new-password"/></a-form-item>
      </a-form>
    </a-modal>
  </section>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useStore } from 'vuex'
import { message } from 'ant-design-vue'
import { EyeOutlined, PlusOutlined, SafetyCertificateOutlined, ToolOutlined } from '@ant-design/icons-vue'
import api from '@/api'

const store = useStore()
const selfId = computed(() => store.getters.userInfo?.id)
const roles = [
  { value:'admin', label:'管理员', icon:SafetyCertificateOutlined, description:'管理用户、资产授权和平台配置。' },
  { value:'operator', label:'运维员', icon:ToolOutlined, description:'对获授权的主机执行运维操作。' },
  { value:'observer', label:'观察员', icon:EyeOutlined, description:'查看授权范围内的资产和监控。' },
]
const states = [{ value:'true', label:'已启用' }, { value:'false', label:'已停用' }]
const accountColumns = [
  { title:'账号',key:'name',width:210 }, { title:'角色',key:'role',width:112 }, { title:'状态',key:'status',width:98 },
  { title:'最近登录',key:'login',width:166 }, { title:'操作',key:'actions',width:185 },
]
const auditColumns = [
  { title:'时间',key:'date',width:172 }, { title:'操作人',dataIndex:'actor',width:130 },
  { title:'目标账号',dataIndex:'target',width:130 }, { title:'操作',key:'action',width:110 }, { title:'变更内容',key:'changes' },
]
const actionLabels = { created:'创建用户', updated:'修改用户', password_changed:'更新密码' }
const fieldLabels = { role:'角色', is_active:'账号状态', active:'账号状态', host_ids:'主机授权', first_name:'显示名称', email:'邮箱' }
const tab = ref('accounts')
const filters = reactive({search:'', role:undefined, active:undefined})
const applied = ref({})
const accounts = ref([])
const events = ref([])
const loading = ref(false)
const page = ref(1)
const total = ref(0)
const pagination = computed(() => ({current:page.value,total:total.value,pageSize:20,showSizeChanger:false,showTotal:count=>'共 ' + count + ' 条'}))
let listRequest = 0

const drawer = ref(false)
const editingId = ref(null)
const formLoading = ref(false)
const formReady = ref(false)
const saving = ref(false)
const hostOptions = ref([])
const defaultForm = () => ({username:'',first_name:'',email:'',password:'',role:'operator',is_active:true,host_ids:[]})
const form = reactive(defaultForm())
const resetOpen = ref(false)
const resetTarget = ref(null)
const newPassword = ref('')
let formRequest = 0

function roleName(value) { return roles.find(role => role.value === value)?.label || value }
function roleColor(value) { return ({admin:'purple',operator:'blue',observer:'default'})[value] }
function formatTime(value, empty='—') {
  return value ? new Date(value).toLocaleString('zh-CN',{hour12:false}) : empty
}
function formatValue(key, value) {
  if (key === 'role') return roleName(value)
  if (key === 'active' || key === 'is_active') return value ? '启用' : '停用'
  if (Array.isArray(value)) return value.length ? value.map(id => '#' + id).join('、') : '无'
  return value || '—'
}
function formatChange(key, value) {
  return value && Object.hasOwn(value, 'from')
    ? formatValue(key,value.from) + ' → ' + formatValue(key,value.to)
    : formatValue(key,value)
}
async function load() {
  const request = ++listRequest
  const isAccounts = tab.value === 'accounts'
  loading.value = true
  try {
    const {data} = await api.get(isAccounts ? '/users/accounts/' : '/users/audit/', {
      params:{page:page.value, ...(isAccounts ? applied.value : {})},
    })
    if (request !== listRequest) return
    if (isAccounts) accounts.value = data.results
    else events.value = data.results
    total.value = data.count
  } catch {
    if (request !== listRequest) return
    if (isAccounts) accounts.value = []
    else events.value = []
    total.value = 0
  } finally {
    if (request === listRequest) loading.value = false
  }
}
function search() { applied.value = {...filters}; page.value = 1; load() }
function onTab() { page.value = 1; total.value = 0; load() }
function onPage(pagination) { page.value = pagination.current; load() }

async function openAccount(id=null) {
  const request = ++formRequest
  Object.assign(form, defaultForm())
  editingId.value = id
  drawer.value = true
  formReady.value = false
  formLoading.value = true
  try {
    const [hosts, result] = await Promise.all([api.get('/host/'), id ? api.get('/users/accounts/' + id + '/') : null])
    if (request !== formRequest || !drawer.value) return
    hostOptions.value = hosts.data.map(host => ({value:host.id,label:host.name + ' · ' + (host.ip_addr || '无连接地址')}))
    if (result) {
      const {username,first_name,email,role,is_active,host_ids} = result.data
      Object.assign(form, {username,first_name,email,role,is_active,host_ids})
    }
    formReady.value = true
  } catch {
    if (request === formRequest) drawer.value = false
  } finally {
    if (request === formRequest) formLoading.value = false
  }
}
function onDrawerChange(open) { if (!open) form.password = '' }

async function save() {
  if (!formReady.value || saving.value) return
  saving.value = true
  try {
    const {username,password,...values} = form
    if (values.role === 'admin') delete values.host_ids
    if (editingId.value) await api.patch('/users/accounts/' + editingId.value + '/', values)
    else await api.post('/users/accounts/', {...values,username,password})
    drawer.value = false
    message.success('账号已保存')
    load()
  } catch {
    // 统一接口提示。
  } finally {
    saving.value = false
  }
}
function openReset(record) { resetTarget.value = record; newPassword.value = ''; resetOpen.value = true }
async function resetPassword() {
  if (newPassword.value.length < 12) { message.warning('请输入至少 12 个字符'); return }
  saving.value = true
  try {
    await api.post('/users/accounts/' + resetTarget.value.id + '/password/', {password:newPassword.value})
    resetOpen.value = false
    message.success('密码已重置')
  } catch {
    // 统一接口提示。
  } finally {
    saving.value = false
  }
}
onMounted(load)
</script>

<style scoped>
.page-intro { display:flex; flex-wrap:wrap; justify-content:space-between; align-items:center; gap:12px; margin:3px 0 20px; }
.page-intro .ops-muted { margin:8px 0 0; }
.roles { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:13px; margin-bottom:19px; }
.role-card { display:flex; align-items:flex-start; gap:12px; padding:15px; }
.role-card strong { color:var(--text); }
.role-card p { margin:5px 0 0; color:var(--muted); font-size:12px; line-height:1.5; }
.role-icon,.account-initial { display:grid; flex:none; place-items:center; width:34px; height:34px; border-radius:9px; background:var(--accent-soft); color:var(--accent); font-weight:700; }
.directory { padding:0 16px 4px; }
.filters { display:flex; flex-wrap:wrap; row-gap:8px; margin-bottom:14px; }
.account-label { display:flex; align-items:center; gap:10px; min-width:0; }
.account-label strong,.account-label small { display:block; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.account-label>div { min-width:0; }
.account-label small { color:var(--muted); margin-top:3px; }
.change-line { display:block; overflow-wrap:anywhere; }
.edit-note { margin-top:14px; }
.form-actions { display:flex; gap:8px; justify-content:flex-end; margin-top:21px; }
@media(max-width:750px) { .roles { grid-template-columns:1fr; gap:8px; } .role-card { padding:12px; } }
</style>
