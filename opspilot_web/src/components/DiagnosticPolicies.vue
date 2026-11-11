
<template>
  <section>
    <div class="policy-toolbar">
      <div><strong>告警联动规则</strong><p class="ops-muted">按来源与资产选择诊断树。同一告警轮次只运行一条策略，重叠命中会交由人工核对。</p></div>
      <a-space><a-button @click="load" :loading="loading">刷新</a-button><a-button type="primary" @click="edit()">新建策略</a-button></a-space>
    </div>
    <a-table :columns="columns" :data-source="visibleRows" row-key="id" :loading="loading" :scroll="{x:760}" :pagination="{pageSize:10}">
      <template #bodyCell="{column,record}">
        <template v-if="column.key==='name'"><strong>{{record.name}}</strong><br><small class="ops-muted">v{{record.version}} · {{record.host_ids.length}} 台资产</small></template>
        <template v-else-if="column.key==='match'">{{sources[record.source]}}<br><small class="ops-muted">{{matchLabel(record)}}</small></template>
        <template v-else-if="column.key==='trigger'">{{triggers[record.trigger]}}<small v-if="record.trigger!=='event'" class="ops-muted"> · {{record.interval}} 分钟</small></template>
        <template v-else-if="column.key==='mode'">{{modes[record.mode]}}<br><small class="ops-muted">{{record.fallback?'异常转人工':'保留执行记录'}}</small></template>
        <template v-else-if="column.key==='enabled'"><a-tag :color="record.enabled?'green':'default'">{{record.enabled?'已启用':'停用'}}</a-tag></template>
        <template v-else-if="column.key==='edit'"><a-space><a-button size="small" @click="edit(record)">配置</a-button><a-button v-if="record.enabled" size="small" @click="disable(record)">停用</a-button></a-space></template>
      </template>
    </a-table>

    <a-drawer v-model:open="open" :title="draft.id?'编辑告警联动':'新建告警联动'" :width="'min(610px, 100vw)'">
      <a-form layout="vertical">
        <a-form-item label="策略名称"><a-input v-model:value="draft.name" :maxlength="100" placeholder="例如：测试环境 Nginx 停止处理"/></a-form-item>
        <div class="fields">
          <a-form-item label="告警来源"><a-select v-model:value="draft.source" :options="choices(sources)" @change="changeSource"/></a-form-item>
          <a-form-item label="目标资产"><a-select v-model:value="draft.host_ids" mode="multiple" :max-tag-count="2" :options="hosts" show-search option-filter-prop="label" placeholder="明确限定资产"/></a-form-item>
        </div>
        <a-form-item :label="draft.source==='service'?'HTTP 探测':'指标或巡检检查项'" :extra="draft.source==='service'?'修复须绑定一个已开启恢复的 Nginx HTTP 探测。':'留空匹配资产内的该来源。示例 cpu、disk、ssh_password、ssh_root、connectivity。'">
          <a-select v-if="draft.source==='service'" v-model:value="draft.signal" :options="probeOptions" allow-clear placeholder="选择探测"/>
          <a-input v-else v-model:value="draft.signal" :maxlength="80" placeholder="精确匹配标识"/>
        </a-form-item>
        <div class="fields">
          <a-form-item label="触发方式"><a-select v-model:value="draft.trigger" :options="choices(triggers)"/></a-form-item>
          <a-form-item v-if="draft.trigger!=='event'" label="定时扫描间隔（分钟）"><a-input-number v-model:value="draft.interval" :min="15" :max="1440" :precision="0" style="width:100%"/></a-form-item>
        </div>
        <p v-if="draft.trigger!=='event'" class="ops-muted">定时扫描未处理的活跃告警。只读策略可按间隔复检；每轮告警的修复最多提交一次。</p>
        <a-form-item label="诊断树">
          <a-select v-model:value="draft.tree" :options="treeOptions" @change="changeTree"/>
        </a-form-item>
        <a-form-item v-if="draft.tree==='custom'" label="锁定模板版本">
          <a-select v-model:value="draft.revision" :options="revisionOptions" show-search option-filter-prop="label" @change="draft.branch=undefined" placeholder="选择自建诊断树版本"/>
        </a-form-item>
        <a-form-item label="诊断后的处理方式"><a-select v-model:value="draft.mode"  :options="modeOptions" @change="draft.branch=undefined"/></a-form-item>
        <a-alert v-if="baseline&&draft.mode==='approval'" type="warning" show-icon
          message="已确认的 SSH 基线异常 → 独立审批 → 平台执行"
          description="选择按来源匹配内置检查。仅支持测试资产上的 root 密钥纳管，执行后重新建立密钥连接验证。"/>
        <template v-else-if="draft.mode!=='diagnose'">
          <a-form-item label="允许修复的结论分支" extra="通往该结论的路径均须确认目标 HTTP 异常且 nginx.service 未运行。其他分支保留诊断证据。">
            <a-select v-model:value="draft.branch" :options="branchOptions" placeholder="选择树的结论"/>
          </a-form-item>
          <a-alert :type="draft.mode==='auto'?'warning':'info'" show-icon
            :message="draft.mode==='auto'?'仅测试环境且开启全局自愈开关时可自动处理':'诊断后暂停，由另一名管理员审批放行'"
            description="动作是校验配置后启动 Nginx，随后做本机检查和外部 HTTP 探测验证。"/>
        </template>
        <div class="policy-switch"><a-switch v-model:checked="draft.fallback"/><span>结论未匹配、证据不足、修复或复检失败时进入待人工队列</span></div>
        <div class="policy-switch"><a-switch v-model:checked="draft.enabled"/><span>启用策略</span></div>
      </a-form>
      <template #extra><a-button type="primary" :loading="saving" @click="save()">保存</a-button></template>
    </a-drawer>
  </section>
</template>

<script setup>
import {computed,onMounted,ref} from 'vue'
import {Modal,message} from 'ant-design-vue'
import api from '@/api'

const props=defineProps({probes:Array,revisions:Array,sample:Object,treeId:Number})
const sources={service:'HTTP 服务探测',monitor:'监控指标',inspection:'巡检',external:'外部告警'}
const modes={diagnose:'只读诊断',approval:'审批后修复',auto:'测试环境自动修复'}
const triggers={event:'告警触发',scheduled:'定时扫描',both:'告警触发 + 定时扫描'}
const choices=values=>Object.entries(values).map(([value,label])=>({value,label}))
const rows=ref([]),hosts=ref([]),loading=ref(false),saving=ref(false),open=ref(false),draft=ref({})
const columns=[{title:'策略',key:'name'},{title:'匹配条件',key:'match'},{title:'触发',key:'trigger'},
  {title:'处理方式',key:'mode'},{title:'状态',key:'enabled',width:85},{title:'操作',key:'edit',width:140}]
const probeOptions=computed(()=>props.probes.filter(item=>draft.value.host_ids?.includes(item.host_id)).map(item=>({
  value:String(item.id),label:item.name+' · '+item.host_name+' · #'+item.id
})))
const scopedRevisions=computed(()=>props.treeId?props.revisions.filter(item=>item.tree_id===props.treeId):props.revisions)
const visibleRows=computed(()=>props.treeId?rows.value.filter(item=>scopedRevisions.value.some(revision=>revision.id===item.revision)):rows.value)
const revisionOptions=computed(()=>scopedRevisions.value.map(item=>({value:item.id,label:item.name+' · v'+item.version})))
const treeOptions=computed(()=>choices({source:'按告警来源匹配内置检查',
  ...(draft.value.source==='service'?{nginx:'内置 Nginx 服务故障树'}:{}),custom:'自建诊断树'}))
const baseline=computed(()=>draft.value.source==='inspection'&&['ssh_password','ssh_root'].includes(draft.value.signal?.trim()))
const modeOptions=computed(()=>choices(draft.value.source==='service'?modes:baseline.value?
  {diagnose:modes.diagnose,approval:modes.approval}:{diagnose:modes.diagnose}))
const branchOptions=computed(()=>{
  const definition=draft.value.tree==='nginx'?props.sample:props.revisions.find(item=>item.id===draft.value.revision)?.definition
  return Object.entries(definition?.conclusions||{}).map(([value,label])=>({value,label:label+' · '+value}))
})

function matchLabel(row){
  if(!row.signal)return '该来源全部'
  return row.source==='service'?props.probes.find(item=>String(item.id)===row.signal)?.name||('#'+row.signal):row.signal
}
function changeTree(){
  draft.value.revision=undefined
  draft.value.branch=undefined
}
function changeSource(){
  Object.assign(draft.value,{tree:props.treeId?'custom':'source',signal:undefined,mode:'diagnose'})
  changeTree()
  if(props.treeId)draft.value.revision=scopedRevisions.value[0]?.id
}
async function load(){
  loading.value=true
  try{rows.value=(await api.get('/mtask/diagnostic-policies')).data}
  finally{loading.value=false}
}
async function edit(row){
  if(!hosts.value.length){
    const {data}=await api.get('/host/')
    hosts.value=data.map(host=>({value:host.id,label:host.name+' · '+host.ip_addr}))
  }
  draft.value=row?{...row,signal:row.signal||undefined,host_ids:[...row.host_ids]}:{
    source:'service',host_ids:[],tree:props.treeId?'custom':'source',revision:props.treeId?scopedRevisions.value[0]?.id:undefined,trigger:'event',mode:'diagnose',interval:15,fallback:true,enabled:false
  }
  open.value=true
}
async function save(row){
  const data=row||{...draft.value,signal:draft.value.signal||'',branch:
    baseline.value&&draft.value.mode==='approval'?'confirmed_violation':draft.value.branch||''}
  saving.value=true
  try{
    await api.post('/mtask/diagnostic-policies'+(data.id?'/'+data.id:''),data)
    open.value=false
    message.success(row?'已停用策略':'诊断策略已保存')
    await load()
  }finally{saving.value=false}
}
function disable(row){
  Modal.confirm({title:'停用 '+row.name,content:'停止新任务，未派发的修复将失去授权。已执行的记录会保留。',
    onOk:()=>save({...row,enabled:false,disable_only:true})})
}
onMounted(load)
</script>

<style scoped>
.policy-toolbar{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;margin-bottom:12px}
.policy-toolbar p{margin:3px 0 0}
.fields{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}
.policy-switch{display:flex;align-items:center;gap:10px;margin-top:16px}
@media(max-width:620px){.fields{grid-template-columns:1fr;gap:0}.policy-toolbar{flex-wrap:wrap}}
</style>
