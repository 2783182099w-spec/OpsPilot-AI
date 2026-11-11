<template>
  <div>
    <a-alert message="按需连接：读取默认仅限 /tmp、/var/log，上传仅限 /tmp；单文件 10MB、传输超时 60 秒、最多同时 2 个传输。不会自动删除文件。" type="info" show-icon />
    <a-space wrap style="margin:12px 0"><a-input v-model:value="path" style="width:420px" placeholder="远端目录，如 /tmp" /><a-button :loading="busy" @click="list">读取目录</a-button><input type="file" @change="pick" /><a-checkbox v-model:checked="overwrite">确认允许覆盖同名普通文件</a-checkbox><a-button :disabled="!file || busy" @click="upload">上传到当前目录</a-button></a-space>
    <p>当前主机：{{host.name}}。{{checksum ? `最近传输 SHA256：${checksum}` : ''}}</p>
    <a-table :data-source="entries" :columns="columns" row-key="name" :pagination="{pageSize:20}">
      <template #bodyCell="{column,record}"><template v-if="column.key==='action'"><a-button v-if="!record.symlink" @click="record.directory ? enter(record) : download(record)">{{record.directory?'打开目录':'下载'}}</a-button><span v-else>符号链接不允许操作</span></template></template>
    </a-table>
    <a-alert v-if="truncated" type="warning" message="该目录只展示前 200 项，请指定更精确的子目录" />
    <a-button style="margin-top:12px" @click="loadHistory">本地主机传输记录（不连接远端）</a-button>
    <a-table v-if="history.length" :data-source="history" :columns="[{title:'方向',dataIndex:'direction'},{title:'路径',dataIndex:'path'},{title:'字节',dataIndex:'size'},{title:'状态',dataIndex:'status'},{title:'时间',dataIndex:'created_at'}]" row-key="id" size="small" />
  </div>
</template>
<script setup>
import {ref} from 'vue'
import {message,Modal} from 'ant-design-vue'
import api from '@/api'
const props=defineProps({host:{type:Object,required:true}})
const path=ref('/tmp'),entries=ref([]),file=ref(null),busy=ref(false),overwrite=ref(false),checksum=ref(''),truncated=ref(false),history=ref([])
const columns=[{title:'名称',dataIndex:'name'},{title:'字节',dataIndex:'size'},{title:'目录',dataIndex:'directory'},{title:'操作',key:'action'}]
async function list(){busy.value=true;try{const {data}=await api.get(`/host/${props.host.id}/files`,{params:{path:path.value},timeout:75000});entries.value=data.entries;path.value=data.path;truncated.value=data.truncated}finally{busy.value=false}}
function enter(row){path.value=path.value.replace(/\/$/,'')+'/'+row.name;list()}
function pick(event){file.value=event.target.files?.[0]||null;if(file.value?.size>10*1024*1024){message.error('文件不能超过 10MB');file.value=null}}
function upload(){Modal.confirm({title:'确认上传文件',content:`目标：${props.host.name} ${path.value}；${overwrite.value?'允许覆盖同名普通文件':'不允许覆盖现有文件'}`,onOk:async()=>{busy.value=true;try{const form=new FormData();form.append('path',path.value);form.append('file',file.value);form.append('overwrite',String(overwrite.value));const {data}=await api.post(`/host/${props.host.id}/files`,form,{timeout:75000});checksum.value=data.sha256;message.success('上传完成');await list()}finally{busy.value=false}}})}
async function download(row){busy.value=true;try{const response=await api.get(`/host/${props.host.id}/files`,{params:{path:path.value.replace(/\/$/,'')+'/'+row.name,download:'1'},responseType:'blob',timeout:75000});const url=URL.createObjectURL(response.data),link=document.createElement('a');link.href=url;link.download=row.name;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}finally{busy.value=false}}
async function loadHistory(){history.value=(await api.get(`/host/${props.host.id}/files`,{params:{history:'1'}})).data}
</script>
