<template>
  <a-layout class="layout">
    <a-layout-sider v-model:collapsed="collapsed" :trigger="null" collapsible theme="dark" breakpoint="lg">
      <div class="logo">
        <cloud-server-outlined/>
        <span v-if="!collapsed">OpsPilot-AI</span>
      </div>
      <a-menu v-model:selectedKeys="selectedKeys" theme="dark" mode="inline" @click="onMenuClick">
        <a-menu-item v-for="item in menus" :key="item.name">
          <component :is="item.icon"/>
          <span>{{ item.title }}</span>
        </a-menu-item>
      </a-menu>
    </a-layout-sider>

    <a-layout>
      <a-layout-header class="header">
        <menu-unfold-outlined v-if="collapsed" class="trigger" @click="collapsed = false"/>
        <menu-fold-outlined v-else class="trigger" @click="collapsed = true"/>
        <div class="header-title">{{ currentTitle }}</div>
        <div class="header-right">
          <a-tag color="blue">{{ username }}</a-tag>
          <a-button type="text" danger @click="logout">注销</a-button>
        </div>
      </a-layout-header>

      <a-layout-content class="content">
        <router-view/>
      </a-layout-content>

      <a-layout-footer class="footer">OpsPilot-AI 运维管理平台 · 本地开发环境</a-layout-footer>
    </a-layout>
  </a-layout>
</template>

<script setup>
import { computed, ref, onMounted } from 'vue'
import api from '@/api'
import { useRoute, useRouter } from 'vue-router'
import { useStore } from 'vuex'
import { Modal } from 'ant-design-vue'
import {
  CloudServerOutlined,
  DashboardOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  RobotOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons-vue'

const store = useStore()
onMounted(async () => { try { store.commit('setProfile', (await api.get('/users/me/')).data) } catch (e) { /* API 客户端统一处理 */ } })
const router = useRouter()
const route = useRoute()

const collapsed = ref(false)

const menus = [
  { name: 'TaskCenter', title: '任务中心', icon: ThunderboltOutlined },
  { name: 'ShowCenter', title: '展示中心', icon: DashboardOutlined },
  { name: 'Host', title: '资产管理', icon: CloudServerOutlined },
  { name: 'MultiExec', title: '批量执行', icon: ThunderboltOutlined },
  { name: 'AiChat', title: 'AI 助手', icon: RobotOutlined },
]

const selectedKeys = computed(() => {
  const matched = menus.find((item) => item.name === route.name)
  return matched ? [matched.name] : []
})

const currentTitle = computed(() => route.meta?.title || '')
const username = computed(() => store.getters.userInfo?.username || '未登录')

function onMenuClick({ key }) {
  router.push({ name: key })
}

function logout() {
  Modal.confirm({
    title: 'OpsPilot-AI 系统提示',
    content: '您确认要注销登录吗？',
    okText: '注销',
    cancelText: '取消',
    onOk() {
      store.commit('setToken', '')
      router.push({ name: 'Login' })
    },
  })
}
</script>

<style scoped>
.layout {
  min-height: 100vh;
}

.logo {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 56px;
  padding: 0 20px;
  color: #fff;
  font-size: 16px;
  font-style: italic;
  white-space: nowrap;
  overflow: hidden;
}

.header {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 0 20px;
  background: #fff;
}

.trigger {
  font-size: 18px;
  cursor: pointer;
}

.header-title {
  flex: 1;
  font-size: 16px;
  font-weight: 500;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 8px;
}

.content {
  margin: 16px;
  padding: 16px;
  background: #fff;
  border-radius: 6px;
  min-height: 0;
}

.footer {
  padding: 12px 20px;
  text-align: center;
  color: rgba(0, 0, 0, 0.45);
}
</style>
