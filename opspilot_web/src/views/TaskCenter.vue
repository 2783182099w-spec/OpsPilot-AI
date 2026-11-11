<template>
  <div>
    <workspace-heading section="资产与作业 / TASKS" title="任务中心" description="跟踪作业进度、查看主机结果、导出报告及查询会话记录。"/>
    <a-alert :type="worker.active?'info':'warning'" show-icon :message="worker.active?'执行器在线 · 最多同时连接 4 台 · 命令执行成功不代表全部检查项通过':'执行器离线，请检查后台任务服务；历史记录仍可查询'"/>
    <a-tabs v-model:activeKey="tab" @change="changeTab"><a-tab-pane key="tasks" tab="任务与命令记录">
      <a-space wrap class="toolbar"><a-select v-model:value="filters.kind" placeholder="全部任务类型" allow-clear style="width:150px" :options="options(kinds)" @change="searchTasks"/><a-select v-model:value="filters.status" placeholder="全部执行状态" allow-clear style="width:160px" :options="options(taskStates)" @change="searchTasks"/><a-input-search v-model:value="filters.search" placeholder="任务 ID / 命令 / 提交人" style="width:260px" @search="searchTasks"/><a-input v-model:value="filters.date_from" type="date" style="width:155px" @change="searchTasks"/><span>至</span><a-input v-model:value="filters.date_to" type="date" style="width:155px" @change="searchTasks"/><a-button @click="load">刷新</a-button><a-button @click="resetFilters">重置</a-button></a-space>
      <a-table :data-source="tasks" row-key="id" :columns="columns" :loading="loading" :scroll="{x:1150}" :pagination="{current:page,total,pageSize:20}" @change="p=>{page=p.current;load()}"><template #bodyCell="{column,record}"><template v-if="column.key==='kind'"><a-tag>{{kinds[record.kind]}}</a-tag><small>{{record.owner_name}}</small></template><template v-if="column.key==='command'"><code class="command">{{record.managed_job?record.script_meta.name+' · v'+record.managed_job.version:record.command||'固定只读检查'}}</code></template><template v-if="column.key==='progress'"><a-progress :percent="record.total?Math.round(record.done/record.total*100):0" :status="record.status==='failed'?'exception':record.status==='completed'?'success':'normal'" size="small"/><small>{{record.done}} / {{record.total}} 台</small></template><template v-if="column.key==='status'"><a-tag :color="color(record.status)">{{states[record.status]}}</a-tag><small v-if="record.cancel_requested&&record.status==='running'">正在停止</small></template><template v-if="column.dataIndex==='created_at'">{{time(record.created_at)}}</template><template v-if="column.key==='action'"><a-space wrap><a-button size="small" type="primary" @click="openDetail(record.id)">结果</a-button><a-button size="small" @click="report(record)">报告</a-button><a-button v-if="store.getters.canOperate&&['pending','running','awaiting_approval','awaiting_release'].includes(record.status)" size="small" danger @click="action(record,'cancel')">取消</a-button><a-button v-if="store.getters.canOperate&&!record.release&&!record.incident&&['completed','failed','interrupted','cancelled'].includes(record.status)" size="small" @click="action(record,'retry')">重试未成功</a-button><a-button v-if="record.status==='awaiting_approval'&&canReview(record)" size="small" @click="action(record,'approve')">审批</a-button><a-button v-if="canRelease(record)" size="small" @click="openDetail(record.id)">核对并放行</a-button></a-space></template></template></a-table>
    </a-tab-pane><a-tab-pane key="sessions" tab="SSH 会话与文本回放">
      <a-alert type="info" show-icon message="仅回放升级后保存的终端输出文本，不记录原始键盘输入。每会话最多 256 KiB，保留 30 天或最近 1000 个会话；文本按行播放，不还原原始输入时序。请勿在终端显示凭据，自动脱敏不能识别所有敏感内容。"/>
      <a-space class="toolbar"><a-input-search v-model:value="sessionSearch" placeholder="搜索主机 / 地址 / 操作人" @search="sessionPage=1;loadSessions()"/><a-button @click="loadSessions">刷新会话</a-button></a-space>
      <a-table :data-source="sessions" :columns="sessionColumns" row-key="id" :pagination="{current:sessionPage,total:sessionTotal,pageSize:20}" @change="p=>{sessionPage=p.current;loadSessions()}"><template #bodyCell="{column,record}"><template v-if="column.key==='status'">{{states[record.status]||record.status}}</template><template v-if="column.key==='time'">{{time(record.started_at)}}<br/><small>结束：{{time(record.ended_at)}}</small></template><template v-if="column.key==='size'">{{(record.bytes/1024).toFixed(1)}} KiB {{record.truncated?'（已截断）':''}}</template><template v-if="column.key==='action'"><a-button @click="openReplay(record)">文本回放</a-button></template></template></a-table>
    </a-tab-pane></a-tabs>
    <a-modal :open="!!selected" :title="'任务 #'+(selected?.id||'')+' · '+(kinds[selected?.kind]||'执行结果')" width="1200px" @cancel="closeDetail" @ok="closeDetail">
      <a-descriptions :column="3" bordered size="small"><a-descriptions-item label="状态">{{states[selected?.status]}}</a-descriptions-item><a-descriptions-item label="主机进度">{{selected?.done}} / {{selected?.total}}</a-descriptions-item><a-descriptions-item label="检查方案">{{profiles[selected?.options?.profile]||'—'}}</a-descriptions-item><a-descriptions-item label="开始时间">{{time(selected?.started_at)}}</a-descriptions-item><a-descriptions-item label="结束时间">{{time(selected?.finished_at)}}</a-descriptions-item><a-descriptions-item label="来源">{{selected?.options?.schedule_name||'手动提交'}}</a-descriptions-item></a-descriptions>
      <a-descriptions v-if="selected?.script_meta" bordered :column="3" size="small" style="margin-top:12px"><a-descriptions-item label="脚本">{{selected.script_meta.name}}</a-descriptions-item><a-descriptions-item label="语言">{{selected.script_meta.language==='shell'?'Shell':'Python 3'}}</a-descriptions-item><a-descriptions-item label="脚本库版本">{{selected.script_meta.script_version||'临时脚本快照'}}</a-descriptions-item><a-descriptions-item label="超时">{{selected.script_meta.timeout}} 秒</a-descriptions-item><a-descriptions-item label="并发">{{selected.script_meta.concurrency}} 台</a-descriptions-item><a-descriptions-item label="参数">{{JSON.stringify(selected.options?.args||[])}}</a-descriptions-item><a-descriptions-item label="SHA256" :span="3"><span style="word-break:break-all">{{selected.script_meta.sha256}}</span></a-descriptions-item></a-descriptions>
      <section v-if="selected?.managed_job" class="rollout-panel">
        <div class="rollout-title"><div><strong>受控批次</strong><small>首批 {{selected.managed_job.canary_count}} 台 · 后续每批 {{selected.managed_job.batch_size}} 台 · 累计失败 {{selected.managed_job.failure_limit}} 台时停派</small></div>
          <a-button v-if="canRelease(selected)" type="primary" @click="action(selected,'release')">放行后续批次</a-button>
          <a-button v-if="selected.status==='awaiting_approval'&&canReview(selected)" type="primary" @click="action(selected,'approve')">审核计划</a-button><a-button v-if="selected.status==='awaiting_approval'&&canReview(selected)" danger @click="action(selected,'reject')">驳回</a-button>
        </div>
        <a-alert v-if="selected.stop_reason" :type="selected.status==='awaiting_release'?'info':'warning'" :message="selected.stop_reason" show-icon/>
        <div class="rollout-facts">
          <span>审批：{{selected.approved_by||'—'}}</span><span>放行：{{selected.released_by||'—'}}</span>
          <span>验证：{{selected.managed_job.verify_service||'脚本退出码'}}</span>
          <span>成功 {{selected.summary?.success||0}} · 等待 {{selected.summary?.queued||0}} · 未派发 {{selected.summary?.skipped||0}}</span>
        </div>
        <p v-if="selected.options.managed.reason">操作原因：{{selected.options.managed.reason}}</p>
        <a-collapse class="target-list"><a-collapse-panel key="targets" header="执行目标快照"><div v-for="(host,index) in selected.options.managed.targets" :key="host.id">{{index+1}}. {{host.name}} · {{host.username}}@{{host.ip_addr}}:{{host.port}} · {{host.environment}}</div></a-collapse-panel></a-collapse>
      </section>
      <pre v-if="selected?.command" class="output">{{selected.command}}</pre>
      <a-space wrap class="toolbar"><a-tag v-for="(count,state) in selected?.check_summary||{}" :key="state" :color="color(state)">{{states[state]||state}} {{count}} 项</a-tag></a-space>
      <a-alert v-if="['inspection','probe'].includes(selected?.kind)" type="info" message="检查结论基于本次采样。待确认通常表示权限不足、工具缺失或未采集到证据；应复核后再判断健康状态。"/>
      <a-space wrap class="toolbar"><a-input-search v-model:value="resultFilters.host_search" placeholder="主机名 / 地址" @search="detail(selected.id,1)"/><a-select v-model:value="resultFilters.result_status" placeholder="全部主机执行状态" allow-clear style="width:200px" :options="options({success:'执行成功',failed:'执行失败',timeout:'超时',interrupted:'中断',cancelled:'已取消',skipped:'未派发',queued:'等待',running:'执行中'})" @change="detail(selected.id,1)"/><a-select v-model:value="resultFilters.check_state" placeholder="全部检查结论" allow-clear style="width:170px" :options="options({warning:'有异常',unknown:'有待确认',pass:'有通过项',review:'需复核',info:'取证'})" @change="detail(selected.id,1)"/><a-select v-if="selected?.managed_job" v-model:value="resultFilters.failure_code" placeholder="按失败原因筛选" allow-clear style="width:175px" :options="options(failureLabels)" @change="detail(selected.id,1)"/><a-button @click="detail(selected.id,resultPage)">刷新结果</a-button><a-button @click="report(selected)">{{selected?.kind==='inspection'?'导出健康报告':'导出执行报告'}}</a-button><a-button v-if="selected?.kind==='inspection'" @click="compare">与上次巡检对比</a-button></a-space>
      <a-table :data-source="selected?.results||[]" :columns="resultColumns" :pagination="{current:resultPage,total:selected?.results_count||0,pageSize:20}" :row-key="r=>r.host_info?.id||r.error" @change="p=>detail(selected.id,p.current)"><template #bodyCell="{column,record}"><template v-if="column.key==='host'"><strong>{{record.host_info?.name||'任务级错误'}}</strong><div>{{record.host_info?.ip_addr}}</div></template><template v-if="column.key==='status'"><a-tag :color="color(record.status)">{{states[record.status]||record.status}}</a-tag><div v-if="record.res_code!=null">退出码 {{record.res_code}}</div><div v-if="record.batch">第 {{record.batch}} 批</div><small v-if="record.failure_code">{{failureLabels[record.failure_code]||record.failure_code}}</small><small v-if="selected?.managed_job&&record.status!=='queued'">{{record.may_have_run===false?'未执行脚本':record.may_have_run===true?'脚本已尝试派发':'需核查远端'}}</small><a-button v-if="store.getters.canOperate&&selected?.managed_job&&!selected.release&&!selected.incident&&isFinished(selected)&&record.host_info?.id&&record.status!=='success'" size="small" type="link" @click="action(selected,'retry',[record.host_info.id],record.may_have_run!==false)">仅重试此主机</a-button></template><template v-if="column.key==='output'"><a-collapse v-if="record.stages?.length"><a-collapse-panel key="stages" header="阶段明细"><a-timeline><a-timeline-item v-for="stage in record.stages" :key="stage.name" :color="color(stage.status)"><strong>{{stageLabels[stage.name]||stage.name}}</strong> · {{states[stage.status]||stage.status}}<span v-if="stage.elapsed_ms!=null"> · {{(stage.elapsed_ms/1000).toFixed(2)}} 秒</span><div>{{stage.detail}}</div></a-timeline-item></a-timeline></a-collapse-panel></a-collapse><a-collapse v-if="record.checks?.length"><a-collapse-panel v-for="check in record.checks" :key="check.id"><template #header><a-tag :color="color(check.state)">{{states[check.state]||check.state}}</a-tag>{{check.title}}<div class="summary">{{check.summary}}</div></template><pre class="output">{{check.evidence||'该项无额外原始输出，请参考结论与任务错误信息。'}}</pre></a-collapse-panel></a-collapse><a-collapse v-if="record.res_data||record.error"><a-collapse-panel key="raw" header="原始输出 / 错误信息"><pre class="output">{{record.res_data||record.error}}</pre></a-collapse-panel></a-collapse></template></template></a-table>
      <a-alert v-if="['command','script'].includes(selected?.kind)" message="取消或超时会关闭连接，但不能保证远端进程终止。重试前请确认远端状态。" type="warning" show-icon/>
    </a-modal>
    <a-modal v-model:open="comparisonOpen" title="同范围巡检对比" width="900px" @ok="comparisonOpen=false"><p>{{comparison.message}} · 上次任务 {{comparison.previous_id||'无'}}</p><a-table :data-source="comparison.changes||[]" :columns="[{title:'主机',dataIndex:'host'},{title:'检查项',dataIndex:'title'},{title:'上次结论',dataIndex:'before'},{title:'本次结论',dataIndex:'after'}]" :row-key="(r,i)=>i"/></a-modal>
    <a-modal :open="!!replay" :title="'文本回放 · '+(replay?.host_label||'')" width="1100px" @cancel="closeReplay" @ok="closeReplay">
      <a-space class="toolbar"><a-button :disabled="!replayText" @click="togglePlay">{{playing?'暂停':'逐行播放'}}</a-button><a-button @click="stopPlay();visibleLines=replayLines.length">显示全文</a-button><a-button @click="stopPlay();visibleLines=0">回到开头</a-button><a-select v-model:value="speed" :options="[{value:1,label:'慢速'},{value:5,label:'正常'},{value:20,label:'快速'}]" style="width:100px"/><a-button :disabled="!replayText" @click="downloadText">下载文本</a-button><span>{{visibleLines}} / {{replayLines.length}} 行</span></a-space>
      <a-alert v-if="!replayText" type="info" message="此会话没有可回放文本：可能是历史会话、连接失败、尚未产生输出或文本已过保留期限。"/><a-alert v-if="replay?.truncated" type="warning" message="文本达到大小上限，仅保存前 256 KiB 范围内的输出。"/>
      <pre class="terminal-text">{{replayLines.slice(0,visibleLines).join('\n')}}</pre>
    </a-modal>
  </div>
</template>
<script setup>
import WorkspaceHeading from '@/components/WorkspaceHeading.vue'
import {h,ref,computed,onMounted,onBeforeUnmount,watch} from 'vue'
import {useRoute} from 'vue-router'
import {useStore} from 'vuex'
import {Modal} from 'ant-design-vue'
import api from '@/api'
import {kinds,profiles,states,color,options,time} from '@/utils/ops'
const route=useRoute(),store=useStore(),admin=computed(()=>store.getters.userInfo?.is_superuser),tab=ref('tasks')
const tasks=ref([]),selected=ref(null),loading=ref(false),worker=ref({active:false}),page=ref(1),total=ref(0),resultPage=ref(1)
const filters=ref({kind:route.query.kind||undefined}),resultFilters=ref({}),taskStates={awaiting_release:'待首批放行',pending:'排队',running:'执行中',completed:'执行完成',failed:'执行失败',interrupted:'中断',cancelled:'已取消',awaiting_approval:'待审批',rejected:'已驳回'}
const sessions=ref([]),sessionPage=ref(1),sessionTotal=ref(0),sessionSearch=ref(''),replay=ref(null),replayText=ref(''),visibleLines=ref(0),playing=ref(false),speed=ref(5),replayLines=computed(()=>replayText.value?replayText.value.split('\n'):[])
const columns=[{title:'ID',dataIndex:'id',width:70},{title:'类型 / 提交人',key:'kind',width:140},{title:'命令记录',key:'command',width:250},{title:'状态',key:'status',width:130},{title:'主机进度',key:'progress',width:140},{title:'提交时间',dataIndex:'created_at',width:180},{title:'操作',key:'action',width:260}]
const resultColumns=[{title:'主机',key:'host',width:180},{title:'执行状态',key:'status',width:130},{title:'检查结论与证据',key:'output'}]
const sessionColumns=[{title:'ID',dataIndex:'id'},{title:'主机',dataIndex:'host_label'},{title:'操作人',dataIndex:'owner'},{title:'会话状态',key:'status'},{title:'起止时间',key:'time'},{title:'文本大小',key:'size'},{title:'操作',key:'action'}]
const comparisonOpen=ref(false),comparison=ref({})
async function compare(){comparison.value=(await api.get(`/mtask/tasks/${selected.value.id}/comparison`)).data;comparisonOpen.value=true}
let timer,playTimer
async function load(){if(loading.value)return;loading.value=true;try{const [a,b]=await Promise.all([api.get('/mtask/tasks',{params:{...filters.value,page:page.value}}),api.get('/mtask/worker')]);tasks.value=a.data.results;total.value=a.data.count;worker.value=b.data}finally{loading.value=false}}
function searchTasks(){page.value=1;load()}
function resetFilters(){filters.value={};searchTasks()}
function openDetail(id){resultFilters.value={};detail(id)}
async function detail(id,p=1){selected.value=(await api.get(`/mtask/tasks/${id}`,{params:{...resultFilters.value,results_page:p}})).data;resultPage.value=p}
function closeDetail(){selected.value=null}
const failureLabels={authentication_failed:'SSH 认证失败',fingerprint_changed:'主机指纹变化',fingerprint_missing:'主机指纹未登记',connection_failed:'SSH 连接失败',connect_timeout:'SSH 连接超时',upload_failed:'脚本投递失败',upload_timeout:'脚本投递超时',preflight_failed:'依赖或语法检查失败',preflight_timeout:'预检超时',script_exit:'脚本返回非零',remote_timeout_code:'退出码 124，核查超时',remote_terminated_code:'退出码 137，核查中断',verification_failed:'服务验证失败',verify_timeout:'服务验证超时',remote_unknown:'远端状态待核查',not_dispatched:'策略拦截，未派发',cancelled_before_dispatch:'取消，未派发',cancelled_before_execution:'取消，未执行脚本',execution_error:'执行器异常'}
const stageLabels={queue:'等待派发',connect:'建立 SSH',upload:'投递脚本',preflight:'依赖与语法预检',execute:'执行脚本',verify:'验证服务',cleanup:'清理投递文件'}
const isFinished=task=>['completed','failed','interrupted','cancelled'].includes(task.status)
const own=task=>store.getters.userInfo?.username===task.owner_name
const canReview=task=>admin.value&&(!task.managed_job?.protected||!own(task))
const canRelease=task=>task.status==='awaiting_release'&&(!task.managed_job?.protected||canReview(task))
async function action(task,name,ids=null,uncertain=true){
  let review=task
  if(task.kind==='script'&&['approve','retry','release'].includes(name))review=(await api.get('/mtask/tasks/'+task.id)).data
  const managed=review.options?.managed
  const title={release:'确认首批结果并放行',approve:'审批执行计划',reject:'驳回执行计划',cancel:'确认取消任务',retry:'按原快照重新提交'}[name]
  const children=[]
  if(name==='release'){
    children.push(h('p','首批已通过。放行后开始其余批次，仍按累计失败阈值停止。'))
    for(const row of review.results.filter(row=>row.batch===1))children.push(h('div',row.host_info.name+' · '+states[row.status]+' · '+(row.stages?.find(s=>s.name==='verify')?.detail||'脚本退出码 '+row.res_code)))
  }else if(review.kind==='script'&&name!=='cancel'){
    children.push(h('p','脚本：'+review.script_meta?.name+'；SHA256：'+review.script_meta?.sha256))
    if(managed)children.push(h('p','作业版本 v'+managed.version+'；原因：'+(managed.reason||'—')+'；验证：'+(managed.verify_service||'退出码')))
    children.push(h('p','目标：'+(ids?ids.join(','):(managed?.targets?.map(h=>h.name+' ['+h.environment+']')||review.host_ids).join('、'))))
    children.push(h('p','参数：'+JSON.stringify(review.options?.args||[])))
    children.push(h('pre',{style:'white-space:pre-wrap;max-height:240px;overflow:auto;word-break:break-word'},review.command))
  }else children.push(h('p',name==='approve'?review.command:'不会回滚已执行的变更。'))
  let rejection='',acknowledged=false
  if(name==='reject')children.push(h('textarea',{placeholder:'填写驳回理由',maxlength:500,rows:4,style:'width:100%',onInput:e=>rejection=e.target.value}))
  if(name==='retry'&&managed&&uncertain)children.push(h('label',[h('input',{type:'checkbox',onChange:e=>acknowledged=e.target.checked}),' 我已核查目标状态，确认可以重新执行脚本。']))
  Modal.confirm({okText:'确认',cancelText:'返回',width:review.kind==='script'?800:520,title,content:h('div',children),
    onOk:async()=>{const {data}=await api.post('/mtask/tasks/'+task.id+'/action',{action:name,confirmed:true,host_ids:ids,acknowledge_remote:acknowledged,reason:rejection});await load();if(name==='retry'&&data.id)openDetail(data.id);else if(selected.value?.id===task.id)await detail(task.id,resultPage.value)}})
}
function saveBlob(data,name){const url=URL.createObjectURL(data),a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}
async function report(task){const health=task.kind==='inspection';const {data}=await api.get(`/mtask/tasks/${task.id}/${health?'health-report':'report'}`,{responseType:'blob'});saveBlob(data,`task-${task.id}-report.${health?'html':'csv'}`)}
async function loadSessions(){const {data}=await api.get('/host/sessions',{params:{page:sessionPage.value,search:sessionSearch.value}});sessions.value=data.results;sessionTotal.value=data.count}
function changeTab(key){if(key==='sessions')loadSessions()}
async function openReplay(row){stopPlay();const {data}=await api.get(`/host/sessions/${row.id}`);replay.value={...row,...data};replayText.value=data.text;visibleLines.value=replayLines.value.length}
function stopPlay(){clearInterval(playTimer);playing.value=false}
function togglePlay(){if(playing.value){stopPlay();return}if(visibleLines.value>=replayLines.value.length)visibleLines.value=0;playing.value=true;playTimer=setInterval(()=>{visibleLines.value=Math.min(visibleLines.value+speed.value,replayLines.value.length);if(visibleLines.value===replayLines.value.length)stopPlay()},250)}
function closeReplay(){stopPlay();replay.value=null;replayText.value=''}
function downloadText(){saveBlob(new Blob([replayText.value],{type:'text/plain;charset=utf-8'}),`ssh-session-${replay.value.id}.txt`)}
watch(()=>route.query,query=>{filters.value.kind=query.kind||undefined;page.value=1;load();if(query.task)openDetail(Number(query.task))})
onMounted(()=>{load();if(route.query.task)openDetail(Number(route.query.task));timer=setInterval(async()=>{if(document.hidden)return;if(tab.value==='tasks'){await load();if(selected.value&&['pending','running','awaiting_approval','awaiting_release'].includes(selected.value.status))await detail(selected.value.id,resultPage.value)}},5000)})
onBeforeUnmount(()=>{clearInterval(timer);stopPlay()})
</script>
<style scoped>
.rollout-panel{background:var(--surface-alt);border:1px solid var(--line);border-radius:9px;padding:14px;margin:14px 0}.rollout-title,.rollout-facts{display:flex;gap:12px;justify-content:space-between;align-items:center;flex-wrap:wrap}.rollout-title{margin-bottom:10px}.rollout-facts,.target-list{margin-top:10px}.target-list :deep(.ant-collapse-content-box){max-height:180px;overflow:auto}.toolbar{margin:16px 0}.output{white-space:pre-wrap;max-height:280px;overflow:auto;word-break:break-word;background:var(--surface-alt);padding:12px}.command{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;word-break:break-all}.summary{font-size:12px;color:var(--muted);margin-top:6px}.terminal-text{white-space:pre-wrap;word-break:break-word;max-height:500px;overflow:auto;min-height:180px;background:#111827;color:#d1fae5;padding:20px}small{color:var(--muted);display:block}
</style>
