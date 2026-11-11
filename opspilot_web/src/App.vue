<template>
  <a-config-provider :locale="zhCN" :theme="themeConfig">
    <router-view/>
  </a-config-provider>
</template>

<script setup>
import { computed, provide, ref, watchEffect } from 'vue'
import { theme } from 'ant-design-vue'
import zhCN from 'ant-design-vue/es/locale/zh_CN'

const appearance = ref(localStorage.getItem('opspilot-appearance') === 'dark' ? 'dark' : 'light')
const themeConfig = computed(() => ({
  algorithm: appearance.value === 'dark' ? theme.darkAlgorithm : theme.defaultAlgorithm,
  token: {
    colorPrimary: appearance.value === 'dark' ? '#5786fb' : '#245de0',
    borderRadius: 8,
    fontFamily: 'Inter, Segoe UI, Microsoft YaHei, system-ui, sans-serif',
  },
}))
provide('appearance', appearance)
watchEffect(() => {
  document.documentElement.dataset.theme = appearance.value
  localStorage.setItem('opspilot-appearance', appearance.value)
})
</script>

<style>
:root {
  color-scheme: light;
  --page:#f3f6fc; --surface:#fff; --surface-alt:#f8faff; --surface-hover:#edf2fc;
  --line:#e0e7f2; --text:#17243d; --muted:#5c6c85; --accent:#245de0; --accent-soft:#eaf0ff;
  --success:#087f64; --danger:#b52245; --shadow:0 1px 2px #0d23440a,0 5px 20px #192b5607;
}
:root[data-theme='dark'] {
  color-scheme: dark;
  --page:#0e1626; --surface:#152035; --surface-alt:#19263e; --surface-hover:#213250;
  --line:#2c3b57; --text:#edf2fc; --muted:#a9b8d2; --accent:#96b3ff; --accent-soft:#243863;
  --success:#76dfc0; --danger:#ff9db1; --shadow:0 1px 3px #080e1b75;
}
* { box-sizing:border-box; }
html,body,#app { min-height:100%; margin:0; }
body { background:var(--page); color:var(--text); }
#app { font-family:Inter,'Segoe UI','Microsoft YaHei',system-ui,sans-serif; -webkit-font-smoothing:antialiased; }
.ops-heading { margin:0; color:var(--text); font-size:clamp(23px,3vw,30px); font-weight:720; letter-spacing:-.025em; }
.ops-eyebrow { margin:0 0 7px; color:var(--accent); font-size:11px; font-weight:700; letter-spacing:.12em; }
.ops-muted { color:var(--muted); }
.ops-panel { background:var(--surface); border:1px solid var(--line); border-radius:13px; box-shadow:var(--shadow); }
</style>
