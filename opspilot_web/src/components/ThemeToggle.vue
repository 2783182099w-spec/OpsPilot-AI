<template>
  <a-button class="theme-toggle" type="text" :title="label" :aria-label="label" @click="toggle">
    <template #icon><span class="theme-glyph" aria-hidden="true">{{ appearance === 'dark' ? '☀' : '☾' }}</span></template>
  </a-button>
</template>

<script setup>
import { computed, inject } from 'vue'

const appearance = inject('appearance')
const label = computed(() => appearance.value === 'dark' ? '切换到浅色主题' : '切换到深色主题')
function toggle() {
  appearance.value = appearance.value === 'dark' ? 'light' : 'dark'
}
</script>

<style scoped>
.theme-toggle { color:var(--muted); width:36px; height:36px; display:grid; place-items:center; }
.theme-glyph { font-size:22px; line-height:1; }
</style>
