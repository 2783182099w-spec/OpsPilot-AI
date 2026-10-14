<template>
  <div>
    <a-row :gutter="16">
      <a-col :span="8">
        <a-card>
          <a-statistic title="主机总数" :value="hosts.length" :loading="loading">
            <template #prefix>
              <cloud-server-outlined/>
            </template>
          </a-statistic>
        </a-card>
      </a-col>
      <a-col :span="8">
        <a-card>
          <a-statistic title="主机类别" :value="categories.length" :loading="loading">
            <template #prefix>
              <appstore-outlined/>
            </template>
          </a-statistic>
        </a-card>
      </a-col>
      <a-col :span="8">
        <a-card>
          <a-statistic title="指令模板" :value="templates.length" :loading="loading">
            <template #prefix>
              <code-outlined/>
            </template>
          </a-statistic>
        </a-card>
      </a-col>
    </a-row>

    <a-row :gutter="16" class="charts">
      <a-col :span="12">
        <a-card title="各类别主机数量" size="small">
          <div ref="hostChartRef" class="chart"></div>
        </a-card>
      </a-col>
      <a-col :span="12">
        <a-card title="指令模板分布" size="small">
          <div ref="templateChartRef" class="chart"></div>
        </a-card>
      </a-col>
    </a-row>
  </div>
</template>

<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import * as echarts from 'echarts'
import { AppstoreOutlined, CloudServerOutlined, CodeOutlined } from '@ant-design/icons-vue'

import api from '@/api'

const loading = ref(true)
const hosts = ref([])
const categories = ref([])
const templates = ref([])

const hostChartRef = ref(null)
const templateChartRef = ref(null)
let hostChart = null
let templateChart = null

const EMPTY_OPTION = {
  title: {
    text: '暂无数据',
    left: 'center',
    top: 'middle',
    textStyle: { color: '#bfbfbf', fontSize: 14, fontWeight: 'normal' },
  },
}

function countBy(items, pick) {
  const result = {}
  items.forEach((item) => {
    const key = pick(item) || '未分类'
    result[key] = (result[key] || 0) + 1
  })
  return result
}

function renderHostChart() {
  if (!hostChart) return
  const data = countBy(hosts.value, (host) => host.category_name)
  const names = Object.keys(data)
  if (!names.length) {
    hostChart.setOption(EMPTY_OPTION, true)
    return
  }
  hostChart.setOption({
    tooltip: { trigger: 'axis' },
    grid: { left: 40, right: 20, top: 20, bottom: 40 },
    xAxis: { type: 'category', data: names },
    yAxis: { type: 'value', minInterval: 1 },
    series: [
      {
        name: '主机数量',
        type: 'bar',
        barMaxWidth: 40,
        itemStyle: { color: '#2a5298', borderRadius: [4, 4, 0, 0] },
        data: names.map((name) => data[name]),
      },
    ],
  }, true)
}

function renderTemplateChart() {
  if (!templateChart) return
  const data = countBy(templates.value, (item) => item.category_name)
  const names = Object.keys(data)
  if (!names.length) {
    templateChart.setOption(EMPTY_OPTION, true)
    return
  }
  templateChart.setOption({
    tooltip: { trigger: 'item' },
    legend: { bottom: 0 },
    series: [
      {
        name: '模板数量',
        type: 'pie',
        radius: ['40%', '65%'],
        itemStyle: { borderRadius: 6, borderColor: '#fff', borderWidth: 2 },
        label: { formatter: '{b}: {c}' },
        data: names.map((name) => ({ name, value: data[name] })),
      },
    ],
  }, true)
}

function resizeCharts() {
  hostChart?.resize()
  templateChart?.resize()
}

async function load() {
  loading.value = true
  try {
    const [hostRes, categoryRes, templateRes] = await Promise.all([
      api.get('/host/'),
      api.get('/host/category'),
      api.get('/mtask/templates'),
    ])
    hosts.value = hostRes.data
    categories.value = categoryRes.data
    templates.value = templateRes.data
  } finally {
    loading.value = false
  }
  await nextTick()
  renderHostChart()
  renderTemplateChart()
}

onMounted(() => {
  hostChart = echarts.init(hostChartRef.value)
  templateChart = echarts.init(templateChartRef.value)
  window.addEventListener('resize', resizeCharts)
  load()
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', resizeCharts)
  hostChart?.dispose()
  templateChart?.dispose()
})
</script>

<style scoped>
.charts {
  margin-top: 16px;
}

.chart {
  height: 320px;
}
</style>
