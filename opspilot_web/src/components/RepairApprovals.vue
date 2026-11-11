<template>
  <section>
    <div class="ops-panel approval-panel">
      <div class="queue-head"><h2>待审批修复 <a-tag color="blue">{{total}}</a-tag></h2><a-button :loading="loading" @click="load">刷新审批</a-button></div>
      <a-table :columns="columns" :data-source="rows" row-key="task_id" :loading="loading" :scroll="{x:720}"
        :pagination="{current:page,total,pageSize:20,showSizeChanger:false}" @change="changePage">
        <template #emptyText><p>当前没有待审批的修复</p><p class="ops-muted">在告警详情一键诊断，符合条件后发起本次修复授权。启用审批策略也可生成待审批记录。</p></template>
        <template #bodyCell="{column,record}">
          <template v-if="column.key==='alert'"><strong>{{record.title}}</strong><br><small class="ops-muted">{{record.host}} · 告警 #{{record.finding_id}} · 第 {{record.occurrence}} 轮</small></template>
          <template v-else-if="column.key==='created'">{{time(record.created_at)}}</template>
          <template v-else-if="column.key==='action'"><a-button size="small" :type="record.can_approve?'primary':'default'" @click="inspect(record)">{{record.can_approve?'核对并授权':'查看申请'}}</a-button></template>
        </template>
      </a-table>
    </div>

    <a-drawer :open="!!current" title="核对本次修复" :width="'min(660px, 100vw)'" @close="close">
      <template v-if="current">
        <h3>{{current.title}} · {{current.host}}</h3>
        <p class="ops-muted">申请人 {{current.actor}} · 告警第 {{current.occurrence}} 轮 · 任务 #{{current.task_id}}</p>
        <a-alert type="info" show-icon  :message="current.action?.name" :description="current.action?.impact"/>
        <a-spin v-if="detailLoading" class="evidence"/>
        <template v-else-if="evidence">
          <h4 class="evidence">{{evidence.tree_name}} · v{{evidence.tree_version}}</h4>
          <p>{{evidence.conclusion || '请核对检查记录。'}}</p>
          <ul class="checks"><li v-for="(check,index) in evidence.checks" :key="index">
            <a-tag :color="check.state==='failed'?'red':check.state==='ok'?'green':'default'">{{states[check.state]}}</a-tag>
            <div><strong>{{check.title}}</strong><p class="ops-muted">{{check.evidence}}</p></div>
          </li></ul>
        </template>
        <a-alert v-else type="warning" class="evidence" message="当前无法读取本轮诊断证据，请到告警详情核对。"/>
        <a-space class="evidence" wrap>
          <a-button v-if="current.can_approve" type="primary" :disabled="detailLoading||!evidence?.checks?.length" @click="confirmOpen=true">授权执行本次修复</a-button>
          <router-link :to="{name:'Alerts',query:{finding:String(current.finding_id)}}">查看告警</router-link>
          <router-link :to="{name:'TaskCenter',query:{task:String(current.task_id)}}">查看任务</router-link>
        </a-space>
        <p v-if="!current.can_approve" class="ops-muted evidence">受控修复需由申请人之外的管理员审批。</p>
      </template>
    </a-drawer>
    <a-modal v-if="confirmOpen" v-model:open="confirmOpen" title="授权执行本次修复" ok-text="确认授权" cancel-text="取消"
      :z-index="1200" :confirm-loading="sending" @ok="approve">
      <p><strong>{{current?.action?.name}}</strong> · {{current?.host}}</p>
      <p>{{current?.action?.impact}}</p>
    </a-modal>
  </section>
</template>

<script setup>
import {onMounted,ref} from 'vue'
import {message} from 'ant-design-vue'
import api from '@/api'
import {time} from '@/utils/ops'

const rows=ref([]),total=ref(0),page=ref(1),loading=ref(false)
const current=ref(null),evidence=ref(null),detailLoading=ref(false)
const confirmOpen=ref(false),sending=ref(false)
const states={ok:'通过',failed:'异常',unknown:'待确认'}
const columns=[{title:'告警与目标',key:'alert'},{title:'申请人',dataIndex:'actor'},
  {title:'提交时间',key:'created'},{title:'操作',key:'action',width:130}]
let detailRequest=0
async function load(){
  loading.value=true
  try{
    const {data}=await api.get('/mtask/repair-approvals',{params:{page:page.value}})
    rows.value=data.results
    total.value=data.count
  }catch{ /* shared API feedback */ }
  finally{loading.value=false}
}
function changePage(p){page.value=p.current;load()}
async function inspect(row){
  const request=++detailRequest
  current.value=row
  evidence.value=null
  detailLoading.value=true
  try{
    const {data}=await api.get('/mtask/incidents/'+row.finding_id+'/runs',{params:{workspace:1}})
    if(request!==detailRequest)return
    const diagnosisId=row.diagnosis_task_id||data.workflow?.diagnosis_task_id
    evidence.value=diagnosisId
      ? data.runs.find(run=>run.task_id===diagnosisId)
      : data.runs.find(run=>run.occurrence===row.occurrence&&run.mode==='diagnosis'&&run.status==='completed')
  }catch{ /* shared API feedback */ }
  finally{if(request===detailRequest)detailLoading.value=false}
}
function close(){detailRequest++;current.value=null;confirmOpen.value=false}
async function approve(){
  const row=current.value
  if(!row||sending.value)return
  sending.value=true
  try{
    await api.post('/mtask/tasks/'+row.task_id+'/action',{action:'approve',confirmed:true})
    message.success('已授权，等待执行器处理')
    close()
    if(rows.value.length===1&&page.value>1)page.value--
    await load()
  }catch{ /* shared API feedback */ }
  finally{sending.value=false}
}
onMounted(load)
</script>

<style scoped>
.approval-panel{padding:16px}
.queue-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px}
.approval-panel h2{margin:0;font-size:17px}
.evidence{margin-top:16px}
.checks{padding:0;list-style:none}
.checks li{display:flex;align-items:flex-start;gap:8px;margin:8px 0}
.checks p{margin:3px 0}
</style>
