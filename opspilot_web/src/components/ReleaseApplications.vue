<template>
  <a-drawer v-model:open="open" title="应用与 CI 接入" :width="'min(660px, 100vw)'" @after-open-change="afterOpen">
    <a-spin :spinning="loading">
      <template v-if="!form">
        <p class="ops-muted">登记 Linux 发布目标、接收 CI 制品，并在首次发布前检查主机。</p>
        <a-button type="primary" @click="edit()">登记应用</a-button>
        <a-empty v-if="!rows.length&&!legacy.length" description="还没有发布应用" class="spaced"/>
        <article v-for="item in [...rows,...legacy]" :key="item.application" class="ops-panel app-card">
          <header><div><strong>{{item.application}}</strong><small>{{item.unit}} · {{envs[item.environment]}} · {{item.host_ids.length}} 台</small></div>
            <a-tag :color="item.enabled?'green':'default'">{{item.version?(item.enabled?'已启用':'已停用'):'环境配置'}}</a-tag>
          </header>
          <a-space wrap>
            <a-button size="small" @click="edit(item)">{{item.version?'编辑配置':'登记到平台'}}</a-button>
            <a-button v-if="item.enabled" size="small" @click="prepareCheck(item)">发布预检</a-button>
            <a-button v-if="item.version" size="small" @click="showCI(item)">CI 接入</a-button>
          </a-space>
        </article>
      </template>
      <template v-else>
        <a-button type="link" class="back" @click="form=null">返回应用列表</a-button>
        <a-form layout="vertical" :disabled="saving">
          <a-form-item label="应用标识" extra="用于发布目录及 CI 上报路径，仅支持小写字母、数字和连字符。">
            <a-input v-model:value="form.application" :disabled="lockedName" :maxlength="40" placeholder="例如 demo-web"/>
          </a-form-item>
          <a-form-item label="发布环境"><a-select v-model:value="form.environment" :options="options(envs)" @change="form.host_ids=[]"/></a-form-item>
          <a-form-item label="目标 Linux 主机"><a-select v-model:value="form.host_ids" mode="multiple" show-search
            option-filter-prop="label" :options="hostOptions(form.environment)" placeholder="从 CMDB 选择主机"/></a-form-item>
          <a-form-item label="systemd 单元"><a-input v-model:value="form.unit" :maxlength="136" placeholder="demo-web.service"/></a-form-item>
          <a-form-item label="可信制品来源" extra="仅填写 HTTPS 协议与主机，可包含端口。">
            <a-input v-model:value="form.origin" :maxlength="200" placeholder="https://artifacts.example.com"/>
          </a-form-item>
          <div class="health-form">
            <a-form-item label="健康检查端口"><a-input-number v-model:value="form.port" :min="1" :max="65535"/></a-form-item>
            <a-form-item label="健康检查路径"><a-input v-model:value="form.path" :maxlength="151" placeholder="/health"/></a-form-item>
          </div>
          <a-form-item label="启用应用"><a-switch v-model:checked="form.enabled"/></a-form-item>
          <p v-if="form.application" class="ops-muted">目标机 ExecStart 路径 /opt/opspilot-releases/{{form.application}}/current</p>
          <a-button type="primary" :loading="saving" @click="save">保存应用</a-button>
        </a-form>
      </template>
    </a-spin>
  </a-drawer>
  <a-modal :open="!!ciApp" title="CI 制品接入" :footer="null" @cancel="closeCI">
    <template v-if="ciApp">
      <p>应用 <strong>{{ciApp.application}}</strong> · {{ciApp.ci_enabled?'已生成令牌':'尚未生成令牌'}}</p>
      <p class="ops-muted">CI 在上传制品后调用下方接口。令牌保存在 CI 的密钥库，接口只登记版本、URL 和摘要。</p>
      <a-input :value="ciEndpoint" readonly aria-label="CI 上报地址"/>
      <a-space wrap class="spaced">
        <a-popconfirm :title="ciApp.ci_enabled?'生成新令牌会让旧令牌立即失效，继续吗？':'为此应用生成 CI 令牌？'" @confirm="changeToken('rotate')">
          <a-button type="primary" :loading="saving" :disabled="!ciApp.enabled">{{ciApp.ci_enabled?'轮换令牌':'生成令牌'}}</a-button>
        </a-popconfirm>
        <a-popconfirm v-if="ciApp.ci_enabled" title="确认撤销此应用的 CI 令牌？" @confirm="changeToken('revoke')">
          <a-button danger :disabled="saving">撤销令牌</a-button>
        </a-popconfirm>
      </a-space>
      <div v-if="token" class="spaced">
        <p class="ops-muted">令牌只显示这一次。关闭弹窗前请复制到 CI 密钥库。</p>
        <a-input-password :value="token" readonly aria-label="一次性 CI 令牌"/>
      </div>
      <details class="spaced"><summary>Linux CI 上报模板</summary>
        <p class="ops-muted">模板已带入本应用上报地址。远程 Runner 使用可访问的 HTTPS 地址，通过 OPSPILOT_CI_URL 覆盖。</p>
        <a-button size="small" @click="copyTemplate">复制 CI 模板</a-button>
        <pre tabindex="0" aria-label="应用专用 CI 上报模板">{{example}}</pre>
      </details>
    </template>
  </a-modal>
  <a-modal :open="!!checkApp" title="执行发布预检" ok-text="提交预检" :confirm-loading="saving" @cancel="checkApp=null" @ok="submitCheck">
    <template v-if="checkApp">
      <p>检查 SSH、发布目录权限和 systemd 配置。每次最多 20 台。结果在任务中心查看。</p>
      <a-select v-model:value="checkHosts" mode="multiple" show-search option-filter-prop="label" style="width:100%"
        :options="hostOptions(checkApp.environment).filter(host=>checkApp.host_ids.includes(host.value))" placeholder="选择要预检的主机"/>
    </template>
  </a-modal>
</template>

<script setup>
import {computed,ref} from 'vue'
import {useRouter} from 'vue-router'
import {message} from 'ant-design-vue'
import api from '@/api'
import {ciEndpoint as endpointFor,ciShell,copyText} from '@/utils/releaseGuide'
import {options} from '@/utils/ops'

const props=defineProps({legacy:{type:Array,default:()=>[]}})
const emit=defineEmits(['updated'])
const open=defineModel({type:Boolean,default:false})
const router=useRouter()
const rows=ref([]),hosts=ref([]),loading=ref(false),saving=ref(false),form=ref(null),lockedName=ref(false)
const ciApp=ref(null),token=ref(''),checkApp=ref(null),checkHosts=ref([])
const envs={test:'测试',staging:'验收',production:'生产'}
const legacy=computed(()=>props.legacy.filter(item=>!item.managed&&!rows.value.some(row=>row.application===item.application))
  .map(item=>({...item,enabled:true})))
const ciEndpoint=computed(()=>ciApp.value?endpointFor(ciApp.value.application):'')
const example=computed(()=>ciApp.value?ciShell(ciApp.value.application):'')
async function copyTemplate(){
  try{await copyText(example.value);message.success('已复制 CI 模板')}
  catch{message.warning('剪贴板不可用，请手动选择模板文本复制')}
}
function hostOptions(environment){
  return hosts.value.filter(host=>host.environment===environment&&host.asset_status!=='retired'&&host.ip_addr&&!/windows/i.test(host.os_name||''))
    .map(host=>({value:host.id,label:host.name+' · '+host.ip_addr}))
}
async function load(){
  loading.value=true
  try{
    const [apps,assets]=await Promise.all([api.get('/mtask/release-applications'),api.get('/host/')])
    rows.value=apps.data;hosts.value=assets.data
  }catch{ /* common API feedback */ }
  finally{loading.value=false}
}
function afterOpen(visible){if(visible){form.value=null;load()}}
function edit(item){
  lockedName.value=Boolean(item)
  form.value=item?{...item,host_ids:[...item.host_ids]}:{application:'',environment:'test',host_ids:[],unit:'',origin:'',port:8080,path:'/health',enabled:true}
}
async function save(){
  saving.value=true
  try{
    await api.post('/mtask/release-applications',form.value)
    form.value=null
    message.success('应用配置已保存')
    await load()
    emit('updated')
  }catch{ /* common API feedback */ }
  finally{saving.value=false}
}
function showCI(item){ciApp.value=item;token.value=''}
function closeCI(){ciApp.value=null;token.value=''}
async function changeToken(action){
  if(!ciApp.value)return
  const name=ciApp.value.application
  saving.value=true
  try{
    const {data}=await api.post('/mtask/release-applications/'+name+'/token',{action})
    if(ciApp.value?.application===name){
      ciApp.value.ci_enabled=data.ci_enabled
      token.value=data.token||''
    }
    await load()
  }catch{ /* common API feedback */ }
  finally{saving.value=false}
}
function prepareCheck(item){checkApp.value=item;checkHosts.value=[]}
async function submitCheck(){
  if(!checkApp.value||!checkHosts.value.length||checkHosts.value.length>20){message.warning('请选择 1 到 20 台主机');return}
  saving.value=true
  try{
    const {data}=await api.post('/mtask/release-applications/'+checkApp.value.application+'/precheck',{host_ids:checkHosts.value,confirmed:true})
    checkApp.value=null
    router.push({name:'TaskCenter',query:{task:String(data.id)}})
  }catch{ /* common API feedback */ }
  finally{saving.value=false}
}
</script>

<style scoped>
.spaced,.app-card{margin-top:13px}
.app-card{padding:12px}
.app-card header{display:flex;justify-content:space-between;gap:10px;margin-bottom:10px}
.app-card small{display:block;margin-top:4px;color:var(--muted)}
.back{padding-left:0;margin-bottom:12px}
.health-form{display:grid;grid-template-columns:130px minmax(0,1fr);gap:10px}
pre{background:var(--surface-alt);padding:9px;white-space:pre-wrap;overflow-wrap:anywhere}
</style>
