<template>
  <a-drawer v-model:open="open" title="发布快速开始" :width="'min(720px, 100vw)'">
    <a-alert type="info" show-icon message="第一次可以用一台测试主机手工提交制品。验证发布流程后，再接入 CI 自动上报。"/>
    <a-steps class="steps" direction="vertical" size="small" :items="steps"/>
    <a-button v-if="admin" type="primary" @click="goApps">前往登记应用</a-button>
    <a-collapse class="examples" :default-active-key="['example']">
      <a-collapse-panel key="example" header="案例 · demo-web 发布参数">
        <a-descriptions size="small" :column="1">
          <a-descriptions-item label="应用 / 环境">demo-web / 测试</a-descriptions-item>
          <a-descriptions-item label="systemd 单元">demo-web.service</a-descriptions-item>
          <a-descriptions-item label="ExecStart"><code>/opt/opspilot-releases/demo-web/current</code></a-descriptions-item>
          <a-descriptions-item label="健康检查">127.0.0.1:8080/health</a-descriptions-item>
          <a-descriptions-item label="制品示例">https://artifacts.example.com/demo-web/1.0.0</a-descriptions-item>
        </a-descriptions>
        <p class="ops-muted">以上为示例值。目标机需先准备 systemd 服务，制品需上传到自己的可信 HTTPS 来源。</p>
      </a-collapse-panel>
      <a-collapse-panel key="ci" header="Linux CI 模板 · 复制到现有流水线">
        <p class="ops-muted">配置 CI 密钥 <code>OPSPILOT_CI_TOKEN</code>，以及 <code>RELEASE_VERSION</code>、<code>ARTIFACT_FILE</code>、<code>ARTIFACT_URL</code>。远程 Runner 须将 <code>OPSPILOT_CI_URL</code> 设为它可访问的 HTTPS 上报地址。</p>
        <a-space wrap class="template-actions">
          <a-select v-if="apps.length" v-model:value="application" :options="appOptions" placeholder="选择已登记应用" style="min-width:195px"/>
          <a-button @click="copyTemplate">复制 CI 模板</a-button>
        </a-space>
        <pre tabindex="0" aria-label="Linux CI 上报模板">{{template}}</pre>
      </a-collapse-panel>
    </a-collapse>
  </a-drawer>
</template>

<script setup>
import {computed,ref,watch} from 'vue'
import {message} from 'ant-design-vue'
import {ciShell,copyText} from '@/utils/releaseGuide'

const props=defineProps({apps:{type:Array,default:()=>[]},admin:Boolean})
const emit=defineEmits(['applications'])
const open=defineModel({type:Boolean,default:false})
const application=ref()
const steps=[
  {title:'登记应用',description:'选择 CMDB 测试主机，填写 systemd 单元、制品来源和健康检查。'},
  {title:'运行发布预检',description:'核对 SSH、发布目录与 ExecStart，在任务中心查看结果。'},
  {title:'准备制品',description:'上传可执行制品。第一次可手动填写下载地址和 SHA-256；CI 接入后可直接选已上报版本。'},
  {title:'预览并提交审批',description:'确认目标及首批主机。独立管理员批准后先执行首批，验证通过后再放行剩余主机。'},
]
const appOptions=computed(()=>props.apps.filter(app=>app.managed).map(app=>({value:app.application,label:app.application})))
const template=computed(()=>ciShell(application.value||'demo-web'))
watch(appOptions,options=>{if(!options.some(item=>item.value===application.value))application.value=options[0]?.value},{immediate:true})
function goApps(){open.value=false;emit('applications')}
async function copyTemplate(){
  try{await copyText(template.value);message.success('已复制 CI 模板')}
  catch{message.warning('剪贴板不可用，请手动选择模板文本复制')}
}
</script>

<style scoped>
.steps{margin:20px 0 8px}
.examples{margin-top:17px}
.template-actions{margin-bottom:10px}
pre{background:var(--surface-alt);padding:12px;border-radius:8px;white-space:pre-wrap;overflow-wrap:anywhere;max-height:330px;overflow:auto}
code{overflow-wrap:anywhere}
</style>
