import { createApp } from 'vue'
import Antd from 'ant-design-vue'
import 'ant-design-vue/dist/reset.css'

import App from './App.vue'
import router from './router'
import store from './store'
import settings from '@/settings'

const app = createApp(App)

app.use(store)
app.use(router)
app.use(Antd)

app.config.globalProperties.$settings = settings

app.mount('#app')

