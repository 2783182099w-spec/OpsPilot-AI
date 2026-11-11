<template>
  <section class="investigation">
    <header>
      <div><p class="ops-eyebrow">INCIDENT WORKFLOW</p><h3>排查与恢复</h3></div>
      <a-button size="small" type="text" :loading="loading" @click="$emit('reload')">刷新进度</a-button>
    </header>
    <div v-if="loading&&!workspace" class="busy"><a-spin/></div>
    <a-alert v-else-if="!workspace" type="warning" message="排查信息加载失败，点击刷新进度重试。" show-icon/>
    <template v-else>
      <div class="steps" aria-label="处理进度">
        <div class="step"><span>01</span><div><strong>确认告警</strong><small>{{ conditionLabel }}</small></div></div>
        <div class="step" :class="{ highlighted:diagnosis && diagnosis.status!=='completed' }"><span>02</span><div><strong>诊断取证</strong><small>{{ diagnosis ? labels[diagnosis.status] : '尚未排查' }}</small></div></div>
        <div class="step"><span>03</span><div><strong>下一步</strong><small>{{ recoveryLabel }}</small></div></div>
      </div>
      <div v-if="canOperate" class="recommendation">
        <div><small>推荐检查</small><strong>{{workspace.recommendation?.name || '需要选择诊断模板'}}</strong></div>
        <a-button v-if="workspace.recommendation" type="primary" :loading="acting" :disabled="!workspace.can_diagnose" @click="$emit('diagnose')">{{ diagnosis ? '重新诊断' : '一键诊断' }}</a-button>
        <div v-if="workspace.recommendation?.checks?.length" class="plan-checks" aria-label="计划检查">
          <a-tag v-for="(check,index) in workspace.recommendation.checks" :key="index">{{check.title || check}}</a-tag>
        </div>
        <p v-if="workspace.recommendation_reason" class="ops-muted">{{workspace.recommendation_reason}}
          <template v-if="!treeOptions.length">
            <router-link v-if="admin" :to="{name:'DiagnosticTrees'}">打开诊断树</router-link>
            <span v-else>当前没有可选模板，请联系管理员。</span>
          </template>
        </p>
        <p v-if="!workspace.worker_online" class="ops-muted">任务执行器离线，请启动执行器后重试。</p>
        <p v-else-if="!workspace.can_diagnose&&diagnosis" class="ops-muted">排查进行中，结果会自动更新。</p>
        <details v-if="treeOptions.length" class="alternate" :open="!workspace.recommendation">
          <summary>{{workspace.recommendation?'选择其他诊断模板':'选择诊断模板'}}</summary>
          <a-space wrap><a-select v-model:value="treeId" :options="treeOptions" style="min-width:190px" placeholder="选择模板"/>
            <a-button :disabled="!treeId||!workspace.can_diagnose" :loading="acting" @click="$emit('diagnose',treeId)">运行模板</a-button>
          </a-space>
        </details>
      </div>
      <div v-if="workflow" class="latest">
        <div class="run-header"><strong>{{workflow.policy}} · v{{workflow.version}}</strong>
          <a-tag :color="workflow.status==='recovered'?'green':workflow.status==='manual'?'orange':'blue'">{{workflowLabel}}</a-tag>
        </div>
        <p>{{workflow.reason || '正在运行已绑定的只读诊断树'}}</p>
        <p v-if="workflow.branch_label" class="ops-muted">命中结论 · {{workflow.branch_label}}（{{workflow.branch}}）</p>
        <p v-if="workflow.action" class="ops-muted">受控动作 · {{workflow.action?.name}} · {{finding.host_name}}</p>
        <a-button v-if="workflow.can_approve" type="primary" :loading="acting" @click="$emit('approve-workflow',workflow.task_id)">授权执行本次修复</a-button>
        <p v-else-if="workflow.task_status==='awaiting_approval'" class="ops-muted">由另一名管理员在此授权执行。</p>
        <router-link v-if="workflow.task_status==='awaiting_approval'&&canOperate" class="task-link" :to="{name:'RepairApprovals'}">查看人工审批队列</router-link>
        <router-link v-if="workflow.task_id" class="task-link" :to="{name:'TaskCenter',query:{task:String(workflow.task_id)}}">修复任务 #{{workflow.task_id}}</router-link>
      </div>
      <div v-if="diagnosis" class="latest">
        <div class="run-header"><strong>{{diagnosis.tree_name || '只读诊断'}}</strong><a-tag :color="diagnosis.status==='completed' ? 'default' : color(diagnosis.status)">{{labels[diagnosis.status] || diagnosis.status}}</a-tag></div>
        <div v-if="diagnosis.summary" class="finding-summary" :class="diagnosis.summary.outcome">
          <div class="run-header"><strong>{{outcomes[diagnosis.summary.outcome]}}</strong><small v-if="diagnosis.finished_at">{{time(diagnosis.finished_at)}}</small></div>
          <p v-if="diagnosis.conclusion" class="conclusion">{{diagnosis.conclusion}}</p>
          <p class="next-step"><strong>下一步</strong><span>{{diagnosis.summary.next_step}}</span></p>
        </div>
        <p v-else-if="diagnosis.conclusion" class="conclusion">{{diagnosis.conclusion}}</p>
        <ul v-if="diagnosis.checks.length" class="checks">
          <li v-for="(check,index) in orderedChecks" :key="index">
            <a-tag :color="checkColors[check.state]">{{checkStates[check.state]}}</a-tag>
            <div><strong>{{check.title || checkNames[check.kind] || check.kind}}</strong>
              <small v-if="check.scope">{{check.scope}}</small>
              <small v-if="check.observed">复检值 <b>{{check.observed}}</b> · 阈值 {{check.threshold}}</small>
              <small v-else>{{check.evidence}}</small>
            </div>
          </li>
        </ul>
        <details v-if="diagnosis.output"><summary>查看执行输出</summary><pre>{{diagnosis.output}}</pre></details>
        <router-link v-if="diagnosis.can_open_task" class="task-link" :to="{name:'TaskCenter',query:{task:String(diagnosis.task_id)}}">任务 #{{diagnosis.task_id}}</router-link>
      </div>
      <div v-if="repair" class="latest repair">
        <div class="run-header"><strong>最近修复 · {{automationLabels[repair.automation_scope]||'人工审批'}}</strong><a-tag :color="color(repair.status)">{{repair.status==='completed' ? '修复动作已完成' : (labels[repair.status] || repair.status)}}</a-tag></div>
        <p v-if="repair.status==='completed'" class="ops-muted">{{finding.condition==='recovered'?'已完成执行和复检':'动作已完成，等待恢复复检。'}}</p>
        <p v-if="repair.escalated_at" class="handoff-note">已转人工 · {{time(repair.escalated_at)}}</p>
        <details v-if="repair.output"><summary>查看修复输出</summary><pre>{{repair.output}}</pre></details>
        <router-link v-if="repair.can_open_task" class="task-link" :to="{name:'TaskCenter',query:{task:String(repair.task_id)}}">任务 #{{repair.task_id}}</router-link>
      </div>
      <div v-if="canOperate&&(!workflow||workflow.mode==='diagnose')" class="recovery">
        <div class="recovery-title"><strong>{{workspace.repair.action?'本次修复':'人工处理'}}</strong>
          <a-tag v-if="workspace.repair.policy" :color="workspace.repair.policy_enabled?'green':'default'">
            {{workspace.repair.policy_enabled?'以后同类告警已启用自动处理':'以后同类告警未启用自动处理'}}
          </a-tag>
        </div>
        <template v-if="workspace.pending_approval">
          <p><strong>{{workspace.pending_approval.action?.name}}</strong></p>
          <p class="ops-muted">{{workspace.pending_approval.action?.impact}}</p>
          <p class="ops-muted">申请人 {{workspace.pending_approval.actor}} · 等待另一名管理员授权</p>
          <a-button v-if="workspace.pending_approval.can_approve" type="primary" :loading="acting"
            @click="$emit('approve-workflow',workspace.pending_approval.task_id)">授权执行本次修复</a-button>
        </template>
        <template v-else>
          <template v-if="workspace.repair.action">
            <p><strong>{{workspace.repair.action.name}}</strong></p>
            <p class="ops-muted">{{workspace.repair.action.impact}}</p>
          </template>
          <p v-if="workspace.repair.reason" class="ops-muted">{{workspace.repair.reason}}</p>
          <p v-else-if="!admin&&workspace.repair.action" class="ops-muted">由管理员发起修复审批。</p>
          <a-space wrap>
            <a-button v-if="workspace.repair.available" type="primary" :loading="acting" @click="$emit('repair')">发起本次修复授权</a-button>
            <a-button v-if="workspace.repair.once_available" :loading="acting" @click="$emit('authorize-automatic')">仅本次自动处理</a-button>
            <a-button v-if="!workspace.repair.available&&finding.condition!=='recovered'&&!finding.assignee&&diagnosis?.status==='completed'"
              :loading="acting" @click="$emit('take-over')">接手处理</a-button>
          </a-space>
        </template>
        <router-link class="task-link" :to="{name:'RepairApprovals'}">查看人工审批队列</router-link>
        <router-link v-if="workspace.repair.policy" class="task-link" :to="{name:'Diagnostics'}">配置以后同类告警的处理策略</router-link>
      </div>
      <details v-if="history.length" class="history">
        <summary>历史执行记录（{{history.length}}）</summary>
        <ul><li v-for="run in history" :key="run.task_id"><span>{{run.mode==='diagnosis' ? (run.tree_name || '只读诊断') : '修复'}} · {{labels[run.status] || run.status}} · {{time(run.created_at)}}</span>
          <router-link v-if="run.can_open_task" :to="{name:'TaskCenter',query:{task:String(run.task_id)}}">#{{run.task_id}}</router-link></li></ul>
      </details>
    </template>
  </section>
</template>

<script setup>
import {computed,ref,watch} from 'vue'
import {color,states,time} from '@/utils/ops'
const props=defineProps({finding:Object,workspace:Object,trees:{type:Array,default:()=>[]},loading:Boolean,acting:Boolean,canOperate:Boolean,admin:Boolean})
defineEmits(['reload','diagnose','repair','authorize-automatic','approve-workflow','take-over'])
const outcomes={pending:'诊断进行中',abnormal:'发现异常',uncertain:'证据不足',incomplete:'检查未完整完成',evidence:'已采集现场信息',clear:'本次检查未复现异常'}
const automationLabels={workflow:'诊断树绑定',once:'仅本次',policy:'持续策略',approval:'人工审批'}
const labels={...states,completed:'检查已完成'}
const checkNames={snapshot:'主机快照',resource:'资源复检',http:'HTTP 探测',port:'端口检查',systemd:'服务状态',disk:'磁盘检查',log:'日志匹配'}
const checkColors={ok:'green',failed:'red',unknown:'default'}
const checkStates={ok:'通过',failed:'异常',unknown:'待确认'}
const treeId=ref()
watch(()=>props.finding.id,()=>{treeId.value=undefined})
const currentRuns=computed(()=>props.workspace?.runs.filter(run=>run.occurrence===props.finding.occurrences)||[])
const workflow=computed(()=>props.workspace?.workflow)
const workflowLabel=computed(()=>{
  const run=workflow.value
  if(run.task_status==='awaiting_approval'&&run.status==='repairing')return '等待人工审批'
  if(run.status==='manual')return props.finding.manual_required?'待人工接手':'需要人工核对'
  return {diagnosing:'诊断中',diagnosed:'只读诊断完成',repairing:'修复处理中',verifying:'等待外部复检',
    recovered:'已验证恢复',closed:'已结束'}[run.status]
})
const diagnosis=computed(()=>currentRuns.value.find(run=>run.task_id===workflow.value?.diagnosis_task_id)
  || currentRuns.value.find(run=>run.mode==='diagnosis'))
const checkOrder={failed:0,unknown:1,ok:2}
const orderedChecks=computed(()=>[...(diagnosis.value?.checks||[])].sort((a,b)=>checkOrder[a.state]-checkOrder[b.state]))
const repair=computed(()=>currentRuns.value.find(run=>run.mode==='repair'))
const history=computed(()=>props.workspace?.runs.filter(run=>![diagnosis.value?.task_id,repair.value?.task_id].includes(run.task_id))||[])
const treeOptions=computed(()=>props.trees.map(tree=>({value:tree.id,label:tree.name+' · v'+tree.version})))
const conditionLabel=computed(()=>props.finding.condition==='unknown'&&diagnosis.value?.summary?.outcome==='abnormal'
  ? '诊断已确认异常' : ({active:'仍在触发',recovered:'已恢复',unknown:'信号待确认'})[props.finding.condition])
const recoveryLabel=computed(()=>props.finding.condition==='recovered'?'已复检恢复':repair.value?.status==='completed'?'等待监控复检':repair.value?(states[repair.value.status]||repair.value.status):props.workspace?.repair.available?'可以发起授权':diagnosis.value?.status==='completed'?'查看处理建议':'等待诊断')
</script>

<style scoped>
.investigation { background:var(--surface-alt); border:1px solid var(--line); border-radius:14px; padding:17px; }
header,.run-header { display:flex; justify-content:space-between; align-items:center; gap:8px; }
h3 { margin:0; font-size:18px; color:var(--text); }
.steps { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:7px; margin:15px 0; }
.step { display:flex; align-items:center; gap:8px; background:var(--surface); border:1px solid var(--line); border-radius:9px; padding:8px; min-width:0; }
.step.highlighted { border-color:var(--accent); }
.step span { color:var(--accent); font:650 12px ui-monospace,monospace; }
.step strong,.step small,.checks small,.recommendation strong,.recommendation small { display:block; }
.step small,.checks small,.recommendation small { color:var(--muted); }
.recommendation,.latest { background:var(--surface); border:1px solid var(--line); border-radius:10px; padding:12px; }
.recommendation { display:flex; flex-wrap:wrap; align-items:center; justify-content:space-between; gap:10px; }
.plan-checks{flex-basis:100%;display:flex;gap:6px;flex-wrap:wrap}
.recommendation p,.alternate { flex-basis:100%; margin:0; }
.alternate>div { margin-top:8px; }
.latest,.recovery,.history { margin-top:12px; }
.finding-summary { margin-top:10px; border-left:3px solid var(--accent); background:var(--surface-alt); padding:9px 12px; border-radius:5px; }
.finding-summary.abnormal { border-left-color:var(--danger); }
.finding-summary.incomplete,.finding-summary.uncertain { border-left-color:#d79132; }
.finding-summary small{color:var(--muted)}
.next-step { display:flex; gap:8px; margin:5px 0 0; }.next-step strong{flex:none;color:var(--accent)}
.recovery-title{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:6px}
.handoff-note{color:var(--danger);margin:6px 0}
.conclusion { margin:6px 0 10px; color:var(--text); }
.checks,.history ul { list-style:none; margin:8px 0; padding:0; }
.checks li { display:flex; align-items:flex-start; gap:8px; border-top:1px solid var(--line); padding:8px 0; }
.history li { display:flex; justify-content:space-between; gap:8px; padding:5px 0; }
summary { cursor:pointer; color:var(--accent); }
pre { white-space:pre-wrap; overflow-wrap:anywhere; max-height:220px; overflow:auto; background:var(--surface-alt); border-radius:8px; padding:10px; margin:8px 0; }
.task-link { display:inline-block; margin-top:6px; }
.recovery p { margin:0; font-size:12px; }
.busy { padding:18px; text-align:center; }
@media(max-width:500px){.step {display:block}.step small{font-size:10px}.investigation{padding:12px}}
</style>
