<template>
  <section>
    <div class="results-toolbar"><p class="ops-muted">汇总告警诊断、策略诊断和只读试跑。</p><a-button :loading="loading" @click="load">刷新结果</a-button></div>
    <a-table :columns="columns" :data-source="rows" row-key="id" :loading="loading" :scroll="{x:720}"
      :pagination="{current:page,total,pageSize:20,showSizeChanger:false}" @change="p=>{page=p.current;load()}">
      <template #emptyText>当前没有可查看的诊断结果</template>
      <template #bodyCell="{column,record}">
        <template v-if="column.key==='tree'"><strong>{{record.incident.tree_name}}</strong> · v{{record.incident.tree_version}}</template>
        <template v-else-if="column.key==='source'">
          <router-link v-if="record.incident.finding_id" :to="{name:'Alerts',query:{finding:String(record.incident.finding_id)}}">告警 #{{record.incident.finding_id}} · 第 {{record.incident.occurrence}} 轮</router-link>
          <span v-else>只读试跑</span>
        </template>
        <template v-else-if="column.key==='state'"><a-tag :color="color(record.status)">{{states[record.status]}}</a-tag></template>
        <template v-else-if="column.key==='created'">{{time(record.created_at)}}</template>
        <template v-else-if="column.key==='result'"><a-button size="small" @click="openResult(record)">查看结果 #{{record.id}}</a-button></template>
      </template>
    </a-table>
    <a-drawer :open="!!selected" title="诊断结果详情" :width="'min(680px, 100vw)'" @close="closeResult">
      <template v-if="selected">
        <h3>{{selected.incident.tree_name}} · v{{selected.incident.tree_version}}</h3>
        <p class="ops-muted">任务 #{{selected.id}} · {{states[selected.status]}}</p>
        <a-spin v-if="resultLoading"/>
        <template v-else-if="structured">
          <a-alert type="info" :message="structured.conclusion || '本次诊断暂无结论'"/>
          <ul><li v-for="(check,index) in structured.checks" :key="index"><strong>{{check.title}}</strong> · {{check.evidence}}</li></ul>
        </template>
        <template v-else>
          <div v-for="(row,index) in results" :key="index">
            <p><a-tag>{{row.status}}</a-tag><small class="ops-muted">{{row.failure_code}}</small></p>
            <pre v-if="row.res_data">{{row.res_data}}</pre>
          </div>
          <p v-if="!results.length" class="ops-muted">当前暂无执行输出。</p>
        </template>
        <router-link :to="{name:'TaskCenter',query:{task:String(selected.id)}}">查看完整任务</router-link>
      </template>
    </a-drawer>
  </section>
</template>

<script setup>
import {onMounted,ref} from 'vue'
import api from '@/api'
import {time,color,states} from '@/utils/ops'

const rows=ref([]),page=ref(1),total=ref(0),loading=ref(false)
const columns=[{title:'诊断树与版本',key:'tree'},{title:'来源',key:'source'},{title:'状态',key:'state'},
  {title:'发起人',dataIndex:'owner_name'},{title:'时间',key:'created'},{title:'操作',key:'result'}]
async function load(){
  loading.value=true
  try{
    const {data}=await api.get('/mtask/tasks',{params:{page:page.value,incident_mode:'diagnosis'}})
    rows.value=data.results
    total.value=data.count
  }catch{ /* shared API feedback */ }
  finally{loading.value=false}
}
const selected=ref(null),structured=ref(null),results=ref([]),resultLoading=ref(false)
let sequence=0
async function openResult(row){
  const request=++sequence
  selected.value=row
  structured.value=null
  results.value=[]
  resultLoading.value=true
  try{
    const finding=row.incident.finding_id
    if(finding){
      const {data}=await api.get('/mtask/incidents/'+finding+'/runs',{params:{workspace:1}})
      if(request!==sequence)return
      structured.value=data.runs.find(run=>run.task_id===row.id)
    }
    if(!structured.value){
      const {data}=await api.get('/mtask/tasks/'+row.id)
      if(request===sequence)results.value=data.results
    }
  }catch{ /* shared API feedback */ }
  finally{if(request===sequence)resultLoading.value=false}
}
function closeResult(){sequence++;selected.value=null}
onMounted(load)
</script>

<style scoped>
.results-toolbar{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:12px}
.results-toolbar p{margin:0}
pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:45vh;overflow:auto}
</style>
