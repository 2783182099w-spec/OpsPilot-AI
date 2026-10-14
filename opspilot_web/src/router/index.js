import { createRouter, createWebHistory } from 'vue-router'

import store from '@/store'

const routes = [
  {
    path: '/login',
    name: 'Login',
    component: () => import('@/views/Login.vue'),
    meta: { title: '账户登录' },
  },
  {
    path: '/opspilot',
    alias: ['/', '/uric'],
    component: () => import('@/views/Base.vue'),
    children: [
      { path: 'tasks', name: 'TaskCenter', component: () => import('@/views/TaskCenter.vue'), meta: { title: '任务中心', authenticate: true } },
      { path: '', redirect: { name: 'ShowCenter' } },
      {
        path: 'show_center',
        name: 'ShowCenter',
        component: () => import('@/views/ShowCenter.vue'),
        meta: { title: '展示中心', authenticate: true },
      },
      {
        path: 'host',
        name: 'Host',
        component: () => import('@/views/Host.vue'),
        meta: { title: '资产管理', authenticate: true },
      },
      {
        path: 'console/:host_id',
        name: 'Console',
        component: () => import('@/views/Console.vue'),
        meta: { title: 'Console', authenticate: true },
      },
      {
        path: 'multi_exec',
        name: 'MultiExec',
        component: () => import('@/views/MultiExec.vue'),
        meta: { title: '批量执行', authenticate: true },
      },
      {
        path: 'ai_chat',
        name: 'AiChat',
        component: () => import('@/views/AiChat.vue'),
        meta: { title: 'AI 助手', authenticate: true },
      },
    ],
  },
  { path: '/:pathMatch(.*)*', redirect: '/' },
]

const router = createRouter({
  history: createWebHistory(process.env.BASE_URL),
  routes,
})

router.beforeEach((to, from, next) => {
  if (to.meta?.title) {
    document.title = `${to.meta.title} - OpsPilot-AI 运维管理平台`
  }
  if (to.meta?.authenticate && !store.getters.token) {
    next({ name: 'Login' })
  } else {
    next()
  }
})

export default router
