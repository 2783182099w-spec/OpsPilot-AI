<template>
  <div class="login-page">
    <a-card class="login-card" :bordered="false">
      <div class="login-head">
        <cloud-server-outlined class="login-logo"/>
        <h2>OpsPilot-AI 运维管理平台</h2>
        <p>Hello, OpsPilot-AI!</p>
      </div>

      <a-form :model="form" layout="vertical" @finish="onSubmit">
        <a-form-item label="用户名" name="username" :rules="[{ required: true, message: '请输入用户名' }]">
          <a-input v-model:value="form.username" size="large" placeholder="请输入用户名" allow-clear>
            <template #prefix>
              <user-outlined/>
            </template>
          </a-input>
        </a-form-item>

        <a-form-item label="密码" name="password" :rules="[{ required: true, message: '请输入密码' }]">
          <a-input-password v-model:value="form.password" size="large" placeholder="请输入密码">
            <template #prefix>
              <lock-outlined/>
            </template>
          </a-input-password>
        </a-form-item>

        <a-form-item>
          <a-checkbox v-model:checked="form.remember">记住我</a-checkbox>
        </a-form-item>

        <a-button type="primary" html-type="submit" size="large" block :loading="loading">
          登录
        </a-button>
      </a-form>
    </a-card>
  </div>
</template>

<script setup>
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useStore } from 'vuex'
import { message } from 'ant-design-vue'
import { CloudServerOutlined, LockOutlined, UserOutlined } from '@ant-design/icons-vue'

import api, { extractError } from '@/api'

const router = useRouter()
const store = useStore()

const loading = ref(false)
const form = reactive({
  username: '',
  password: '',
  remember: true,
})

async function onSubmit() {
  loading.value = true
  try {
    const { data } = await api.post('/users/login/', {
      username: form.username,
      password: form.password,
    })
    // 先记录“记住我”的偏好，再写入 token，两者会被一起持久化
    store.commit('setRemember', form.remember)
    store.commit('setToken', data.access)
    message.success('登录成功，欢迎回来！')
    router.push({ name: 'ShowCenter' })
  } catch (err) {
    message.error(extractError(err?.response?.data) || '登录失败，请检查用户名和密码')
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-page {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
  background: linear-gradient(135deg, #1e3c72 0%, #2a5298 100%);
}

.login-card {
  width: 400px;
  border-radius: 8px;
  box-shadow: 0 8px 30px rgba(0, 0, 0, 0.25);
}

.login-head {
  text-align: center;
  margin-bottom: 24px;
}

.login-logo {
  font-size: 40px;
  color: #2a5298;
}

.login-head h2 {
  margin: 12px 0 4px;
}

.login-head p {
  margin: 0;
  color: rgba(0, 0, 0, 0.45);
}
</style>
