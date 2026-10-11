import { createStore } from 'vuex'

const STORAGE_KEY = 'uric'

function persist(state) {
  const data = JSON.stringify({ token: state.token, remember: state.remember })
  if (state.remember) {
    localStorage.setItem(STORAGE_KEY, data)
    sessionStorage.removeItem(STORAGE_KEY)
  } else {
    sessionStorage.setItem(STORAGE_KEY, data)
    localStorage.removeItem(STORAGE_KEY)
  }
}

function restore() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY) || sessionStorage.getItem(STORAGE_KEY)
    return raw ? JSON.parse(raw) : null
  } catch (e) {
    return null
  }
}

/** 解析 JWT 的 payload 部分（仅用于展示用户名，不用于校验） */
function decodePayload(token) {
  const base64 = (token || '').split('.')[1]
  if (!base64) return {}
  try {
    const normalized = base64.replace(/-/g, '+').replace(/_/g, '/')
    const padded = normalized.padEnd(normalized.length + (4 - (normalized.length % 4)) % 4, '=')
    const json = decodeURIComponent(
      atob(padded)
        .split('')
        .map((c) => '%' + ('00' + c.charCodeAt(0).toString(16)).slice(-2))
        .join('')
    )
    return JSON.parse(json)
  } catch (e) {
    return {}
  }
}

const cached = restore()

export default createStore({
  state: {
    token: cached?.token || '',
    remember: cached?.remember ?? true,
  },
  getters: {
    token: (state) => state.token,
    remember: (state) => state.remember,
    userInfo: (state) => decodePayload(state.token),
  },
  mutations: {
    setToken(state, token) {
      state.token = token || ''
      persist(state)
    },
    setRemember(state, remember) {
      state.remember = remember
      persist(state)
    },
  },
  actions: {},
  modules: {},
})
