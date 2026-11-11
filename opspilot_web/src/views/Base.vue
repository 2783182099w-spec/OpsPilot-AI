<template>
  <div class="shell" :class="{ compact: collapsed }">
    <aside class="sidebar">
      <router-link class="brand" :to="{ name:'ShowCenter' }"><brand-mark/><span>OpsPilot<span class="brand-suffix"> AI</span><small>运维工作台</small></span></router-link>
      <nav class="navigation" aria-label="主导航">
        <section v-for="group in visibleGroups" :key="group.title">
          <h2>{{ group.title }}</h2>
          <router-link v-for="item in group.items" :key="item.name" :to="{ name:item.name }"
            :class="{ selected: activeName === item.name }" :aria-current="activeName === item.name ? 'page' : undefined" :title="item.title"
            @click="mobileOpen = false">
            <component :is="item.icon"/><span>{{ item.title }}</span>
          </router-link>
        </section>
      </nav>
      <div class="side-footer"><span class="side-dot"></span><span>OpsPilot 工作区</span></div>
    </aside>

    <a-drawer v-model:open="mobileOpen" placement="left" width="260" title="OpsPilot AI">
      <nav class="navigation mobile-nav" aria-label="移动端导航">
        <section v-for="group in visibleGroups" :key="group.title">
          <h2>{{ group.title }}</h2>
          <router-link v-for="item in group.items" :key="item.name" :to="{ name:item.name }"
            :class="{ selected: activeName === item.name }" @click="mobileOpen = false">
            <component :is="item.icon"/><span>{{ item.title }}</span>
          </router-link>
        </section>
      </nav>
    </a-drawer>

    <div class="workspace">
      <header class="topbar">
        <a-button class="desktop-menu" type="text" :aria-label="collapsed ? '展开导航' : '收起导航'" @click="collapsed = !collapsed">
          <template #icon><menu-unfold-outlined v-if="collapsed"/><menu-fold-outlined v-else/></template>
        </a-button>
        <a-button class="mobile-menu" type="text" aria-label="打开导航" @click="mobileOpen = true"><template #icon><menu-outlined/></template></a-button>
        <div class="breadcrumb"><span>{{ currentGroup }}</span><span class="slash">/</span><strong>{{ route.meta.title }}</strong></div>
        <theme-toggle/>
        <a-dropdown :trigger="['click']">
          <a-button type="text" class="identity" aria-label="账户菜单">
            <span class="initial">{{ username.slice(0, 1).toUpperCase() }}</span>
            <span class="identity-label"><strong>{{ username }}</strong><small>{{ roleLabel }}</small></span>
            <down-outlined class="identity-arrow"/>
          </a-button>
          <template #overlay>
            <a-menu>
              <a-menu-item key="password" @click="openPassword"><key-outlined/> 修改密码</a-menu-item>
              <a-menu-item key="logout" @click="logout"><logout-outlined/> 退出登录</a-menu-item>
            </a-menu>
          </template>
        </a-dropdown>
      </header>
      <main class="workspace-main">
        <div v-if="profileReady && forbidden" class="ops-panel denied">
          <a-result status="403" title="没有访问权限" sub-title="当前账号无法进入该模块">
            <template #extra><a-button type="primary" @click="router.push({name:'ShowCenter'})">返回运维总览</a-button></template>
          </a-result>
        </div>
        <router-view v-else-if="!restricted || profileReady"/>
        <a-spin v-else-if="!profileFailed" class="profile-wait"/>
        <a-result v-else status="warning" title="账号资料加载失败">
          <template #extra><a-button @click="loadProfile">重新加载</a-button></template>
        </a-result>
      </main>
    </div>
  </div>

  <a-modal v-model:open="passwordOpen" title="修改登录密码" ok-text="更新密码" :confirm-loading="savingPassword" @ok="savePassword">
    <p class="ops-muted">更新后，旧登录会话会失效。请使用新密码重新登录。</p>
    <a-form layout="vertical" :model="passwordForm">
      <a-form-item label="当前密码" name="current_password"><a-input-password v-model:value="passwordForm.current_password" autocomplete="current-password" :maxlength="128"/></a-form-item>
      <a-form-item label="新密码" name="password" extra="至少 12 个字符"><a-input-password v-model:value="passwordForm.password" autocomplete="new-password" :maxlength="128"/></a-form-item>
    </a-form>
  </a-modal>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useStore } from 'vuex'
import { message } from 'ant-design-vue'
import {
  AlertOutlined, AppstoreOutlined, AuditOutlined, BarChartOutlined, CloudServerOutlined,
  DashboardOutlined, DownOutlined, KeyOutlined, LogoutOutlined, MenuFoldOutlined, RocketOutlined,
  MenuOutlined, MenuUnfoldOutlined, RobotOutlined, TeamOutlined, UnorderedListOutlined,
} from '@ant-design/icons-vue'
import api from '@/api'
import BrandMark from '@/components/BrandMark.vue'
import ThemeToggle from '@/components/ThemeToggle.vue'

const route = useRoute()
const router = useRouter()
const store = useStore()
const collapsed = ref(false)
const mobileOpen = ref(false)
const profileReady = ref(false)
const profileFailed = ref(false)
const passwordOpen = ref(false)
const savingPassword = ref(false)
const passwordForm = reactive({ current_password:'', password:'' })
const username = computed(() => store.getters.userInfo?.username || '账户')
const role = computed(() => store.getters.userInfo?.role)
const roleLabel = computed(() => ({ admin:'管理员', operator:'运维员', observer:'观察员' })[role.value] || '账号')
const groups = [
  { title:'运行态势', items:[
    { name:'ShowCenter', title:'运维总览', icon:DashboardOutlined },
    { name:'Monitoring', title:'监控中心', icon:BarChartOutlined },
    { name:'Alerts', title:'告警中心', icon:AlertOutlined },
    { name:'DiagnosticTrees', title:'诊断与修复', icon:AppstoreOutlined },
  ] },
  { title:'资产与作业', items:[
    { name:'Host', title:'资产管理', icon:CloudServerOutlined },
    { name:'Operations', title:'健康巡检', icon:AuditOutlined },
    { name:'JobLibrary', title:'作业库', icon:AppstoreOutlined },
    { name:'Releases', title:'发布流水线', icon:RocketOutlined },
    { name:'TaskCenter', title:'任务中心', icon:UnorderedListOutlined },
  ] },
  { title:'平台管理', items:[
    { name:'AiChat', title:'AI 助手', icon:RobotOutlined, operator:true },
    { name:'UserManagement', title:'用户管理', icon:TeamOutlined, admin:true },
  ] },
]
const parents = { HostWorkbench:'Host', Console:'Host', MultiExec:'JobLibrary' }
const activeName = computed(() => parents[route.name] || route.name)
const currentGroup = computed(() => groups.find(group => group.items.some(item => item.name === activeName.value))?.title || '工作区')
const visibleGroups = computed(() => groups.map(group => ({
  ...group, items:group.items.filter(item => (!item.admin || role.value === 'admin') && (!item.operator || ['admin','operator'].includes(role.value))),
})).filter(group => group.items.length))
const restricted = computed(() => route.meta.admin || route.meta.operator)
const forbidden = computed(() => (route.meta.admin && role.value !== 'admin') || (route.meta.operator && role.value === 'observer'))

async function loadProfile() {
  profileFailed.value = false
  try {
    store.commit('setProfile', (await api.get('/users/me/')).data)
    profileReady.value = true
  } catch {
    profileFailed.value = true
  }
}
onMounted(loadProfile)

function logout() {
  store.commit('setToken', '')
  router.replace({ name:'Login' })
}
function openPassword() {
  Object.assign(passwordForm, {current_password:'',password:''})
  passwordOpen.value = true
}
async function savePassword() {
  if (!passwordForm.current_password || passwordForm.password.length < 12) {
    message.warning('请输入当前密码和至少 12 个字符的新密码')
    return
  }
  savingPassword.value = true
  try {
    await api.post('/users/me/password/', passwordForm)
    passwordOpen.value = false
    message.success('密码已更新，请重新登录')
    logout()
  } catch {
    // 统一接口提示。
  } finally {
    savingPassword.value = false
  }
}
</script>

<style scoped>
.shell { display:grid; grid-template-columns:228px minmax(0,1fr); min-height:100vh; }
.shell.compact { grid-template-columns:70px minmax(0,1fr); }
.sidebar { position:sticky; top:0; height:100dvh; display:flex; flex-direction:column; overflow:auto; background:var(--surface); border-right:1px solid var(--line); }
.brand { display:flex; align-items:center; gap:11px; flex:none; min-height:77px; padding:0 17px; font-weight:750; font-size:17px; color:var(--text); white-space:nowrap; }
.brand,.navigation a { text-decoration:none; }
.brand:hover,.navigation a:hover { text-decoration:none; }
.brand svg { flex:none; }
.brand small { display:block; color:var(--muted); font-weight:500; font-size:11px; margin-top:2px; }
.brand-suffix { color:var(--accent); }
.navigation { display:grid; gap:18px; padding:9px 11px 16px; }
.navigation h2 { margin:0 0 5px 11px; color:var(--muted); font-size:11px; font-weight:650; white-space:nowrap; }
.navigation a { display:flex; gap:11px; align-items:center; min-height:40px; margin:3px 0; padding:0 11px; border-radius:8px; color:var(--muted); white-space:nowrap; font-weight:550; }
.navigation a:hover { color:var(--text); background:var(--surface-hover); }
.navigation a.selected { color:var(--accent); background:var(--accent-soft); font-weight:700; }
.navigation a :deep(.anticon) { font-size:16px; }
.side-footer { display:flex; align-items:center; gap:9px; margin:auto 14px 0; padding:17px 6px; border-top:1px solid var(--line); color:var(--muted); font-size:11px; white-space:nowrap; }
.side-dot { width:7px;height:7px;border-radius:50%;background:var(--accent);flex:none; }
.compact .brand>span,.compact .sidebar h2,.compact .sidebar a>span:not(.anticon),.compact .side-footer>span:last-child { display:none; }
.compact .sidebar .navigation a,.compact .side-footer { justify-content:center; }
.topbar { position:sticky; top:0; z-index:9; display:flex; align-items:center; gap:10px; min-height:62px; padding:0 22px; background:var(--surface); border-bottom:1px solid var(--line); }
.topbar .desktop-menu,.topbar .mobile-menu { color:var(--muted); }
.breadcrumb { display:flex; gap:10px; align-items:center; flex:1; min-width:0; font-size:13px; color:var(--muted); }
.breadcrumb strong { color:var(--text); overflow:hidden; white-space:nowrap; text-overflow:ellipsis; }
.slash { opacity:.65; }
.identity { display:flex; gap:9px; align-items:center; height:44px; max-width:190px; padding:4px 8px; }
.initial { display:grid; place-items:center; width:30px;height:30px;border-radius:9px;background:var(--accent-soft);color:var(--accent);font-weight:750;flex:none; }
.identity-label { display:flex; flex-direction:column; align-items:flex-start; gap:2px; line-height:1.1; min-width:0; }
.identity-label strong { max-width:100%; overflow:hidden; text-overflow:ellipsis; color:var(--text); }
.identity-label small,.identity-arrow { font-size:11px; color:var(--muted); }
.workspace-main { min-width:0; width:100%; max-width:1800px; margin:auto; padding:clamp(12px,2vw,25px); }
.profile-wait { display:block; margin:35px auto; }
.topbar .mobile-menu { display:none; }
.mobile-nav { padding:0; }
@media(max-width:850px) {
  .shell,.shell.compact { display:block; }
  .sidebar,.topbar .desktop-menu { display:none; }
  .topbar .mobile-menu { display:inline-flex; align-items:center; justify-content:center; }
  .topbar { padding:0 12px; min-height:55px; gap:6px; }
}
@media(max-width:470px) {
  .breadcrumb>span,.identity-label,.identity-arrow { display:none; }
  .identity { padding:4px; }
}
</style>
