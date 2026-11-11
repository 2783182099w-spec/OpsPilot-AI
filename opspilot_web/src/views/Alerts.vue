<template>
  <a-config-provider :theme="focusTheme">
    <section class="alerts-page" :class="{focus}">
      <header class="wall">
        <div class="wall-top">
          <div>
            <p class="wall-eyebrow"><span></span> OPSPILOT / INCIDENT OPERATIONS</p>
            <h1>告警中心</h1>
            <p class="wall-description">{{ scopeLabel }} · 查看当前风险、待处理事项和恢复记录。</p>
          </div>
          <div class="wall-actions">
            <span class="wall-clock">{{updatedAt ? '更新于 '+clock(updatedAt) : '等待数据'}}<i :class="{problem:fetchFailed}"></i></span>
            <button type="button" @click="load()" :disabled="loading"><reload-outlined/> 刷新</button>
            <button type="button" class="focus-button" @click="focus=!focus"><fullscreen-exit-outlined v-if="focus"/><fullscreen-outlined v-else/> {{focus?'退出大屏':'值班大屏'}}</button>
          </div>
        </div>
        <div class="tiles">
          <button v-for="tile in tiles" :key="tile.key" type="button" class="tile" :class="[tile.tone,{picked:tile.key===view}]" :aria-pressed="tile.key===view" @click="choose(tile.key)">
            <div class="tile-title"><span class="mark"></span><span>{{tile.label}}</span></div>
            <strong>{{ overview[tile.key] ?? '—' }}</strong>
            <small>{{tile.hint}}</small>
          </button>
        </div>
      </header>

      <div v-if="canOperate&&!focus" class="queue-tabs" aria-label="告警工作区">
        <router-link :to="{name:'Alerts',query:{...route.query,view:undefined,finding:undefined}}" :class="{selected:!approvals}">告警队列</router-link>
        <router-link :to="{name:'RepairApprovals'}" :class="{selected:approvals}">待审批</router-link>
      </div>
      <repair-approvals v-if="approvals"/>
      <section v-else class="ops-panel queue">
        <div class="queue-heading">
          <div><p class="ops-eyebrow">INCIDENT QUEUE</p><h2>告警队列</h2></div>
          <a-space wrap>
            <a-button v-if="canOperate" :type="filters.manual?'primary':'default'" @click="toggleManual">待人工接手</a-button>
            <router-link v-if="admin" class="policy-link" :to="{name:'Diagnostics'}"><setting-outlined/> 配置联动</router-link>
            <a-button :aria-expanded="advanced" @click="advanced=!advanced"><filter-outlined/> 高级筛选<span v-if="filterCount"> · {{filterCount}}</span></a-button>
          </a-space>
        </div>
        <div class="queue-controls">
          <a-segmented v-model:value="view" :options="views" @change="search"/>
          <a-input-search v-model:value="draftSearch" placeholder="搜索告警、主机或负责人" allow-clear @search="applySearch"/>
        </div>
        <div v-if="advanced" class="advanced">
          <a-select v-model:value="filters.source" placeholder="全部来源" allow-clear :options="options(sources)" @change="search"/>
          <a-select v-model:value="filters.condition" placeholder="全部故障状态" allow-clear :options="options(conditions)" @change="search"/>
          <a-select v-model:value="filters.state" placeholder="全部处理状态" allow-clear :options="options(handling)" @change="search"/>
          <a-select v-model:value="filters.severity" placeholder="全部级别" allow-clear :options="options(severityNames)" @change="search"/>
          <a-select v-model:value="filters.environment" placeholder="全部环境" allow-clear :options="options(environments)" @change="search"/>
          <a-input-search v-model:value="draftBusiness" placeholder="按业务搜索" allow-clear @search="applyBusiness"/>
          <a-button type="text" @click="reset">清除筛选</a-button>
        </div>
        <a-alert v-if="fetchFailed" type="warning" show-icon message="暂时无法刷新告警，已保留上一次加载的结果。" style="margin:12px 0"/>
        <a-table :data-source="rows" :columns="columns" row-key="id" :loading="loading&&!rows.length"
          :pagination="{current:page,total,pageSize:20,showSizeChanger:false,showTotal:value=>'共 '+value+' 条'}" :scroll="{x:700}" @change="onPage">
          <template #emptyText><a-empty :description="view==='inbox'?'当前范围内没有待处理告警':'当前筛选范围内没有告警'"/></template>
          <template #bodyCell="{column,record}">
            <template v-if="column.key==='title'">
              <div class="alert-title">
                <span class="severity-dot" :class="record.severity"></span>
                <div><button class="title-link" type="button" @click="open(record)">{{record.title}}</button>
                  <small>{{record.host__name||'资产已删除'}} · {{environments[record.host__environment]||'未知环境'}}<template v-if="record.host__business"> · {{record.host__business}}</template></small>
                </div>
              </div>
            </template>
            <template v-else-if="column.key==='condition'">
              <a-tag :color="stateColor[record.condition]">{{conditions[record.condition]||record.condition}}</a-tag>
              <small class="row-note">{{severityNames[record.severity]||'未知级别'}} · {{sources[record.source]||record.source}}</small>
            </template>
            <template v-else-if="column.key==='handling'">
              {{handling[record.state]||record.state}} <a-tag v-if="record.manual_required" color="red">待接手</a-tag><small class="row-note">{{record.assignee||'未分派'}}<template v-if="isSilenced(record)"> · 维护中</template></small>
            </template>
            <template v-else-if="column.key==='last'">{{clock(record.last_seen)}}<small class="row-note">{{time(record.last_seen).split(' ')[0]}} · {{record.occurrences}} 次</small></template>
            <template v-else-if="column.key==='action'"><a-button type="link" size="small" @click="open(record)">证据与处理</a-button></template>
          </template>
        </a-table>
      </section>

      <a-drawer :open="drawer" title="告警证据与处理" :width="'min(660px, 100vw)'" @close="closeDrawer">
        <a-spin :spinning="detailLoading">
          <a-result v-if="!detail&&!detailLoading" status="warning" title="无法加载告警详情">
            <template #extra><a-button @click="reloadDetail()">重试</a-button></template>
          </a-result>
          <template v-if="detail">
            <header class="drawer-heading">
              <a-space wrap><a-tag :color="severityColor[detail.severity]">{{severityNames[detail.severity]}}</a-tag>
                <a-tag :color="stateColor[detail.condition]">{{conditions[detail.condition]}}</a-tag>
                <span class="ops-muted">{{sources[detail.source]}} · 第 {{detail.occurrences}} 次</span></a-space>
              <h2>{{detail.title}}</h2><p class="ops-muted">{{detail.host_name}} · {{environments[detail.environment]||'未知环境'}}<template v-if="detail.business"> · {{detail.business}}</template></p>
            </header>
            <div v-if="detail.signal?.metric" class="signal-card">
              <div><small>{{metricNames[detail.signal.metric]||detail.signal.metric}}</small><strong>{{signalValue(detail.signal.value)}}</strong></div>
              <div><small>触发阈值</small><strong>{{signalValue(detail.signal.threshold)}}</strong></div>
              <div><small>告警样本</small><strong>{{clock(detail.signal.sample_time)}}</strong></div>
            </div>
            <div class="drawer-actions">
              <a-button v-if="detail.source==='monitor'&&detail.host_id" size="small" @click="trendOpen=true">指标趋势</a-button>
              <a-button v-if="detail.host_id" size="small" @click="router.push({name:'HostWorkbench',params:{id:detail.host_id}})">主机工作台</a-button>
              <a-button v-if="detail.source_task_id" size="small" @click="goTask(detail.source_task_id)">来源报告</a-button>
              <a-button v-if="canOperate&&detail.source==='inspection'&&detail.host_id" size="small" @click="recheck">重新巡检</a-button>
            </div>
            <a-alert v-if="detail.manual_required" type="error" show-icon message="自动处理失败或恢复未确认，等待人工接手" style="margin:0 0 10px">
              <template v-if="canOperate" #action><a-button size="small" :loading="saving" @click="takeOver">确认接手</a-button></template>
            </a-alert>
            <alert-investigation v-if="detail.host_id" :finding="detail" :workspace="workspace" :trees="trees"
              :loading="workLoading" :acting="acting" :can-operate="canOperate" :admin="admin"
              @reload="refreshDrawer" @diagnose="treeId=>run('diagnosis',treeId)" @repair="confirmRepair" @authorize-automatic="openOnce" @approve-workflow="confirmWorkflow" @take-over="takeOver"/>
            <a-collapse class="detail-panels" :active-key="panels" @change="panels=$event">
              <a-collapse-panel key="evidence" header="原始证据与触发时间">
                <p class="ops-muted">首次 {{time(detail.first_seen)}} · 最近 {{time(detail.last_seen)}}</p>
                <p v-if="detail.recovered_at" class="ops-muted">确认恢复 {{time(detail.recovered_at)}}</p>
                <p v-if="detail.signal?.metric" class="ops-muted">触发持续 {{detail.signal.duration}} 秒 · 恢复保持 {{detail.signal.recovery_seconds}} 秒</p>
                <pre class="evidence">{{detail.evidence||'暂无原始证据'}}</pre>
              </a-collapse-panel>
              <a-collapse-panel v-if="canOperate" key="handling" header="分派、维护与处理结论">
                <a-form layout="vertical">
                  <div class="handling-grid">
                    <a-form-item label="处理状态"><a-select v-model:value="form.state" :options="options(handling)"/></a-form-item>
                    <a-form-item label="负责人"><a-input v-model:value="form.assignee" :maxlength="100"/></a-form-item>
                  </div>
                  <a-alert v-if="detail.condition==='active'&&form.state==='resolved'" type="warning" show-icon message="故障仍在触发，关闭只更新人工处理状态。"/>
                  <a-form-item label="维护窗口" :extra="isSilenced(detail)?'维护至 '+time(detail.silenced_until):'维护期间暂停自动修复'"><a-select v-model:value="form.silence_minutes" allow-clear placeholder="保持现有设置" :options="silenceOptions"/></a-form-item>
                  <a-form-item label="处理备注"><a-textarea v-model:value="form.note" :rows="3" :maxlength="2000"/></a-form-item>
                  <a-button type="primary" :loading="saving" @click="save">保存处理记录</a-button>
                </a-form>
              </a-collapse-panel>
              <a-collapse-panel key="timeline" :header="'处理时间线（'+(detail.notes?.length||0)+'）'">
                <a-timeline v-if="detail.notes?.length"><a-timeline-item v-for="(note,index) in detail.notes" :key="index">
                  <small class="ops-muted">{{time(note.created_at)}} · {{note.actor__username||'系统'}}</small><div>{{note.text}}</div>
                </a-timeline-item></a-timeline>
                <span v-else class="ops-muted">暂无处理记录</span>
              </a-collapse-panel>
            </a-collapse>
          </template>
        </a-spin>
      </a-drawer>
      <a-modal v-if="repairConfirmOpen" v-model:open="repairConfirmOpen" :title="repairConfirmation?.once?'仅本次自动处理':repairConfirmation?.taskId?'授权执行本次修复':'发起本次修复授权'"
        :ok-text="repairConfirmation?.once?'授权本次处理':repairConfirmation?.taskId?'确认授权':'提交申请'" cancel-text="取消" :z-index="1200"
        :confirm-loading="confirming||acting" @ok="submitRepairConfirmation">
        <p v-if="repairConfirmation?.action?.name"><strong>{{repairConfirmation.action.name}}</strong></p>
        <p>{{repairConfirmation?.action?.impact||'执行器会在派发前复核告警、目标和证据。'}}</p>
        <template v-if="repairConfirmation?.once">
          <p>授权只对当前告警轮次有效。</p>
          <a-checkbox v-model:checked="escalateOnce">失败或恢复未确认时，站内转人工</a-checkbox>
        </template>
      </a-modal>
      <a-modal v-if="trendOpen" v-model:open="trendOpen" title="告警关联主机趋势" width="950px" :footer="null" destroy-on-close>
        <metric-chart v-if="detail&&trendOpen" :host-id="detail.host_id" :initial-metric="detail.signal?.metric"/>
      </a-modal>
    </section>
  </a-config-provider>
</template>

<script setup>
import {computed,defineAsyncComponent,onBeforeUnmount,onMounted,ref,watch} from 'vue'
import {useRoute,useRouter} from 'vue-router'
import {useStore} from 'vuex'
import {Modal,Segmented as ASegmented,message,theme} from 'ant-design-vue'
import {FilterOutlined,FullscreenExitOutlined,FullscreenOutlined,ReloadOutlined,SettingOutlined} from '@ant-design/icons-vue'
import AlertInvestigation from '@/components/AlertInvestigation.vue'
import RepairApprovals from '@/components/RepairApprovals.vue'
import api from '@/api'
import {options,time} from '@/utils/ops'
import {severityColor,severityNames,stateColor} from '@/utils/signals'

const MetricChart=defineAsyncComponent(()=>import('@/components/MetricChart.vue'))
const router=useRouter(),route=useRoute(),store=useStore()
const admin=computed(()=>Boolean(store.getters.userInfo?.is_superuser))
const approvals=computed(()=>route.query.view==='approvals'&&canOperate.value&&!focus.value)
const canOperate=computed(()=>store.getters.canOperate)
const focus=ref(false), advanced=ref(false)
const focusTheme=computed(()=>focus.value?{
  algorithm:theme.darkAlgorithm,
  token:{colorBgContainer:'#142138',colorBgElevated:'#192945',colorBorder:'#3a5070',
    colorPrimary:'#8daeff',colorTextPlaceholder:'#93a9ca'},
  components:{Table:{headerBg:'#1b2b46',rowHoverBg:'#1d304f',borderColor:'#304360'},
    Segmented:{trackBg:'#172944',itemSelectedBg:'#294879'}},
}:undefined)
const view=ref('inbox'), filters=ref({}), draftSearch=ref(''), draftBusiness=ref('')
const filterCount=computed(()=>['source','condition','state','severity','environment','business'].filter(key=>filters.value[key]).length)
const scopeLabel=computed(()=>filters.value.host_id?'当前主机范围':'当前授权范围')
const page=ref(1), total=ref(0), rows=ref([]), overview=ref({}), loading=ref(false), updatedAt=ref(null),fetchFailed=ref(false)
const drawer=ref(false), findingId=ref(), detail=ref(null), detailLoading=ref(false), workspace=ref(null),workLoading=ref(false)
const escalateOnce=ref(true)
const repairConfirmOpen=ref(false),repairConfirmation=ref(null),confirming=ref(false)
const trees=ref([]), panels=ref([]), form=ref({}), saving=ref(false), acting=ref(false),trendOpen=ref(false)
let listRequest,timer,detailSequence=0,workSequence=0

const environments={test:'测试',staging:'验收',production:'生产',other:'其他'}
const sources={monitor:'主机监控',service:'HTTP 探测',inspection:'健康巡检',external:'外部告警'}
const conditions={active:'触发中',recovered:'已恢复',unknown:'待确认'}
const handling={open:'待处理',acknowledged:'已确认',processing:'处理中',resolved:'已关闭'}
const metricNames={up:'采集端点',cpu:'CPU 使用率',memory:'内存使用率',disk:'磁盘使用率',inode:'inode 使用率',load:'一分钟负载',collector_failures:'失败采集器数量'}
const tiles=[
  {key:'inbox',label:'待处理',hint:'尚未关闭且未恢复',tone:'indigo'},
  {key:'critical',label:'严重触发中',hint:'按严重级别快速排查',tone:'red'},
  {key:'active',label:'全部触发中',hint:'包含已确认和处理中',tone:'amber'},
  {key:'recovered',label:'近 24 小时恢复',hint:'监控已确认恢复',tone:'green'},
]
const views=[...tiles.map(tile=>({value:tile.key,label:tile.label})),{value:'all',label:'全部记录'}]
const columns=[{title:'告警 / 资产',key:'title',width:300},{title:'故障状态',key:'condition',width:140},
  {title:'处理进度',key:'handling',width:115},{title:'最近触发',key:'last',width:135},{title:'操作',key:'action',width:100}]
const silenceOptions=[{value:0,label:'清除维护标记'},{value:60,label:'维护 1 小时'},{value:240,label:'维护 4 小时'},{value:1440,label:'维护 24 小时'}]

function clock(value){return value?new Date(value).toLocaleTimeString('zh-CN',{hour12:false}):'—'}
function isSilenced(record){return record.silenced_until&&new Date(record.silenced_until)>new Date()}
function signalValue(value){return typeof value==='number'?Number(value.toFixed(2)):value??'无有效数据'}
async function load(){
  listRequest?.abort()
  const request=new AbortController()
  listRequest=request;loading.value=true
  const bucket=view.value==='critical'?'active':view.value
  const severity=view.value==='critical'?'critical':filters.value.severity
  try{
    const {data}=await api.get('/host/findings',{signal:request.signal,params:{...filters.value,severity,bucket,
      page:page.value,overview:1,sort:view.value==='all'||view.value==='recovered'?'recent':'priority'}})
    if(request!==listRequest)return
    rows.value=data.results;total.value=data.count;overview.value=data.overview||{};updatedAt.value=data.updated_at;fetchFailed.value=false
  }catch(error){
    if(request===listRequest&&error.code!=='ERR_CANCELED')fetchFailed.value=true
  }finally{if(request===listRequest)loading.value=false}
}
function search(){page.value=1;load()}
function choose(key){view.value=key;search()}
function onPage(pagination){page.value=pagination.current;load()}
function applySearch(value){filters.value.search=(value||'').trim();search()}
function applyBusiness(value){filters.value.business=(value||'').trim();search()}
function toggleManual(){filters.value.manual=filters.value.manual?undefined:'1';if(filters.value.manual)view.value='inbox';search()}
function reset(){draftSearch.value='';draftBusiness.value='';filters.value={host_id:route.query.host_id};search()}
watch(()=>[route.query.host_id,route.query.source],([host_id,source])=>{
  filters.value={host_id,source};draftSearch.value='';draftBusiness.value='';view.value='inbox';search()
},{immediate:true})

async function open(row){
  findingId.value=row.id;detail.value=null;workspace.value=null;panels.value=[];drawer.value=true
  await reloadDetail()
  if(findingId.value!==row.id||!drawer.value||!detail.value?.host_id)return
  if(canOperate.value&&!trees.value.length)api.get('/mtask/diagnostic-trees').then(({data})=>{trees.value=data.results}).catch(()=>{})
}
async function reloadDetail(resetForm=true){
  const seq=++detailSequence
  const id=findingId.value
  if(!id)return
  if(resetForm)detailLoading.value=true
  try{
    const {data}=await api.get('/host/findings/'+id)
    if(seq!==detailSequence||!drawer.value)return
    detail.value=data
    if(resetForm)form.value={state:data.state,assignee:data.assignee,note:''}
    if(!workspace.value&&data.host_id&&!workLoading.value)loadWorkspace()
  }catch{ /* common API errors shown by client */ }
  finally{if(seq===detailSequence)detailLoading.value=false}
}
async function loadWorkspace(){
  const seq=++workSequence
  const id=findingId.value
  if(!id||!detail.value?.host_id)return
  workLoading.value=true
  try{
    const {data}=await api.get('/mtask/incidents/'+id+'/runs',{params:{workspace:1}})
    if(seq===workSequence&&drawer.value)workspace.value=data
  }catch{ /* common API errors shown by client */ }
  finally{if(seq===workSequence)workLoading.value=false}
}
watch(()=>route.query.finding,(value)=>{
  if(typeof value==='string'&&/^\d+$/.test(value)&&Number.isSafeInteger(Number(value))&&Number(value)>0)open({id:Number(value)})
},{immediate:true})
function closeDrawer(){
  drawer.value=false;repairConfirmOpen.value=false;findingId.value=undefined
  detailSequence++;workSequence++;detailLoading.value=false;workLoading.value=false
  if(route.query.finding){const {finding:_,...query}=route.query;router.replace({query})}
}
function refreshDrawer(){reloadDetail(false);loadWorkspace()}
async function run(mode,tree_id,extras={}){
  if(acting.value)return
  const id=findingId.value
  acting.value=true
  try{
    await api.post('/mtask/incidents/'+id+'/runs',{mode,tree_id,confirmed:true,...extras})
    message.success(({repair:'已提交修复审批',repair_once:'已授权本次自动处理'})[mode]||'已提交只读排查')
    if(findingId.value===id)refreshDrawer()
    return true
  }catch{if(findingId.value===id)loadWorkspace()}
  finally{acting.value=false}
}
function openOnce(){
  escalateOnce.value=true
  repairConfirmation.value={once:true,action:workspace.value?.repair?.action}
  repairConfirmOpen.value=true
}
async function takeOver(){
  const id=findingId.value
  if(!id||saving.value)return
  saving.value=true
  try{
    const body=detail.value.manual_required?{ack_follow_up:true}:{
      state:'processing',assignee:detail.value.assignee||store.getters.userInfo?.username||'',note:'人工接手，处理后复检。',
    }
    await api.patch('/host/findings/'+id,body)
    message.success('已确认人工接手')
    if(findingId.value===id)refreshDrawer()
    load()
  }catch{ /* common API feedback */ }
  finally{saving.value=false}
}
function confirmWorkflow(taskId){
  repairConfirmation.value={taskId,action:workspace.value?.pending_approval?.action||workspace.value?.workflow?.action}
  repairConfirmOpen.value=true
}
function confirmRepair(){
  repairConfirmation.value={action:workspace.value?.repair?.action}
  repairConfirmOpen.value=true
}
async function submitRepairConfirmation(){
  if(confirming.value||acting.value)return
  const taskId=repairConfirmation.value?.taskId
  confirming.value=true
  try{
    if(taskId){
      await api.post('/mtask/tasks/'+taskId+'/action',{action:'approve',confirmed:true})
      message.success('审批已通过，等待执行器处理')
      repairConfirmOpen.value=false
      refreshDrawer()
    }else{
      const once=repairConfirmation.value?.once
      const submitted=await run(once?'repair_once':'repair',undefined,once?{escalate_on_failure:escalateOnce.value}:{})
      if(submitted)repairConfirmOpen.value=false
    }
  }catch{ /* shared API feedback */ }
  finally{confirming.value=false}
}
async function save(){
  saving.value=true
  try{await api.patch('/host/findings/'+findingId.value,form.value);message.success('处理记录已保存');await reloadDetail();loadWorkspace();load()}
  catch{ /* common API errors shown by client */ }
  finally{saving.value=false}
}
function goTask(id){router.push({name:'TaskCenter',query:{task:String(id)}})}
function recheck(){
  const selected=detail.value
  Modal.confirm({title:'重新检查当前主机健康状态',content:'沿用来源任务的只读检查方案。',okText:'开始巡检',onOk:async()=>{
    const plan=selected.source_task_id?(await api.get('/mtask/tasks/'+selected.source_task_id)).data.options:{profile:'health'}
    const {data}=await api.post('/mtask/tasks',{kind:'inspection',host_ids:[selected.host_id],options:plan,confirmed:true})
    goTask(data.id)
  }})
}
function onKey(event){if(event.key==='Escape'&&!drawer.value&&!trendOpen.value)focus.value=false}
onMounted(()=>{
  window.addEventListener('keydown',onKey)
  let cycles=0
  timer=setInterval(()=>{
    if(document.hidden)return
    if(drawer.value&&detail.value?.host_id&&!workLoading.value&&cycles%2===0)refreshDrawer()
    if(cycles++%6===0&&!loading.value)load()
  },5000)
})
onBeforeUnmount(()=>{listRequest?.abort();clearInterval(timer);window.removeEventListener('keydown',onKey)})
</script>

<style scoped>
.alerts-page { min-width:0; }
.queue-tabs { display:flex; gap:8px; margin:16px 0 8px; }
.queue-tabs a { padding:7px 13px; border-radius:7px; color:var(--text); }
.queue-tabs a.selected { background:var(--surface-alt); border:1px solid var(--line); font-weight:600; }
.alerts-page.focus { position:fixed; z-index:30; inset:0; overflow:auto; overscroll-behavior:contain; padding:clamp(12px,2vw,32px); background:#0b1425;
  --surface:#142138;--surface-alt:#182740;--line:#2d405e;--muted:#a9bbd7;--text:#edf4ff;--accent:#98bbff; }
.wall { background:radial-gradient(ellipse at 90% -45%,#274476 0%,transparent 65%),#101d33; border:1px solid #2d4061; border-radius:17px; color:#f0f5ff; padding:clamp(15px,2.5vw,30px); }
.wall-top { display:flex; align-items:flex-start; justify-content:space-between; flex-wrap:wrap; gap:15px; }
.wall-eyebrow { display:flex; align-items:center; gap:8px; color:#a9bfe1; font-size:10px; font-weight:750; letter-spacing:.17em; margin:0 0 7px; }
.wall-eyebrow span { width:7px;height:7px;border-radius:50%;background:#73ddc2; }
.wall h1 { font-size:clamp(24px,3vw,38px); font-weight:740; letter-spacing:-.045em; margin:0; }
.wall-description { margin:8px 0 0; color:#b5c5df; }
.wall-actions { display:flex; align-items:center; flex-wrap:wrap; gap:8px; }
.wall-actions button { display:flex; align-items:center; gap:6px; color:#dce9ff; background:#192c4a; border:1px solid #384e70; padding:7px 11px; border-radius:7px; cursor:pointer; }
.wall-actions button:hover,.wall-actions button:focus-visible { border-color:#8cadf5; }
.wall-actions .focus-button { background:#294e8c; }
.wall-clock { display:flex; align-items:center; gap:6px; color:#b5c5df; font-size:12px; }
.wall-clock i { width:6px;height:6px;border-radius:50%;background:#72e0c2; }
.wall-clock i.problem { background:#ffa8a8; }
.tiles { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; margin-top:22px; }
.tile { position:relative; overflow:hidden; text-align:left; color:#f1f6ff; background:#172945; border:1px solid #344b70; border-radius:11px; padding:13px 15px; cursor:pointer; --tone:#9bb8ff; }
.tile.red { --tone:#ff9aa9; }.tile.amber { --tone:#ffd08c; }.tile.green { --tone:#83e3c6; }
.tile:hover,.tile.picked { border-color:var(--tone); }
.tile-title { display:flex; align-items:center; gap:7px; color:#d8e5fa; font-size:13px; }
.tile .mark { width:7px;height:7px;border-radius:50%;background:var(--tone); }
.tile strong { display:block; font:720 clamp(24px,3.1vw,35px) ui-monospace,monospace; margin:7px 0 4px; color:var(--tone); }
.tile small { color:#b0c4e1; }
.queue { margin-top:17px; padding:17px 16px 4px; }
.queue-heading { display:flex; align-items:center; justify-content:space-between; gap:10px; flex-wrap:wrap; }
.queue-heading h2 { color:var(--text); margin:0; font-size:21px; }
.policy-link { color:var(--accent); padding:5px 7px; }
.queue-controls { display:flex; flex-wrap:wrap; align-items:center; gap:10px; justify-content:space-between; margin:13px 0 12px; }
.queue-controls :deep(.ant-input-search) { width:min(100%,260px); }
.advanced { display:grid; grid-template-columns:repeat(auto-fit,minmax(122px,1fr)); gap:8px; padding:11px; background:var(--surface-alt); border-radius:9px; border:1px solid var(--line); margin:0 0 12px; }
.advanced :deep(.ant-select) { min-width:0; }
.alert-title { display:flex; align-items:flex-start; gap:9px; }
.alert-title small,.row-note { color:var(--muted); display:block; margin-top:3px; }
.severity-dot { flex:none; width:7px;height:7px;border-radius:50%;background:#5984e9;margin-top:8px; }
.severity-dot.critical { background:#e75069; }.severity-dot.warning { background:#d98619; }
.title-link { text-align:left; color:var(--text); padding:0; border:0; background:none; cursor:pointer; font-weight:650; }
.title-link:hover { color:var(--accent); text-decoration:underline; }
.drawer-heading h2 { margin:10px 0 4px; font-size:23px; overflow-wrap:anywhere; }
.signal-card { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:8px; padding:12px; border:1px solid var(--line); border-radius:10px; background:var(--surface-alt); }
.signal-card small,.signal-card strong { display:block; overflow-wrap:anywhere; }.signal-card small{color:var(--muted);margin-bottom:4px;}
.drawer-actions { display:flex; flex-wrap:wrap; gap:7px; margin:12px 0; }
.detail-panels { margin-top:13px; }
.evidence { white-space:pre-wrap; overflow-wrap:anywhere; max-height:270px; overflow:auto; background:var(--surface-alt); border-radius:8px; padding:10px; }
.handling-grid { display:grid; grid-template-columns:1fr 1fr; gap:10px; }
@media(max-width:700px){.tiles{grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.queue{padding:11px 10px 4px}.queue-controls{overflow:auto}.tile{padding:10px}}
</style>
