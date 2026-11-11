<template>
  <section>
    <workspace-heading section="运行态势 / DIAGNOSTIC TREES" title="诊断与修复" description="管理诊断模板、分支动作与告警联动，核对执行结果。">
      <a-space wrap><router-link :to="{name:'Alerts'}">返回告警中心</router-link><a-button @click="load">刷新</a-button></a-space>
    </workspace-heading>
    <div class="ops-panel content">
      <a-tabs v-model:activeKey="tab" @change="switchTab">
        <a-tab-pane key="catalog" tab="诊断目录">
          <p class="ops-muted">目录按告警来源列出已有检查。自建树可在告警联动中锁定版本并绑定结论。</p>
          <a-table :columns="catalogColumns" :data-source="builtins" :loading="loading" row-key="key" :scroll="{x:640}" :pagination="{pageSize:10}">
            <template #bodyCell="{column,record}">
              <template v-if="column.key==='checks'"><a-space wrap size="small"><a-tag v-for="check in record.checks" :key="check">{{check}}</a-tag></a-space></template>
              <template v-else-if="column.key==='action'"><a-tag :color="record.action?'blue':'default'">{{actions[record.action]||'只读复检'}}</a-tag></template>
            </template>
          </a-table>
        </a-tab-pane>
        <a-tab-pane key="trees" tab="自建诊断树">
          <a-button v-if="admin" :disabled="!sample" type="primary" class="action" @click="editTree()">从 Nginx 示例新建</a-button>
          <a-table :columns="treeColumns" :data-source="trees" :loading="loading" row-key="id">
            <template #bodyCell="{column,record}">
              <template v-if="column.key==='size'">{{record.definition.nodes.length}} 个检查 · {{Object.keys(record.definition.conclusions).length}} 个结论</template>
              <template v-else-if="column.key==='actions'">
                <a-space wrap><a-button size="small" @click="editTree(record)">{{admin?'编辑':'查看'}}</a-button><a-button v-if="canOperate" size="small" @click="openTrial(record)">只读试跑</a-button><a-button v-if="admin" size="small" @click="openRules(record.id)">查看联动</a-button></a-space>
              </template>
            </template>
          </a-table>
        </a-tab-pane>
        <a-tab-pane key="rules" tab="告警联动">
          <a-space v-if="treeId" style="margin-bottom:10px">
            <a-tag color="blue">{{trees.find(tree=>tree.id===treeId)?.name||'当前诊断树'}}</a-tag>
            <a-button size="small" @click="openRules()">查看全部联动</a-button>
          </a-space>
          <diagnostic-rules :tree-id="treeId"/>
        </a-tab-pane>
        <a-tab-pane key="results" tab="诊断结果"><diagnostic-results/></a-tab-pane>
      </a-tabs>
    </div>
    <a-drawer v-model:open="treeOpen" title="诊断树配置" :width="'min(850px, 100vw)'">
      <a-form layout="vertical" :disabled="!admin">
        <a-form-item label="名称"><a-input v-model:value="draft.name" :maxlength="100"/></a-form-item>
        <a-form-item label="入口检查"><a-select v-model:value="draft.definition.root" :options="draft.definition.nodes.map(node=>({label:node.id,value:node.id}))"/></a-form-item>
        <a-collapse v-if="draft.revisions?.length" style="margin:0 0 15px">
          <a-collapse-panel key="revisions" header="版本记录（最近 20 次）">
            <ul><li v-for="item in draft.revisions" :key="item.version">
              v{{item.version}} · {{item.actor__username}} · {{time(item.created_at)}}
              <a-button v-if="admin&&item.version!==draft.version" size="small" type="link" @click="loadVersion(item)">复制定义到编辑区</a-button>
            </li></ul>
          </a-collapse-panel>
        </a-collapse>
        <h3>检查节点</h3>
        <div v-for="(node,index) in draft.definition.nodes" :key="index" class="ops-panel node">
          <div class="node-head"><strong>检查 {{index+1}}</strong><a-button danger type="text" size="small" @click="draft.definition.nodes.splice(index,1)">移除</a-button></div>
          <div class="grid">
            <a-form-item label="节点 ID"><a-input v-model:value="node.id" :maxlength="30"/></a-form-item>
            <a-form-item label="检查类型"><a-select v-model:value="node.kind" :options="options(checkKinds)"/></a-form-item>
            <a-form-item v-if="['systemd','log'].includes(node.kind)" label="systemd 单元"><a-input v-model:value="node.unit"/></a-form-item>
            <a-form-item v-if="['http','port'].includes(node.kind)" label="本机端口"><a-input-number v-model:value="node.port" :min="1" :max="65535"/></a-form-item>
            <a-form-item v-if="['http','disk'].includes(node.kind)" label="路径"><a-input v-model:value="node.path" :maxlength="151"/></a-form-item>
            <a-form-item v-if="node.kind==='disk'" label="磁盘使用率阈值"><a-input-number v-model:value="node.threshold" :min="1" :max="100"/></a-form-item>
            <a-form-item v-if="node.kind==='log'" label="匹配关键词"><a-input v-model:value="node.keyword" :maxlength="80"/></a-form-item>
          </div>
          <div class="grid branches">
            <a-form-item v-for="(label,key) in branchNames" :key="key" :label="label"><a-select v-model:value="node[key]" :options="branchOptions(node)"/></a-form-item>
          </div>
        </div>
        <a-button v-if="admin" :disabled="draft.definition.nodes.length>=12" @click="addNode">添加检查</a-button>
        <h3 class="spaced">结论节点</h3>
        <div v-for="(leaf,index) in draft.leaves" :key="index" class="leaf">
          <a-input v-model:value="leaf.id" placeholder="结论 ID" aria-label="结论 ID" :maxlength="30"/><a-input v-model:value="leaf.text" placeholder="结论说明" aria-label="结论说明" :maxlength="160"/>
          <a-button v-if="admin" danger type="text" @click="draft.leaves.splice(index,1)">移除</a-button>
        </div>
        <a-space wrap><a-button v-if="admin" :disabled="draft.leaves.length>=12" @click="draft.leaves.push({id:'',text:''})">添加结论</a-button><a-button v-if="admin" type="primary" :loading="saving" @click="saveTree">保存诊断树</a-button></a-space>
      </a-form>
    </a-drawer>
    <a-modal v-model:open="trialOpen" title="只读诊断试跑" ok-text="提交试跑" :confirm-loading="saving" @ok="runTrial">
      <p>在授权主机执行 {{trial.tree?.name}} v{{trial.tree?.version}}，结果可在任务中心核对。</p>
      <a-select v-model:value="trial.host_id" :options="hostOptions" show-search option-filter-prop="label" style="width:100%" placeholder="选择主机"/>
    </a-modal>
  </section>
</template>

<script setup>
import {computed,onMounted,ref,watch} from 'vue'
import {useRoute,useRouter} from 'vue-router'
import {useStore} from 'vuex'
import {message} from 'ant-design-vue'
import api from '@/api'
import WorkspaceHeading from '@/components/WorkspaceHeading.vue'
import DiagnosticResults from '@/components/DiagnosticResults.vue'
import DiagnosticRules from '@/components/DiagnosticRules.vue'
import {options,time} from '@/utils/ops'

const store=useStore(),router=useRouter(),route=useRoute()
const tab=ref('catalog')
const treeId=computed(()=>tab.value==='rules'?Number(route.query.tree)||undefined:undefined)
const actions={nginx_start:'审批或受控启动 Nginx',ssh_password:'审批后禁用 SSH 密码登录',ssh_root:'审批后限制 root 密码登录'}
watch(()=>route.query.tab,value=>{tab.value=['catalog','trees','rules','results'].includes(value)?value:'catalog'},{immediate:true})
function switchTab(value){router.replace({query:{tab:value}})}
function openRules(id){router.push({query:{tab:'rules',tree:id?String(id):undefined}})}
const admin=computed(()=>store.getters.userInfo?.is_superuser)
const canOperate=computed(()=>store.getters.canOperate)
const builtins=ref([]),trees=ref([]),hosts=ref([]),sample=ref(null),loading=ref(false),saving=ref(false)
const treeOpen=ref(false),trialOpen=ref(false)
const draft=ref({definition:{nodes:[]},leaves:[]}),trial=ref({})
const catalogColumns=[{title:'诊断检查',dataIndex:'name'},{title:'适用来源',dataIndex:'source'},{title:'采集证据',key:'checks'},{title:'可绑定动作',key:'action'}]
const treeColumns=[{title:'模板名称',dataIndex:'name'},{title:'版本',dataIndex:'version'},{title:'构成',key:'size'},{title:'操作',key:'actions'}]
const hostOptions=computed(()=>hosts.value.map(host=>({value:host.id,label:host.name+' · '+host.ip_addr})))
const checkKinds={http:'本机 HTTP',port:'本机 TCP 端口',systemd:'systemd 状态',disk:'文件系统空间',log:'日志关键词'}
const branchNames={ok:'通过分支',failed:'异常分支',unknown:'待确认分支'}
function branchOptions(current){
  return [...draft.value.definition.nodes.filter(n=>n!==current),...draft.value.leaves]
    .filter(item=>item.id).map(item=>({label:item.id,value:item.id}))
}
function addNode(){
  const taken=new Set([...draft.value.definition.nodes,...draft.value.leaves].map(item=>item.id))
  let i=1
  while(taken.has('check'+i))i++
  draft.value.definition.nodes.push({id:'check'+i,kind:'port',port:80})
}
async function load(){
  loading.value=true
  try{
    const {data}=await api.get('/mtask/diagnostic-trees')
    trees.value=data.results
    builtins.value=data.builtins
    sample.value=data.sample
  }catch{ /* shared API feedback */ }
  finally{loading.value=false}
}
async function getHosts(){hosts.value=(await api.get('/host/')).data}
async function editTree(row){
  if(!row&&!sample.value)return
  let data
  try{data=row?(await api.get('/mtask/diagnostic-trees/'+row.id)).data:{name:'Nginx 可用性诊断',definition:sample.value}}
  catch{return}
  const item=JSON.parse(JSON.stringify(data))
  draft.value={...item,leaves:Object.entries(item.definition.conclusions).map(([id,text])=>({id,text}))}
  treeOpen.value=true
}
function loadVersion(item){
  draft.value.definition=JSON.parse(JSON.stringify(item.definition))
  draft.value.leaves=Object.entries(item.definition.conclusions).map(([id,text])=>({id,text}))
}
async function saveTree(){
  const {id,name,version,definition,leaves}=draft.value
  if(new Set(leaves.map(item=>item.id)).size!==leaves.length){message.warning('结论 ID 不可重复');return}
  saving.value=true
  try{
    await api.post('/mtask/diagnostic-trees'+(id?'/'+id:''),{name,version,
      definition:{...definition,conclusions:Object.fromEntries(leaves.map(item=>[item.id,item.text]))}})
    treeOpen.value=false
    message.success('诊断树已保存')
    load()
  }catch{ /* shared API feedback */ }
  finally{saving.value=false}
}
async function openTrial(tree){
  try{await getHosts()}catch{return}
  trial.value={tree,host_id:undefined}
  trialOpen.value=true
}
async function runTrial(){
  if(!trial.value.host_id){message.warning('请选择目标主机');return}
  saving.value=true
  try{
    const {data}=await api.post('/mtask/diagnostic-trees/'+trial.value.tree.id+'/trial',{
      host_id:trial.value.host_id,version:trial.value.tree.version,confirmed:true,
    })
    trialOpen.value=false
    router.push({name:'TaskCenter',query:{task:String(data.id)}})
  }catch{ /* shared API feedback */ }
  finally{saving.value=false}
}
onMounted(load)
</script>

<style scoped>
.content { padding:0 14px 12px; }
.action { margin-bottom:12px; }
.node { padding:12px; margin-bottom:12px; }
.node-head { display:flex; align-items:center; justify-content:space-between; }
.grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:0 12px; }
.branches { grid-template-columns:repeat(3,minmax(0,1fr)); }
.leaf { display:grid; grid-template-columns:1fr 2fr auto; gap:8px; margin-bottom:10px; }
.spaced { margin-top:18px; }
@media(max-width:600px){.branches{grid-template-columns:1fr}.leaf{grid-template-columns:1fr 2fr}}
</style>
