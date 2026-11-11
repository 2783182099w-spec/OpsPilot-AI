<template><div><a-page-header :title="data.host?.name||'主机工作台'" :sub-title="data.host?.ip_addr" @back="router.push({name:'Host'})"/>
<a-space style="margin:12px 0"><a-button @click="load">刷新</a-button><a-button v-if="store.getters.canOperate" type="primary" @click="inspect">健康巡检</a-button><a-button v-if="store.getters.canOperate" @click="router.push({name:'Console',params:{host_id:id}})">SSH 终端</a-button><a-button @click="router.push({name:'Alerts',query:{host_id:id}})">此主机告警</a-button></a-space>
<a-descriptions bordered :column="{xs:1,sm:2,lg:3}"><a-descriptions-item v-for="field in fields" :key="field.key" :label="field.label">{{data.host?.[field.key]||'—'}}</a-descriptions-item></a-descriptions>
<a-tabs><a-tab-pane key="metrics" tab="指标趋势"><MetricChart v-if="data.host&&data.target?.enabled" :host-id="id"/><a-alert v-else type="info" message="该主机尚未启用持续监控，可在监控中心配置接入；SSH 健康巡检仍可独立使用。"/></a-tab-pane><a-tab-pane key="alerts" tab="最近告警"><a-table :data-source="data.alerts" :columns="[{title:'告警',dataIndex:'title'},{title:'故障状态',key:'condition'},{title:'处理状态',key:'state'}]" row-key="id"><template #bodyCell="{column,record}"><template v-if="column.key==='condition'">{{conditions[record.condition]}}</template><template v-if="column.key==='state'">{{states[record.state]}}</template></template></a-table></a-tab-pane><a-tab-pane key="tasks" tab="巡检与命令历史"><a-table :data-source="data.tasks" :columns="[{title:'任务',key:'id'},{title:'类型',key:'kind'},{title:'执行状态',key:'status'},{title:'时间',key:'time'},{title:'命令',dataIndex:'command'}]" row-key="id"><template #bodyCell="{column,record}"><template v-if="column.key==='id'"><a @click="router.push({name:'TaskCenter',query:{task:String(record.id)}})">#{{record.id}}</a></template><template v-if="column.key==='kind'">{{kinds[record.kind]}}</template><template v-if="column.key==='status'">{{states[record.status]}}</template><template v-if="column.key==='time'">{{time(record.created_at)}}</template></template></a-table></a-tab-pane><a-tab-pane key="changes" tab="资产变更"><p v-for="(change,i) in data.changes" :key="i">{{time(change.created_at)}} {{change.actor__username}}<pre>{{change.changes}}</pre></p></a-tab-pane></a-tabs></div></template>
<script setup>
import {ref,computed,onMounted} from 'vue'
import {useRoute,useRouter} from 'vue-router'
import {useStore} from 'vuex'
const store = useStore()
import {Modal} from 'ant-design-vue'
import api from '@/api'
import MetricChart from '@/components/MetricChart.vue'
import {time,states,kinds} from '@/utils/ops'
const route=useRoute(),router=useRouter(),id=computed(()=>Number(route.params.id)),data=ref({alerts:[],tasks:[],changes:[]})
const fields=[{key:'environment',label:'环境'},{key:'business',label:'业务'},{key:'owner_label',label:'负责人'},{key:'os_name',label:'系统'},{key:'purpose',label:'用途'},{key:'last_checked_at',label:'最近 SSH 检查'}],conditions={active:'触发中',unknown:'待确认',recovered:'已恢复'}
async function load(){data.value=(await api.get(`/monitoring/hosts/${id.value}/workbench`)).data}
function inspect(){Modal.confirm({title:'检查当前主机健康状态',content:'只读检查 CPU、内存、磁盘、负载与系统信息。',okText:'开始巡检',cancelText:'取消',onOk:async()=>{const {data}=await api.post('/mtask/tasks',{kind:'inspection',host_ids:[id.value],options:{profile:'health'},confirmed:true});router.push({name:'TaskCenter',query:{task:String(data.id)}})}})}
onMounted(load)
</script>
