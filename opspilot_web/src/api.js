import axios from 'axios'
import { message } from 'ant-design-vue'

import settings from '@/settings'
import store from '@/store'
import router from '@/router'

/**
 * 统一接口客户端：
 * - 自动携带 JWT
 * - 统一解析 DRF 的错误响应
 * - 401 自动清理登录态并跳回登录页
 */
const api = axios.create({
  baseURL: settings.host,
  timeout: 20000,
})

export function extractError(data) {
  if (!data) return '请求失败'
  if (typeof data === 'string') return data
  if (Array.isArray(data)) return data.join('；')
  if (data.detail) return data.detail
  if (data.error) return data.error
  const parts = []
  Object.keys(data).forEach((key) => {
    const value = data[key]
    const text = Array.isArray(value) ? value.join('；') : String(value)
    parts.push(key === 'non_field_errors' ? text : `${key}: ${text}`)
  })
  return parts.join('；') || '请求失败'
}

api.interceptors.request.use((config) => {
  const token = store.getters.token
  if (token) {
    config.headers.Authorization = `jwt ${token}`
  }
  return config
})

api.interceptors.response.use(
  (response) => response,
  (error) => {
    const status = error?.response?.status
    const url = error?.config?.url || ''
    const isLoginRequest = url.includes('/users/login')

    if (status === 401 && !isLoginRequest) {
      store.commit('setToken', '')
      message.error('登录已过期，请重新登录')
      router.push({ name: 'Login' })
    } else if (!isLoginRequest) {
      message.error(extractError(error?.response?.data))
    }

    return Promise.reject(error)
  }
)

export default api
