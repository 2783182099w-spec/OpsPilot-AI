<template>
  <section class="release-page">
    <header class="overview">
      <div class="hero">
        <div><p class="hero-eyebrow"><i></i> OPSPILOT / RELEASE CONTROL</p><h1>发布流水线</h1>
          <p>从制品核验、首批验证到分批放行，跟踪每次变更与回退。</p></div>
        <div class="hero-actions">
          <span class="worker"><i :class="{ online:workerActive }"></i>{{workerActive===null?'检查执行器':workerActive?'执行器在线':'执行器离线'}}</span>
          <a-button :loading="loading" @click="load">刷新</a-button>
          <a-button @click="guideOpen=true">发布指引</a-button>
          <a-button v-if="admin" @click="appsOpen=true">应用与 CI</a-button>
          <a-button v-if="admin" type="primary" :disabled="!readyApps.length" @click="openNew"><template #icon><plus-outlined/></template>新建发布</a-button>
        </div>
      </div>
      <div class="tiles">
        <button v-for="tile in tiles" :key="tile.key" type="button" :class="[tile.key,{chosen:filter.status===tile.key}]" :aria-pressed="filter.status===tile.key" @click="choose(tile.key)">
          <span>{{tile.label}}</span><strong>{{summary[tile.key]??'—'}}</strong><small>{{tile.note}}</small>
        </button>
      </div>
    </header>

    <a-alert v-if="admin&&!apps.length&&!loading&&!loadError" type="info" show-icon class="banner"
      message="尚未登记发布应用。点击「应用与 CI」登记目标主机、systemd 单元和可信制品来源。"/>
    <a-alert v-if="loadError" type="warning" show-icon class="banner" message="发布记录刷新失败，已保留上一次加载的数据。"/>
    <div class="ops-panel directory">
      <div class="directory-header">
        <div><p class="ops-eyebrow">RELEASE HISTORY</p><h2>发布记录</h2></div>
        <a-space wrap>
          <a-select v-model:value="filter.application" :options="filterApplications" placeholder="全部应用" aria-label="筛选发布应用" allow-clear style="width:145px" @change="search"/>
          <a-select v-model:value="filter.status" :options="statusOptions" placeholder="全部状态" aria-label="筛选发布状态" allow-clear style="width:145px" @change="search"/>
          <a-input-search v-model:value="draftSearch" placeholder="搜索应用、版本或提交人" allow-clear @search="applySearch"/>
        </a-space>
      </div>
      <a-table row-key="id" :columns="columns" :data-source="rows" :loading="loading&&!rows.length" :scroll="{x:795}"
        :pagination="pagination" @change="onPage">
        <template #emptyText><a-empty description="当前筛选范围内暂无发布记录"/></template>
        <template #bodyCell="{column,record}">
          <template v-if="column.key==='version'">
            <button class="title-link" type="button" @click="openDetail(record.id)">{{record.application}} · {{record.version}}</button>
            <small class="secondary">#{{record.id}} · {{record.rollback_of?'回退':'发布'}} · {{environments[record.environment]||'—'}}</small>
          </template>
          <template v-else-if="column.key==='state'"><a-tag :color="statusColor(record.status)">{{statusLabel(record.status)}}</a-tag></template>
          <template v-else-if="column.key==='progress'"><a-progress :percent="percent(record)" :show-info="false" :status="progressStatus(record.status)" size="small"/><small class="secondary">{{record.done}} / {{record.total}} 台已结算</small></template>
          <template v-else-if="column.key==='time'">{{time(record.created_at)}}<small class="secondary">{{record.actor}}</small></template>
          <template v-else-if="column.key==='actions'"><a-button size="small" type="link" @click="openDetail(record.id)">查看流水线</a-button></template>
        </template>
      </a-table>
    </div>

    <release-quick-start v-model="guideOpen" :apps="apps" :admin="Boolean(admin)" @applications="appsOpen=true"/>
    <release-applications v-if="admin" v-model="appsOpen" :legacy="apps" @updated="load"/>

    <a-drawer :open="Boolean(detailId)" title="发布详情" :width="'min(805px, 100vw)'" @close="closeDetail">
      <a-spin :spinning="detailLoading&&!detail">
        <a-result v-if="!detail&&!detailLoading" status="warning" title="无法加载发布详情">
          <template #extra><a-button @click="loadDetail">重试</a-button></template>
        </a-result>
        <template v-if="detail">
          <header class="detail-header">
            <div><a-space><a-tag :color="statusColor(detail.status)">{{statusLabel(detail.status)}}</a-tag><a-tag>{{environments[detail.environment]||'未知环境'}}</a-tag><span class="ops-muted">#{{detail.id}}</span></a-space>
              <h2>{{detail.application}} · {{detail.version}}</h2><p class="ops-muted">{{detail.reason||'—'}}</p></div>
            <a-button size="small" :loading="detailLoading" @click="loadDetail">刷新进度</a-button>
          </header>
          <a-alert v-if="detail.stop_reason||detail.cancel_requested" show-icon :type="['failed','interrupted'].includes(detail.status)?'error':'warning'"
            :message="detail.stop_reason||(detail.cancel_requested?'已请求停止。执行中的远端操作需核对结果。':'')" class="spaced"/>
          <a-steps size="small" class="spaced" :items="detailSteps"/>
          <div v-if="detail.actions.approve||detail.actions.release||detail.actions.cancel||detail.actions.rollback" class="controls ops-panel spaced">
            <a-button v-if="detail.actions.approve" type="primary" @click="ask('approve')">批准发布</a-button>
            <a-button v-if="detail.actions.reject" danger @click="ask('reject')">驳回</a-button>
            <a-button v-if="detail.actions.release" type="primary" @click="ask('release')">核对并放行</a-button>
            <a-button v-if="detail.actions.cancel" danger @click="ask('cancel')">停止或撤销</a-button>
            <a-button v-if="detail.actions.rollback" @click="openRollback">准备回退</a-button>
            <router-link :to="{name:'TaskCenter',query:{task:String(detail.task_id)}}">任务 #{{detail.task_id}}</router-link>
          </div>
          <div v-else class="spaced"><router-link :to="{name:'TaskCenter',query:{task:String(detail.task_id)}}">查看任务 #{{detail.task_id}}</router-link>
            <span v-if="['awaiting_approval','awaiting_release'].includes(detail.status)" class="ops-muted review-note"> 等待其他管理员处理</span></div>
          <section class="results-section">
            <div class="directory-header"><div><h3>主机执行结果</h3><small class="ops-muted">{{detail.done}} / {{detail.total}} 台已结算</small></div>
              <a-select v-model:value="resultFilter" :options="hostFilters" aria-label="筛选主机结果" style="width:145px" @change="changeResultFilter"/>
            </div>
            <a-table :columns="hostColumns" :data-source="detail.hosts.results" :loading="detailLoading"
              :row-key="row=>row.host_info?.id" :scroll="{x:605}" size="small"
              :pagination="{current:resultPage,total:detail.hosts.count,pageSize:15,showSizeChanger:false}" @change="page=>{resultPage=page.current;loadDetail()}">
              <template #bodyCell="{column,record}">
                <template v-if="column.key==='name'"><strong>{{record.host_info?.name}}</strong><small class="secondary">{{record.host_info?.ip_addr}}<span v-if="record.batch"> · 第 {{record.batch}} 批</span></small></template>
                <template v-else-if="column.key==='status'"><a-tag :color="statusColor(record.status)">{{statusLabel(record.status)}}</a-tag><small class="secondary">{{impact(record)}}</small></template>
                <template v-else-if="column.key==='evidence'"><details v-if="record.stages?.length||record.res_data"><summary>阶段与输出</summary>
                  <ul class="stage-log"><li v-for="stage in record.stages" :key="stage.name"><a-tag :color="statusColor(stage.status)">{{stageNames[stage.name]||stage.name}}</a-tag>{{stage.detail}}</li></ul>
                  <pre v-if="record.res_data">{{record.res_data}}</pre></details><span v-else class="ops-muted">—</span></template>
              </template>
            </a-table>
          </section>
          <a-collapse class="spaced">
            <a-collapse-panel key="artifact" header="制品、健康检查与审核记录">
              <a-descriptions size="small" :column="1">
                <a-descriptions-item label="制品地址">{{detail.url}}</a-descriptions-item>
                <a-descriptions-item label="SHA-256"><code>{{detail.sha256}}</code></a-descriptions-item>
                <a-descriptions-item label="健康检查">{{detail.policy.unit||'—'}} · :{{detail.policy.port}}{{detail.policy.path}}</a-descriptions-item>
                <a-descriptions-item label="发起">{{detail.actor}} · {{time(detail.created_at)}}</a-descriptions-item>
                <a-descriptions-item label="审批">{{detail.approved_by||'—'}} · {{time(detail.approved_at)}}</a-descriptions-item>
                <a-descriptions-item label="放行">{{detail.released_by||'—'}} · {{time(detail.released_at)}}</a-descriptions-item>
                <a-descriptions-item v-if="detail.restore_from" label="回退关系">来源发布 #{{detail.rollback_of}} · 恢复到 #{{detail.restore_from}}</a-descriptions-item>
              </a-descriptions>
            </a-collapse-panel>
          </a-collapse>
        </template>
      </a-spin>
    </a-drawer>

    <a-drawer v-model:open="editor" :width="'min(650px, 100vw)'" :title="form.rollback_of ? '准备回退' : '新建发布'">
      <a-form layout="vertical" :disabled="busy">
        <a-form-item label="发布应用"><a-select v-model:value="form.application" :disabled="!!form.rollback_of" :options="appOptions" @change="changeApp"/></a-form-item>
        <div v-if="application" class="ops-panel policy">
          <a-tag :color="application.environment==='production'?'red':'blue'">{{environments[application.environment]}}</a-tag>
          <span>{{application.unit}} · :{{application.port}}{{application.path}}</span>
          <a-tag v-if="application.busy" color="orange">已有运行或待审核发布</a-tag>
        </div>
        <template v-if="form.rollback_of">
          <a-alert v-if="!rollbackChoices.length" type="warning" show-icon class="spaced" message="没有可用的回退版本。候选要求历史版本在相应主机验证通过，且主机仍属于当前发布范围。"/>
          <a-form-item v-else label="可回退版本"><a-select v-model:value="form.restore_from" :options="previousOptions" @change="changeApp"/></a-form-item>
        </template>
        <template v-else>
          <a-form-item v-if="artifacts.length" label="CI 上报制品" extra="选择后自动使用该记录的版本、下载地址和摘要。">
            <a-select v-model:value="form.artifact_id" :options="artifactOptions" show-search option-filter-prop="label"
              allow-clear placeholder="选择 CI 版本，或在下方手动填写" @change="applyArtifact"/>
          </a-form-item>
          <a-form-item label="制品版本"><a-input v-model:value="form.version" :disabled="!!form.artifact_id" :maxlength="80" placeholder="如 1.8.2" @change="invalidate"/></a-form-item>
          <a-form-item label="制品 HTTPS 地址" :extra="application?.origin"><a-input v-model:value="form.url" :disabled="!!form.artifact_id" :maxlength="220" @change="invalidate"/></a-form-item>
          <a-form-item label="SHA-256"><a-input v-model:value="form.sha256" :disabled="!!form.artifact_id" :maxlength="64" placeholder="64 位小写十六进制" @change="invalidate"/></a-form-item>
        </template>
        <a-form-item label="目标主机" extra="第一台为首批验证主机，可在下方调整。">
          <a-select v-model:value="form.host_ids" mode="multiple" :options="hostOptions" option-filter-prop="label" placement="topLeft" @change="invalidate"/>
        </a-form-item>
        <div v-if="form.host_ids?.length>1" class="ordered">
          <span>首批主机</span><a-select :value="form.host_ids[0]" :options="hostOptions.filter(item=>form.host_ids.includes(item.value))" @change="chooseCanary"/>
        </div>
        <a-form-item label="变更原因"><a-textarea v-model:value="form.reason" :maxlength="500" :rows="3" @change="invalidate"/></a-form-item>
        <div v-if="plan" class="ops-panel plan">
          <p class="ops-eyebrow">PREVIEW / 待审核</p><strong>{{plan.application}} · {{plan.artifact.version}}</strong>
          <p>首批 {{plan.targets[0]?.name}} · 目标 {{plan.targets.length}} 台 · 每批最多 {{plan.strategy.batch_size}} 台 · 并发 {{plan.concurrency}}</p>
          <p>失败达到 {{plan.strategy.failure_limit}} 台时停止派发 · 健康检查 {{plan.policy.unit}} :{{plan.policy.port}}{{plan.policy.path}}</p>
          <code>{{plan.artifact.sha256}}</code>
          <a-alert v-if="plan.warnings.length" type="warning" :message="plan.warnings.join('；')" class="spaced"/>
          <a-alert v-if="plan.application_busy" type="warning" message="已有未结束的变更，当前不可提交。" class="spaced"/>
        </div>
        <a-space><a-button :loading="busy" :disabled="!canPreview" @click="preview">生成预览</a-button><a-button type="primary" :disabled="!plan||plan.application_busy" :loading="busy" @click="submit">提交审批</a-button></a-space>
      </a-form>
    </a-drawer>

    <a-modal v-model:open="actionOpen" :title="actionText[action]?.title" :ok-text="actionText[action]?.ok"
      :ok-type="['cancel','reject'].includes(action)?'danger':'primary'" :confirm-loading="acting" @ok="perform">
      <p>{{actionText[action]?.description}}</p>
      <a-textarea v-if="action==='reject'" v-model:value="rejectReason" :maxlength="500" placeholder="填写驳回原因" :rows="3"/>
    </a-modal>
  </section>
</template>

<script setup>
import {computed,onBeforeUnmount,onMounted,ref} from 'vue'
import {useRoute} from 'vue-router'
import {useStore} from 'vuex'
import {message} from 'ant-design-vue'
import {PlusOutlined} from '@ant-design/icons-vue'
import api from '@/api'
import ReleaseApplications from '@/components/ReleaseApplications.vue'
import ReleaseQuickStart from '@/components/ReleaseQuickStart.vue'
import {color,states,time} from '@/utils/ops'

const store=useStore(), route=useRoute()
const admin=computed(()=>store.getters.userInfo?.is_superuser)
const apps=ref([]),rows=ref([]),summary=ref({}),workerActive=ref(null),loading=ref(false),loadError=ref(false),total=ref(0),page=ref(1)
const filter=ref({}),draftSearch=ref('')
const appsOpen=ref(false),guideOpen=ref(false),artifacts=ref([])
const editor=ref(false),hosts=ref([]),busy=ref(false),plan=ref(null),form=ref({}),rollbackChoices=ref([])
const detailId=ref(null),detail=ref(null),detailLoading=ref(false),resultPage=ref(1),resultFilter=ref('all')
const actionOpen=ref(false),action=ref(''),acting=ref(false),rejectReason=ref('')
let listRequest,detailRequest,timer
const environments={test:'测试',staging:'验收',production:'生产'}
const tiles=[{key:'awaiting_approval',label:'待审批',note:'等待独立管理员确认'},
  {key:'in_progress',label:'进行中',note:'排队或正在执行'},
  {key:'awaiting_release',label:'待放行',note:'首批通过，等待扩大范围'},
  {key:'needs_review',label:'待核查',note:'失败或中断的发布'}]
const statusOptions=[...tiles.map(item=>({value:item.key,label:item.label})),...['completed','cancelled','rejected'].map(value=>({value,label:states[value]}))]
const columns=[{title:'应用 / 版本',key:'version',width:245},{title:'状态',key:'state',width:115},{title:'执行进度',key:'progress',width:140},
  {title:'提交',key:'time',width:165},{title:'操作',key:'actions',width:110}]
const hostColumns=[{title:'主机 / 批次',key:'name',width:175},{title:'结果 / 变更状态',key:'status',width:190},{title:'证据',key:'evidence',width:230}]
const hostFilters=[{value:'all',label:'全部主机'},{value:'attention',label:'失败 / 待核查'},{value:'success',label:'验证成功'},
  {value:'running',label:'执行中'},{value:'queued',label:'等待派发'},{value:'skipped',label:'未派发'}]
const stageNames={queue:'排队',connect:'SSH 连接',upload:'上传脚本',preflight:'远端预检',execute:'执行',verify:'服务验证',cleanup:'清理'}
const actionText={
  approve:{title:'批准发布',ok:'确认批准',description:'批准后，执行器会在目标顺序的第一台主机执行制品校验、换版和健康检查。'},
  reject:{title:'驳回发布',ok:'确认驳回',description:'请填写原因，驳回后未派发的主机不会执行。'},
  release:{title:'放行后续批次',ok:'确认放行',description:'请先检查首批主机的健康验证与执行输出。确认后，剩余主机会按批次执行。'},
  cancel:{title:'停止或撤销发布',ok:'确认停止',description:'停止将阻止后续派发。已经执行的主机仍需核对远端状态，系统不会自动回退。'},
}
const pagination=computed(()=>({current:page.value,total:total.value,pageSize:15,showSizeChanger:false,showTotal:n=>'共 '+n+' 条'}))
const application=computed(()=>apps.value.find(app=>app.application===form.value.application))
const artifactOptions=computed(()=>artifacts.value.map(item=>({value:item.id,label:item.version+' · '+item.sha256.slice(0,12)})))
const readyApps=computed(()=>apps.value.filter(app=>!app.busy))
const appOptions=computed(()=>apps.value.map(app=>({label:app.application+' · '+environments[app.environment],value:app.application,disabled:app.busy})))
const filterApplications=computed(()=>[...new Set([...apps.value.map(app=>app.application),...rows.value.map(row=>row.application),filter.value.application].filter(Boolean))].sort().map(value=>({value,label:value})))
const previousOptions=computed(()=>rollbackChoices.value.map(row=>({value:row.id,label:'#'+row.id+' · '+row.version+' · '+row.host_ids.length+' 台可回退'})))
const hostOptions=computed(()=>{
  const ids=form.value.rollback_of?rollbackChoices.value.find(item=>item.id===form.value.restore_from)?.host_ids:application.value?.host_ids
  return hosts.value.filter(host=>ids?.includes(host.id)&&host.environment===application.value?.environment&&host.asset_status!=='retired')
    .map(host=>({value:host.id,label:host.name+' · '+host.ip_addr}))
})
const canPreview=computed(()=>form.value.application&&form.value.host_ids?.length&&form.value.reason?.trim()&&
  (form.value.rollback_of ? form.value.restore_from : form.value.version?.trim()&&form.value.url?.trim()&&form.value.sha256?.trim()))
const detailSteps=computed(()=>{
  const row=detail.value
  if(!row)return []
  const stopped=['failed','interrupted','cancelled','rejected'].includes(row.status)
  const firstPassed=row.rollout_phase==='remainder'||row.status==='awaiting_release'||row.status==='completed'
  const needsGate=row.total>1
  return [
    {title:'制品已登记',status:'finish'},
    {title:'独立审批',status:row.approved_at?'finish':stopped?'error':'process'},
    {title:'首批验证',status:firstPassed?'finish':row.approved_at?(stopped?'error':'process'):'wait'},
    {title:needsGate?'后续放行':'单机发布',status:!needsGate?(firstPassed?'finish':'wait'):row.released_at?'finish':row.status==='awaiting_release'?'process':'wait'},
    {title:'执行结束',status:row.status==='completed'?'finish':stopped?'error':row.rollout_phase==='remainder'?'process':'wait'},
  ]
})
const activeStates=['awaiting_approval','pending','running','awaiting_release']
function statusLabel(value){return ({completed:'发布完成',awaiting_release:'待首批放行',skipped:'未派发'})[value]||states[value]||value}
function statusColor(value){return ({awaiting_approval:'orange',awaiting_release:'gold',pending:'blue',interrupted:'orange',rejected:'red'})[value]||color(value)}
function progressStatus(value){return ['failed','interrupted'].includes(value)?'exception':value==='completed'?'success':'normal'}
function percent(record){return record.total?Math.round(100*record.done/record.total):0}
function impact(row){
  if(row.status==='success')return '制品切换和验证通过'
  if(row.may_have_run===false)return ['queued','running'].includes(row.status)?'尚未确认派发':'未执行制品脚本'
  return '可能已执行，需核查远端'
}
async function load(){
  listRequest?.abort()
  const request=new AbortController();listRequest=request
  loading.value=true
  try {
    const {data}=await api.get('/mtask/releases',{params:{...filter.value,page:page.value},signal:request.signal})
    if(listRequest!==request)return
    apps.value=data.applications;rows.value=data.results;total.value=data.count;summary.value=data.overview;workerActive.value=data.worker_active;loadError.value=false
  }catch(error){if(listRequest===request&&error.code!=='ERR_CANCELED')loadError.value=true}
  finally{if(listRequest===request)loading.value=false}
}
function search(){page.value=1;load()}
function choose(key){filter.value.status=filter.value.status===key?undefined:key;search()}
function applySearch(value){filter.value.search=(value||'').trim();search()}
function onPage(value){page.value=value.current;load()}
function openDetail(id){detailId.value=id;detail.value=null;resultPage.value=1;resultFilter.value='all';loadDetail()}
function closeDetail(){detailRequest?.abort();detailId.value=null;detail.value=null;detailLoading.value=false}
async function loadDetail(){
  if(!detailId.value)return
  detailRequest?.abort()
  const request=new AbortController();detailRequest=request
  detailLoading.value=true
  try{
    const {data}=await api.get('/mtask/releases/'+detailId.value,{params:{page:resultPage.value,result:resultFilter.value},signal:request.signal})
    if(detailRequest===request&&detailId.value)detail.value=data
  }catch{ /* shared API feedback */ }
  finally{if(detailRequest===request)detailLoading.value=false}
}
function changeResultFilter(){resultPage.value=1;loadDetail()}
function ask(type){action.value=type;rejectReason.value='';actionOpen.value=true}
async function perform(){
  if(!detail.value||acting.value)return
  if(action.value==='reject'&&!rejectReason.value.trim()){message.warning('请填写驳回原因');return}
  acting.value=true
  try{
    await api.post('/mtask/tasks/'+detail.value.task_id+'/action',{action:action.value,confirmed:true,reason:rejectReason.value.trim()})
    actionOpen.value=false;message.success('操作已记录');loadDetail();load()
  }catch{loadDetail()}
  finally{acting.value=false}
}
async function getHosts(){hosts.value=(await api.get('/host/')).data}
async function openNew(){
  try{await getHosts()}catch{return}
  rollbackChoices.value=[];plan.value=null
  form.value={application:readyApps.value[0]?.application,host_ids:[],reason:'',version:'',url:'',sha256:''}
  editor.value=true
  loadArtifacts()
}
async function openRollback(){
  if(!detail.value)return
  const current=detail.value
  try{
    const [result]=await Promise.all([api.get('/mtask/releases/'+current.id+'/rollback-options'),getHosts()])
    if(result.data.busy){message.warning('该应用已有待处理发布，结束后再准备回退');return}
    rollbackChoices.value=result.data.candidates
    plan.value=null
    form.value={application:current.application,host_ids:[],reason:'',rollback_of:current.id,restore_from:rollbackChoices.value[0]?.id}
    editor.value=true
  }catch{ /* shared API feedback */ }
}
function invalidate(){plan.value=null}
function changeApp(){
  form.value.host_ids=[]
  if(!form.value.rollback_of){form.value.artifact_id=undefined;applyArtifact();loadArtifacts()}
  invalidate()
}
async function loadArtifacts(){
  artifacts.value=[]
  const name=form.value.application
  if(!name||!application.value?.managed)return
  try{
    const {data}=await api.get('/mtask/release-applications/'+name+'/artifacts')
    if(form.value.application===name&&!form.value.rollback_of)artifacts.value=data
  }catch{ /* common API feedback */ }
}
function applyArtifact(){
  const item=artifacts.value.find(record=>record.id===form.value.artifact_id)
  Object.assign(form.value,{version:item?.version||'',url:item?.url||'',sha256:item?.sha256||''})
  invalidate()
}
function chooseCanary(id){form.value.host_ids=[id,...form.value.host_ids.filter(value=>value!==id)];invalidate()}
async function preview(){
  if(!canPreview.value||busy.value)return
  busy.value=true
  try{plan.value=(await api.post('/mtask/releases',form.value)).data}
  catch{plan.value=null}
  finally{busy.value=false}
}
async function submit(){
  if(!plan.value||busy.value)return
  busy.value=true
  try{
    const {data}=await api.post('/mtask/releases',{...form.value,token:plan.value.token,confirmed:true})
    message.success('已提交审批');editor.value=false;load();openDetail(data.id)
  }catch{plan.value=null}
  finally{busy.value=false}
}
onMounted(()=>{
  load()
  if(/^\d+$/.test(String(route.query.release||'')))openDetail(Number(route.query.release))
  let tick=0
  timer=setInterval(()=>{
    if(document.hidden)return
    if(detail.value&&activeStates.includes(detail.value.status)&&!detailLoading.value&&!acting.value)loadDetail()
    if(++tick%3===0&&!loading.value)load()
  },8000)
})
onBeforeUnmount(()=>{clearInterval(timer);listRequest?.abort();detailRequest?.abort()})
</script>

<style scoped>
.overview{border:1px solid #2d4264;border-radius:16px;padding:clamp(15px,2.5vw,29px);background:radial-gradient(ellipse at 86% -50%,#284671,transparent 70%),#112039;color:#f0f6ff}
.hero,.directory-header,.detail-header{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;flex-wrap:wrap}
.hero h1,.detail-header h2,.directory-header h2,.directory-header h3{margin:0}
.hero h1{font-size:clamp(24px,3vw,36px);font-weight:740;letter-spacing:-.04em}
.hero p:not(.hero-eyebrow){margin:7px 0 0;color:#b7c9e5}
.hero-eyebrow{color:#b2c5e5;letter-spacing:.16em;font-size:10px;font-weight:700;margin:0 0 8px}
.hero-eyebrow i,.worker i{display:inline-block;width:7px;height:7px;border-radius:50%;background:#91a9d0;margin-right:6px}
.worker i.online,.hero-eyebrow i{background:#73ddc2}
.worker{font-size:12px;color:#ccd9ee}.hero-actions,.controls,.policy,.ordered{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.tiles{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:11px;margin-top:20px}
.tiles button{background:#182c49;color:#d8e6fd;border:1px solid #385172;border-radius:10px;padding:12px;text-align:left;cursor:pointer;--tone:#9fbaff}
.tiles .awaiting_release{--tone:#ffd297}.tiles .needs_review{--tone:#ffa5b0}.tiles .in_progress{--tone:#85e6ce}
.tiles button:hover,.tiles .chosen{border-color:var(--tone)}.tiles strong,.tiles small{display:block}
.tiles strong{font:700 clamp(22px,3vw,34px) ui-monospace,monospace;margin:6px 0;color:var(--tone)}
.tiles small{color:#b0c3de}
.banner,.directory{margin-top:16px}
.directory{padding:15px 14px 5px}
.directory-header{margin-bottom:10px}
.title-link{background:none;border:0;padding:0;color:var(--text);text-align:left;font-weight:700;cursor:pointer}.title-link:hover{color:var(--accent);text-decoration:underline}
.secondary{display:block;color:var(--muted);margin-top:3px}
.detail-header h2{margin-top:9px;font-size:23px;overflow-wrap:anywhere}.detail-header p{margin:7px 0 0}
.spaced,.results-section{margin-top:16px}
.controls,.policy,.plan{padding:12px}.policy,.ordered,.plan{margin-bottom:15px}
.ordered :deep(.ant-select){min-width:160px}
.review-note{margin-left:9px}
.plan p{margin:5px 0}.plan code,.ant-descriptions code{overflow-wrap:anywhere}
.stage-log{padding-left:17px}.stage-log li{margin:5px 0;overflow-wrap:anywhere}
summary{cursor:pointer;color:var(--accent)}
pre{background:var(--surface-alt);padding:8px;border-radius:8px;max-height:230px;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere}
@media(max-width:680px){.tiles{grid-template-columns:repeat(2,minmax(0,1fr))}.directory{padding:11px}}
</style>
