<template>
  <section>
    <div class="policy-banner"><div><strong>HTTP 探测策略</strong><p>从探测目标、失败阈值到排查与恢复的配置入口。可直接使用内置诊断，需要定制时再维护模板。</p></div>
      <a-button size="small" @click="load">刷新联动</a-button><a-tag :color="autoEnabled?'orange':'default'">{{autoEnabled?'自动修复总开关已启用':'自动修复总开关关闭'}}</a-tag></div>
    <div>
      <a-tabs>
        <a-tab-pane v-if="admin" key="policies" tab="告警联动">
          <diagnostic-policies :tree-id="treeId" :probes="probes" :revisions="revisions" :sample="sample"/>
        </a-tab-pane>
        <a-tab-pane key="services" tab="服务探测">
          <a-button v-if="admin" type="primary" class="action" @click="editProbe()">新建探测</a-button>
          <a-table :columns="probeColumns" :data-source="probes" :loading="loading" row-key="id" :scroll="{x:930}" :pagination="{pageSize:15}">
            <template #bodyCell="{column,record}">
              <template v-if="column.key==='endpoint'"><strong>{{record.name}}</strong><br><small class="ops-muted">{{record.host_name}} · :{{record.port}}{{record.path}}</small></template>
              <template v-else-if="column.key==='state'"><a-tag :color="stateColor[record.state]">{{record.enabled ? serviceStates[record.state] : '已停用'}}</a-tag><small class="ops-muted">{{record.evidence}}</small></template>
              <template v-else-if="column.key==='policy'">{{modes[record.mode]}}<br><small class="ops-muted">{{trees.find(t=>t.id===record.tree_id)?.name||'—'}}</small></template>
              <template v-else-if="column.key==='severity'"><a-tag :color="severityColor[record.severity]">{{severityNames[record.severity]}}</a-tag></template>
              <template v-else-if="column.key==='checked'">{{time(record.checked_at)}}</template>
              <template v-else-if="column.key==='action'">
                <a-space><router-link :to="{name:'Alerts',query:{host_id:record.host_id,source:'service'}}">告警</router-link><a-button v-if="admin" size="small" @click="editProbe(record)">配置</a-button></a-space>
              </template>
            </template>
          </a-table>
        </a-tab-pane>
      </a-tabs>
    </div>
    <a-drawer v-model:open="probeOpen" title="配置服务探测" :width="'min(560px, 100vw)'">
      <a-form layout="vertical">
        <a-form-item label="探测名称"><a-input v-model:value="probe.name" :maxlength="100"/></a-form-item>
        <a-form-item label="目标资产" extra="探测访问资产登记的 IP，连续 3 次失败触发，连续 3 次通过才标为恢复。"><a-select v-model:value="probe.host_id" :disabled="!!probe.id" :options="hostOptions" show-search option-filter-prop="label"/></a-form-item>
        <a-form-item label="HTTP 端口与路径"><a-space><a-input-number v-model:value="probe.port" :min="1" :max="65535"/><a-input v-model:value="probe.path" placeholder="/health" :maxlength="151"/></a-space></a-form-item>
        <a-form-item label="告警级别"><a-select v-model:value="probe.severity" :options="options(severityNames)"/></a-form-item>
        <a-form-item label="以后同类告警的处理方式" extra="这里保存的是持续策略；只想处理当前一次，请到告警详情授权。"><a-select v-model:value="probe.mode" :options="options(modes)"/></a-form-item>
        <a-form-item v-if="probe.mode!=='monitor'" label="诊断模板" extra="留空时使用与探测端口和路径对应的内置检查。"><a-select v-model:value="probe.tree_id" :options="treeOptions" placeholder="使用内置检查" allow-clear/></a-form-item>
        <a-form-item v-if="['approval','auto'].includes(probe.mode)" label="允许修复的服务"><a-input value="nginx.service" disabled/></a-form-item>
        <a-form-item v-if="probe.mode==='auto'" label="自动处理失败时站内转人工" extra="在告警队列标记待接手，并记录失败原因。">
          <a-switch v-model:checked="probe.escalate_on_failure"/>
        </a-form-item>
        <a-form-item label="启用探测"><a-switch v-model:checked="probe.enabled"/></a-form-item>
        <a-button type="primary" :loading="saving" @click="saveProbe">保存策略</a-button>
      </a-form>
    </a-drawer>
  </section>
</template>

<script setup>
import {computed,onMounted,ref} from 'vue'
import {useStore} from 'vuex'
import {message} from 'ant-design-vue'
import api from '@/api'
import DiagnosticPolicies from '@/components/DiagnosticPolicies.vue'
import {options,time} from '@/utils/ops'
import {serviceStates,severityColor,severityNames,stateColor} from '@/utils/signals'

defineProps({treeId:Number})
const store=useStore()
const admin=computed(()=>store.getters.userInfo?.is_superuser)
const probes=ref([]),trees=ref([]),hosts=ref([]),sample=ref(null),autoEnabled=ref(false),loading=ref(false),saving=ref(false)
const revisions=ref([])
const probeOpen=ref(false)
const probe=ref({})
const modes={monitor:'仅监控',diagnose:'自动只读诊断',approval:'诊断 + 人工审批修复',auto:'诊断 + 测试环境自动修复'}
const probeColumns=[{title:'探测 / 资产',key:'endpoint',width:220},{title:'探测状态',key:'state',width:180},{title:'告警级别',key:'severity',width:100},{title:'联动策略',key:'policy',width:200},{title:'最近采样',key:'checked',width:160},{title:'操作',key:'action',width:135}]
const hostOptions=computed(()=>hosts.value.map(host=>({value:host.id,label:host.name+' · '+host.ip_addr})))
const treeOptions=computed(()=>trees.value.map(tree=>({value:tree.id,label:tree.name+' · v'+tree.version})))
async function load(){
  loading.value=true
  try {
    const [serviceData,treeData]=await Promise.all([api.get('/monitoring/service-checks'),api.get('/mtask/diagnostic-trees')])
    probes.value=serviceData.data.results;autoEnabled.value=serviceData.data.automatic_repair_enabled
    trees.value=treeData.data.results;sample.value=treeData.data.sample
    revisions.value=treeData.data.revisions
  }catch{ /* shared API feedback */ }
  finally{loading.value=false}
}
async function getHosts(){hosts.value=(await api.get('/host/')).data}
async function editProbe(row){
  try{await getHosts()}catch{return}
  probe.value=row?{...row}:{name:'',port:80,path:'/',unit:'nginx.service',mode:'monitor',severity:'warning',enabled:false,escalate_on_failure:true}
  probeOpen.value=true
}
async function saveProbe(){
  saving.value=true
  try{
    const {id,...body}=probe.value
    await api.post('/monitoring/service-checks'+(id?'/'+id:''),{...body,unit:['approval','auto'].includes(body.mode)?'nginx.service':body.unit,tree_id:body.tree_id??null})
    probeOpen.value=false
    message.success('探测策略已保存')
    load()
  }catch{ /* shared API feedback */ }
  finally{saving.value=false}
}
onMounted(load)
</script>

<style scoped>
.policy-banner{display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap;padding:15px 17px}.policy-banner p{margin:5px 0 0;color:var(--muted)}
.action { margin-bottom:12px; }
</style>
