<template>
  <div class="job-workspace">
    <header class="hero">
      <div><span class="eyebrow">OPSPILOT / OPERATIONS</span><h1>作业库</h1><p>用已发布的作业执行日常操作。从参数与目标核对，到首批验证，再到逐机结果。</p></div>
      <a-space wrap><router-link v-if="store.getters.canOperate" :to="{name:'MultiExec'}"><a-button>临时命令与脚本</a-button></router-link><a-button v-if="admin" type="primary" @click="edit()">新建作业</a-button></a-space>
    </header>
    <div class="summary-grid">
      <div><span>可用作业</span><strong>{{ jobs.filter(j=>j.state==='published').length }}</strong></div>
      <div><span>{{admin?'待发布草稿':'只读作业'}}</span><strong>{{ jobs.filter(j=>admin?j.state==='draft':j.definition.risk==='read_only').length }}</strong></div>
      <div><span>修改类作业</span><strong>{{ jobs.filter(j=>j.definition.risk==='change').length }}</strong></div>
      <router-link :to="{name:'TaskCenter'}"><span>执行记录</span><strong class="go">前往任务中心 →</strong></router-link>
    </div>
    <section class="library">
      <div class="library-bar"><h2>全部作业 <small>{{ visible.length }} 项</small></h2><a-space wrap><a-input-search v-model:value="search" placeholder="搜索作业名称、用途" allow-clear/><a-select v-if="admin" v-model:value="stateFilter" style="width:120px" :options="options({all:'全部状态',published:'已发布',draft:'草稿',disabled:'已停用'})"/><a-button @click="load">刷新</a-button></a-space></div>
      <a-alert v-if="error" type="error" message="作业库未能加载，请重试。"/>
      <a-spin :spinning="loading">
        <a-empty v-if="!visible.length" description="没有符合条件的作业"><a-button v-if="admin&&!jobs.length" type="primary" @click="edit()">从只读示例开始</a-button></a-empty>
        <div v-else class="job-grid">
          <article v-for="job in visible" :key="job.id" class="job-card">
            <div class="card-top"><a-tag :color="job.definition.kind==='python'?'purple':'blue'">{{ labels[job.definition.kind] }}</a-tag><a-tag :color="job.state==='published'?'green':'default'">{{ jobStates[job.state] }}</a-tag><span>v{{job.version}}</span></div>
            <h3>{{ job.definition.name }}</h3><p>{{ job.definition.description }}</p>
            <div class="tags"><a-tag :color="job.definition.risk==='change'?'orange':'cyan'">{{ riskLabels[job.definition.risk] }}</a-tag><a-tag v-for="e in job.definition.environments" :key="e">{{ envs[e] }}</a-tag></div>
            <div class="card-meta">首批 {{job.definition.canary_count}} 台 · 并发 {{job.definition.concurrency}} 台 · {{job.definition.parameters.length}} 个参数</div>
            <div class="card-bottom"><span>{{job.owner}} · {{time(job.updated_at)}}</span><a-space wrap>
              <a-button v-if="admin" size="small" @click="edit(job)">维护</a-button>
              <a-button v-if="store.getters.canOperate&&job.state==='published'" type="primary" size="small" @click="begin(job)">运行作业</a-button>
            </a-space></div>
          </article>
        </div>
      </a-spin>
    </section>

    <a-drawer v-model:open="runOpen" :title="current?.definition.name||'运行作业'" :width="drawerWidth" destroy-on-close>
      <template v-if="current">
        <a-steps :current="plan?1:0" size="small" :items="[{title:'填写执行范围'},{title:'确认作业计划'}]"/>
        <a-form v-if="!plan" layout="vertical" class="spaced">
          <a-alert type="info" show-icon message="按选择顺序确定首批主机。后续批次需在任务详情里人工放行。" />
          <a-form-item class="spaced" label="目标主机" required>
            <a-select v-model:value="hostIds" mode="multiple" show-search option-filter-prop="label" :options="eligible" :loading="hostsLoading" placeholder="按主机名或地址搜索并按顺序选择" />
          </a-form-item>
          <div class="host-order" v-if="hostIds.length"><strong>首批：</strong>{{hostIds.slice(0,current.definition.canary_count).map(id=>hostMap[id]?.name).join('、')}}<span>后续 {{Math.max(0,hostIds.length-current.definition.canary_count)}} 台</span></div>
          <a-form-item v-for="field in current.definition.parameters" :key="field.name" :label="field.label" :required="field.required">
            <a-select v-if="field.type==='enum'" v-model:value="values[field.name]" :options="field.choices.map(v=>({value:v,label:v}))" placeholder="请选择"/>
            <a-input-number v-else-if="field.type==='integer'" v-model:value="values[field.name]" :min="field.min" :max="field.max" :precision="0" style="width:100%"/>
            <a-input v-else v-model:value="values[field.name]" :maxlength="200" :placeholder="field.name"/>
          </a-form-item>
          <a-form-item label="操作原因 / 变更单号" :required="protectedRun"><a-textarea v-model:value="reason" :rows="2" :maxlength="500" show-count placeholder="修改类或生产作业必填"/></a-form-item>
          <a-button type="primary" :disabled="!hostIds.length" :loading="busy" @click="preview">生成执行计划</a-button>
        </a-form>
        <div v-else class="spaced">
          <a-alert show-icon :type="plan.options.managed.protected?'warning':'info'" :message="plan.approval_required?'提交后等待审批；受控变更不可自审':'提交后进入队列，只先执行首批'"/>
          <a-descriptions class="spaced" :column="2" bordered size="small">
            <a-descriptions-item label="版本">v{{plan.options.managed.version}}</a-descriptions-item><a-descriptions-item label="目标">{{plan.options.managed.targets.length}} 台</a-descriptions-item>
            <a-descriptions-item label="首批 / 后续批大小">{{plan.options.managed.canary_count}} / {{plan.options.managed.batch_size}} 台</a-descriptions-item>
            <a-descriptions-item label="累计失败停止">{{plan.options.managed.failure_limit}} 台</a-descriptions-item>
            <a-descriptions-item label="服务验证">{{plan.options.managed.verify_service||'以脚本退出码判定'}}</a-descriptions-item>
            <a-descriptions-item label="单机超时">{{plan.options.timeout}} 秒</a-descriptions-item>
          </a-descriptions>
          <div class="plan-targets"><div v-for="(host,index) in plan.options.managed.targets" :key="host.id"><a-tag :color="index<plan.options.managed.canary_count?'blue':'default'">{{index<plan.options.managed.canary_count?'首批':'后续'}}</a-tag>{{host.name}} · {{host.username}}@{{host.ip_addr}}:{{host.port}} · {{envs[host.environment]}}</div></div>
          <p v-if="plan.options.managed.reason">原因：{{plan.options.managed.reason}}</p>
          <p v-if="Object.keys(plan.options.managed.parameters).length">参数：{{JSON.stringify(plan.options.managed.parameters)}}</p>
          <a-collapse><a-collapse-panel key="1" header="核对脚本快照"><pre class="code">{{plan.source}}</pre></a-collapse-panel></a-collapse>
          <p class="hint">计划 10 分钟内有效。本步骤未连接主机，也不预测脚本副作用。</p>
          <a-space><a-button @click="plan=null">返回修改</a-button><a-button type="primary" :loading="busy" @click="submit">确认并提交</a-button></a-space>
        </div>
      </template>
    </a-drawer>

    <a-drawer v-model:open="editorOpen" :title="editing?'维护作业':'新建作业'" :width="drawerWidth" destroy-on-close>
      <template #footer>
        <div v-if="form" class="editor-footer"><span class="hint">{{dirty?'有未保存的修改':'当前修改已保存'}}</span>
          <a-space wrap><a-button type="primary" :loading="busy" @click="save">保存草稿</a-button><a-button v-if="editing&&editing.state!=='published'" :disabled="dirty||busy" @click="transition('publish')">发布当前版本</a-button><a-button v-if="editing?.state==='published'" danger :disabled="busy" @click="transition('disable')">停用</a-button></a-space>
        </div>
      </template>
      <template v-if="form">
        <a-alert v-if="editing" :type="editing.state==='published'?'warning':'info'" show-icon :message="'当前版本 v'+editing.version+' · '+jobStates[editing.state]+(editing.state==='published'?'。保存修改后转为草稿，需重新发布。':'')"/>
        <div v-else class="import-line"><a-select show-search option-filter-prop="label" v-model:value="legacySelection" :options="legacyChoices" placeholder="从已有命令模板或脚本复制" style="flex:1"/><a-button :disabled="!legacySelection" @click="importLegacy">复制到草稿</a-button></div>
        <a-form layout="vertical" class="spaced">
          <h3>定义与适用范围</h3>
          <div class="form-grid">
            <a-form-item label="作业名称" required><a-input id="managed-job-name" v-model:value="form.name" :maxlength="100"/></a-form-item>
            <a-form-item label="类型"><a-select v-model:value="form.kind" :options="options(labels)"/></a-form-item>
            <a-form-item label="风险"><a-select v-model:value="form.risk" :options="options(riskLabels)"/></a-form-item>
            <a-form-item label="目标环境"><a-select v-model:value="form.environments" mode="multiple" :options="options(envs)"/></a-form-item>
            <a-form-item label="限定 SSH 账号（可选）"><a-input v-model:value="form.ssh_user" placeholder="留空使用资产上的账号"/></a-form-item>
            <a-form-item label="系统名称包含（可选）"><a-input v-model:value="form.os_match" placeholder="例如 Alibaba Cloud Linux"/></a-form-item>
          </div>
          <a-form-item label="限定主机（可选）"><a-select v-model:value="form.allowed_host_ids" mode="multiple" show-search option-filter-prop="label" :options="allHostOptions" placeholder="留空表示符合其他条件的已授权资产"/></a-form-item>
          <a-form-item label="用途与影响范围" required><a-textarea v-model:value="form.description" :maxlength="1000" :rows="2"/></a-form-item>
          <a-form-item label="脚本正文" required><a-textarea v-model:value="form.source" :rows="6" class="mono" spellcheck="false"/><div class="hint">{{ usage[form.kind] }} 请勿在脚本或参数里写凭据。</div></a-form-item>
          <div class="section-line"><h3>运行参数</h3><a-button size="small" :disabled="form.parameters.length>=10" @click="form.parameters.push({name:'',label:'',type:'text',required:true})">添加参数</a-button></div>
          <div v-for="(field,index) in form.parameters" :key="index" class="parameter-box">
            <div class="section-line"><strong>位置 {{index+1}}</strong><a-button type="link" danger size="small" @click="form.parameters.splice(index,1)">移除</a-button></div>
            <div class="form-grid">
              <a-form-item label="变量名"><a-input v-model:value="field.name"/></a-form-item>
              <a-form-item label="显示名称"><a-input v-model:value="field.label"/></a-form-item>
              <a-form-item label="类型"><a-select v-model:value="field.type" :options="options({text:'文本',integer:'整数',enum:'选项'})" @change="delete field.default"/></a-form-item>
              <a-form-item label="必填"><a-switch v-model:checked="field.required"/></a-form-item>
              <a-form-item v-if="field.type==='enum'" label="选项（每行一项）"><a-textarea :value="(field.choices||[]).join('\n')" @update:value="v=>field.choices=v.split('\n')"/></a-form-item>
              <a-form-item label="默认值（可选）"><a-input-number v-if="field.type==='integer'" :value="field.default" @update:value="v=>setDefault(field,v)" :precision="0"/><a-input v-else :value="field.default" @update:value="v=>setDefault(field,v)"/></a-form-item>
              <a-form-item v-if="field.type==='integer'" label="最小值"><a-input-number v-model:value="field.min" :precision="0"/></a-form-item>
              <a-form-item v-if="field.type==='integer'" label="最大值"><a-input-number v-model:value="field.max" :precision="0"/></a-form-item>
            </div>
          </div>
          <h3 class="spaced">验证与批次控制</h3>
          <div class="form-grid">
            <a-form-item label="服务验证（生产或修改类必填）"><a-input v-model:value="form.verify_service" placeholder="nginx.service"/></a-form-item>
            <a-form-item label="单机脚本超时（秒）"><a-input-number v-model:value="form.timeout" :min="5" :max="600" :precision="0"/></a-form-item>
            <a-form-item label="首批台数"><a-input-number v-model:value="form.canary_count" :min="1" :max="4" :precision="0"/></a-form-item>
            <a-form-item label="每批台数"><a-input-number v-model:value="form.batch_size" :min="1" :max="30" :precision="0"/></a-form-item>
            <a-form-item label="同时运行台数"><a-input-number v-model:value="form.concurrency" :min="1" :max="4" :precision="0"/></a-form-item>
            <a-form-item label="累计失败停止台数"><a-input-number v-model:value="form.failure_limit" :min="1" :max="300" :precision="0"/></a-form-item>
          </div>
        </a-form>
        <a-collapse v-if="revisions.length" class="spaced"><a-collapse-panel key="history" header="版本记录"><a-list :data-source="revisions"><template #renderItem="{item}"><a-list-item>v{{item.version}} · {{jobStates[item.state]}} · {{item.actor}} · {{time(item.created_at)}} <a-button type="link" @click="form=clone(item.definition)">复制此版本到编辑区</a-button></a-list-item></template></a-list></a-collapse-panel></a-collapse>
      </template>
    </a-drawer>
  </div>
</template>

<script setup>
import {computed, onMounted, ref} from 'vue'
import {useRouter} from 'vue-router'
import {useStore} from 'vuex'
import {Modal,message} from 'ant-design-vue'
import api from '@/api'
import {options,time} from '@/utils/ops'

const store=useStore(), router=useRouter(), admin=computed(()=>store.getters.userInfo?.is_superuser)
const jobs=ref([]), hosts=ref([]), search=ref(''), stateFilter=ref('all'), loading=ref(false), error=ref(false), hostsLoading=ref(false), busy=ref(false)
const current=ref(null), runOpen=ref(false), hostIds=ref([]), values=ref({}), reason=ref(''), plan=ref(null), planInput=ref(null)
const editing=ref(null), form=ref(null), revisions=ref([]), editorOpen=ref(false), legacySelection=ref(null), legacyChoices=ref([])
const usage={command:'命令模板使用独立的 {{参数名}}。',shell:'Shell 通过 $1、$2 按参数顺序读取。',python:'Python 通过 sys.argv[1]、sys.argv[2] 按参数顺序读取。'}
const labels={command:'命令模板',shell:'Shell',python:'Python 3'}, riskLabels={read_only:'只读',change:'修改类'}
const jobStates={draft:'草稿',published:'已发布',disabled:'已停用'}, envs={test:'测试',staging:'验收环境',production:'生产',other:'其他'}
const clone=x=>JSON.parse(JSON.stringify(x)), drawerWidth='min(760px, 100vw)'
const hostMap=computed(()=>Object.fromEntries(hosts.value.map(h=>[h.id,h])))
const hostLabel=h=>({value:h.id,label:h.name+' · '+h.ip_addr+' · '+(envs[h.environment]||h.environment)})
const allHostOptions=computed(()=>hosts.value.map(hostLabel))
const visible=computed(()=>jobs.value.filter(j=>(!admin.value||stateFilter.value==='all'||j.state===stateFilter.value)
  && (j.definition.name+' '+j.definition.description).toLowerCase().includes(search.value.trim().toLowerCase())))
const eligible=computed(()=>{
  const d=current.value?.definition
  if(!d)return []
  return hosts.value.filter(h=>d.environments.includes(h.environment)&&(!d.allowed_host_ids.length||d.allowed_host_ids.includes(h.id))
    &&(!d.ssh_user||h.username===d.ssh_user)&&(!d.os_match||(h.os_name||'').toLowerCase().includes(d.os_match.toLowerCase()))).map(hostLabel)
})
const protectedRun=computed(()=>current.value?.definition.risk==='change'||hostIds.value.some(id=>hostMap.value[id]?.environment==='production'))
const dirty=computed(()=>JSON.stringify(form.value)!==JSON.stringify(editing.value?.definition))
const defaults=()=>({name:'系统信息查询',kind:'shell',risk:'read_only',description:'只读查询 Linux 内核、启动时间及根文件系统用量。',environments:['test'],allowed_host_ids:[],ssh_user:'',os_match:'',source:'#!/bin/bash\nset -e\nuname -sr\nuptime\ndf -h /\n',parameters:[],verify_service:'',timeout:60,concurrency:2,canary_count:1,batch_size:4,failure_limit:1})

async function load(){loading.value=true;error.value=false;try{jobs.value=(await api.get('/mtask/jobs')).data}catch{error.value=true}finally{loading.value=false}}
async function loadHosts(){hostsLoading.value=true;try{hosts.value=(await api.get('/host/')).data}finally{hostsLoading.value=false}}
async function begin(job){current.value=job;hostIds.value=[];reason.value='';plan.value=null;values.value=Object.fromEntries(job.definition.parameters.filter(x=>Object.hasOwn(x,'default')).map(x=>[x.name,x.default]));runOpen.value=true;await loadHosts()}
async function preview(){busy.value=true;try{const body=clone({host_ids:hostIds.value,parameters:Object.fromEntries(Object.entries(values.value).filter(([,v])=>v!=null)),reason:reason.value});plan.value=(await api.post('/mtask/jobs/'+current.value.id+'/preview',body)).data;planInput.value=body}finally{busy.value=false}}
async function submit(){busy.value=true;try{const {data}=await api.post('/mtask/jobs/'+current.value.id+'/run',{...planInput.value,token:plan.value.token,confirmed:true});runOpen.value=false;router.push({name:'TaskCenter',query:{task:String(data.id)}})}finally{busy.value=false}}
async function edit(job){
  form.value=defaults();editing.value=null;revisions.value=[];legacySelection.value=null
  if(job){const {data}=await api.get('/mtask/jobs/'+job.id);editing.value=data;form.value=clone(data.definition);revisions.value=data.revisions||[]}
  editorOpen.value=true
  await loadHosts()
  if(!job){const [a,b]=await Promise.all([api.get('/mtask/templates'),api.get('/mtask/scripts')]);legacyChoices.value=[...a.data.map(x=>({value:'template:'+x.id,label:'命令模板 · '+x.name})),...b.data.map(x=>({value:'script:'+x.id,label:'脚本 · '+x.name}))]}
}
async function importLegacy(){
  const [kind,id]=legacySelection.value.split(':')
  const {data}=await api.get('/mtask/'+(kind==='template'?'templates':'scripts')+'/'+id)
  const source=data.template||data.script
  form.value={...defaults(),name:source.name,kind:kind==='template'?'command':source.language,source:source.cmd||source.source,
    description:source.description||'从已有内容复制，请确认用途和风险后发布。',risk:'change',parameters:(source.parameters||[]).map(p=>({name:p.name,label:p.label||p.name,type:p.type||'text',required:p.required!==false,choices:p.choices,...(p.default!==undefined?{default:p.type==='integer'?Number(p.default):String(p.default)}:{})}))}
  message.info('已复制为草稿，请核对风险和验证条件')
}
function setDefault(field,value){if(value===null||value==='')delete field.default;else field.default=value}
async function save(){
  busy.value=true
  try{const id=editing.value?.id, definition=clone(form.value)
    for(const p of definition.parameters){
      if(p.choices)p.choices=p.choices.map(x=>x.trim()).filter(Boolean)
      if(p.min==null)delete p.min
      if(p.max==null)delete p.max
    }
    const {data}=await api.post('/mtask/jobs'+(id?'/'+id:''),id?{action:'save',version:editing.value.version,definition}:{definition})
    await edit(data);await load();message.success('草稿已保存')
  }finally{busy.value=false}
}
function transition(action){Modal.confirm({title:action==='publish'?'发布此作业版本？':'停用此作业？',content:action==='publish'?'发布后，拥有目标主机权限的用户可提交此版本。':'停用仅阻止新提交，已经提交的任务保留原快照。',okText:'确认',cancelText:'返回',onOk:async()=>{const {data}=await api.post('/mtask/jobs/'+editing.value.id,{action,version:editing.value.version});await edit(data);await load()}})}
onMounted(load)
</script>

<style scoped>
.job-workspace{max-width:1580px;margin:0 auto}.hero{background:#10213b;color:#eef6ff;padding:30px;border-radius:14px;display:flex;justify-content:space-between;gap:18px;align-items:center}.hero h1{color:#fff;font-size:29px;margin:6px 0}.hero p{color:#bdcde2;margin:0}.eyebrow{font-size:11px;letter-spacing:2px;color:#68d4c0}.summary-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:18px 0}.summary-grid>div,.summary-grid>a{padding:16px 20px;background:var(--surface);border:1px solid var(--line);border-radius:10px;color:var(--muted)}.summary-grid strong{display:block;color:var(--text);font-size:27px;margin-top:3px}.summary-grid .go{font-size:17px;margin-top:9px}.library{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:20px}.library-bar,.card-bottom,.section-line,.editor-footer{display:flex;align-items:center;justify-content:space-between;gap:10px}.library-bar h2{margin:0 0 14px}.library-bar small,.card-top span,.card-meta,.card-bottom span,.hint{color:var(--muted);font-size:12px}.job-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,300px),1fr));gap:14px}.job-card{border:1px solid var(--line);border-radius:10px;padding:16px;min-width:0}.card-top span:last-child{float:right}.job-card h3{margin:12px 0 5px}.job-card p{min-height:42px;color:var(--muted);overflow-wrap:anywhere}.card-meta{margin-top:9px}.card-bottom{margin-top:12px;border-top:1px solid var(--line);padding-top:12px}.spaced{margin-top:18px}.form-grid :deep(.ant-input-number){width:100%}.form-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));column-gap:16px}.parameter-box,.plan-targets,.host-order{background:var(--surface-alt);border:1px solid var(--line);border-radius:8px;padding:10px;margin:10px 0}.plan-targets{max-height:190px;overflow:auto;line-height:28px}.host-order span{margin-left:12px;color:var(--muted)}.import-line{display:flex;gap:8px}.mono,.code{font-family:ui-monospace,monospace}.code{white-space:pre-wrap;overflow-wrap:anywhere;max-height:260px;overflow:auto}.hint{margin-top:8px}@media(max-width:720px){.summary-grid{grid-template-columns:repeat(2,1fr)}.hero,.library-bar{align-items:flex-start;flex-direction:column}.hero{padding:20px}.form-grid{grid-template-columns:1fr}.library{padding:12px}}
</style>
