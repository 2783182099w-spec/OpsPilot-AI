<template>
  <div>
    <a-space wrap style="margin:12px 0"><a-select v-model:value="metric" :options="metricOptions" style="width:210px" @change="load"/><a-select v-model:value="hours" :options="[{value:1,label:'最近 1 小时'},{value:6,label:'最近 6 小时'},{value:24,label:'最近 24 小时'},{value:168,label:'最近 7 天'}]" style="width:170px" @change="load"/><a-button :loading="loading" @click="load">刷新趋势</a-button></a-space>
    <a-alert v-if="error" type="warning" :message="error"/><div ref="element" style="height:330px"></div>
    <small>缺失样本显示为断点。CPU、网络速率需要至少两个采样点，首次接入请等待约 2 分钟。磁盘与 inode 展示使用率最高的文件系统。</small>
  </div>
</template>
<script setup>
import {ref,onMounted,onBeforeUnmount,watch} from 'vue'
import { init } from '@/utils/charts'
import api from '@/api'
const props=defineProps({hostId:{type:Number,required:true},initialMetric:{type:String,default:'cpu'}})
const element=ref(),metric=ref(['up','cpu','memory','disk','inode','load','rx','tx','collector_failures'].includes(props.initialMetric)?props.initialMetric:'cpu'),hours=ref(6),loading=ref(false),error=ref('')
const metricOptions=[{value:'cpu',label:'CPU 使用率（%）'},{value:'memory',label:'内存使用率（%）'},{value:'disk',label:'文件系统使用率（%）'},{value:'inode',label:'inode 使用率（%）'},{value:'load',label:'一分钟负载'},{value:'rx',label:'网络接收（B/s）'},{value:'tx',label:'网络发送（B/s）'},{value:'up',label:'采集可用性（1/0）'},{value:'collector_failures',label:'失败采集器数量'}]
let chart,sequence=0
async function load(){const current=++sequence;loading.value=true;error.value='';try{const {data}=await api.get(`/monitoring/hosts/${props.hostId}/history`,{params:{metric:metric.value,hours:hours.value}});if(current!==sequence)return;chart?.setOption({animation:false,title:{text:data.points.length?'':'此时段暂无样本',left:'center'},tooltip:{trigger:'axis'},grid:{left:65,right:25,bottom:45,top:40},xAxis:{type:'time'},yAxis:{type:'value',scale:true},series:[{type:'line',showSymbol:false,connectNulls:false,data:data.points.map(([t,v])=>[t*1000,v])}]},true)}catch{if(current===sequence){error.value='趋势暂不可用，采集服务可能尚未接入或暂时失联';chart?.clear()}}finally{if(current===sequence)loading.value=false}}
function resize(){chart?.resize()}
onMounted(()=>{chart=init(element.value);window.addEventListener('resize',resize);load()})
watch(()=>props.hostId,load)
onBeforeUnmount(()=>{sequence++;window.removeEventListener('resize',resize);chart?.dispose();chart=null})
</script>
