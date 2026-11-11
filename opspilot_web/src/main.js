import { createApp } from 'vue'
import {
  Alert, AutoComplete, Badge, Button, Card, Checkbox, Col, Collapse, ConfigProvider,
  Descriptions, Drawer, Dropdown, Empty, Form, Input, InputNumber, List, Menu, Modal,
  PageHeader, Popconfirm, Progress, Radio, Result, Row, Select, Space, Spin, Statistic,
  Steps, Switch, Table, Tabs, Tag, Timeline, Tooltip, Upload,
} from 'ant-design-vue'
import 'ant-design-vue/dist/reset.css'

import App from './App.vue'
import router from './router'
import store from './store'
import settings from '@/settings'

const app = createApp(App)
const components = [
  Alert, AutoComplete, Badge, Button, Card, Checkbox, Col, Collapse, ConfigProvider,
  Descriptions, Drawer, Dropdown, Empty, Form, Input, InputNumber, List, Menu, Modal,
  PageHeader, Popconfirm, Progress, Radio, Result, Row, Select, Space, Spin, Statistic,
  Steps, Switch, Table, Tabs, Tag, Timeline, Tooltip, Upload,
]
components.forEach(component => app.use(component))

app.use(store)
app.use(router)
app.config.globalProperties.$settings = settings
app.mount('#app')
