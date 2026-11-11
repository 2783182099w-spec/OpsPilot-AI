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
      { path:'users', name:'UserManagement', component:()=>import('@/views/UserManagement.vue'), meta:{title:'用户管理',authenticate:true,admin:true} },
      { path:'releases', name:'Releases', component:()=>import('@/views/Releases.vue'), meta:{title:'发布流水线',authenticate:true} },
      { path:'diagnostic-trees', name:'DiagnosticTrees', component:()=>import('@/views/DiagnosticTrees.vue'), meta:{title:'诊断与修复',authenticate:true} },
      { path:'repair-approvals', name:'RepairApprovals', redirect:to=>({name:'Alerts',query:{...to.query,view:'approvals'}}) },
      { path:'diagnostics', name:'Diagnostics', redirect:to=>({name:'DiagnosticTrees',query:{...to.query,tab:'rules'}}) },
      { path:'jobs', name:'JobLibrary', component:()=>import('@/views/JobLibrary.vue'), meta:{title:'作业库',authenticate:true} },
      {path:'monitoring',name:'Monitoring',component:()=>import('@/views/Monitoring.vue'),meta:{title:'监控中心',authenticate:true}},
      {path:'alerts',name:'Alerts',component:()=>import('@/views/Alerts.vue'),meta:{title:'告警中心',authenticate:true}},
      {path:'hosts/:id/workbench',name:'HostWorkbench',component:()=>import('@/views/HostWorkbench.vue'),meta:{title:'主机工作台',authenticate:true}},
      { path:'operations', name:'Operations', component:()=>import('@/views/Operations.vue'), meta:{title:'健康巡检',authenticate:true} },
      { path: 'tasks', name: 'TaskCenter', component: () => import('@/views/TaskCenter.vue'), meta: { title: '任务中心', authenticate: true } },
      { path: '', redirect: { name: 'ShowCenter' } },
      {
        path: 'show_center',
        name: 'ShowCenter',
        component: () => import('@/views/ShowCenter.vue'),
        meta: { title: '运维总览', authenticate: true },
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
        meta: { title: 'Web SSH', authenticate: true, operator: true },
      },
      {
        path: 'multi_exec',
        name: 'MultiExec',
        component: () => import('@/views/MultiExec.vue'),
        meta: { title: '批量作业', authenticate: true, operator: true },
      },
      {
        path: 'ai_chat',
        name: 'AiChat',
        component: () => import('@/views/AiChat.vue'),
        meta: { title: 'AI 助手', authenticate: true, operator: true },
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
