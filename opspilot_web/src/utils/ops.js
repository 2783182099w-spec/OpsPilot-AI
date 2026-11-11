export const kinds={inspection:'健康巡检',probe:'在线检测',command:'命令执行',script:'脚本执行'}
export const profiles={health:'资源健康',baseline:'安全基线',acceptance:'综合巡检'}
export const states={awaiting_release:'待首批放行',skipped:'未派发',attempted:'已尝试',pending:'排队',running:'执行中',completed:'执行完成',failed:'执行失败',interrupted:'中断待核查',cancelled:'已取消',awaiting_approval:'待审批',rejected:'已驳回',success:'执行成功',timeout:'超时',queued:'等待',pass:'通过',warning:'异常',unknown:'待确认',info:'取证',review:'需复核',evidence:'取证',open:'待处理',acknowledged:'已确认',processing:'处理中',resolved:'已关闭',connected:'连接中',closed:'已结束',connecting:'建立连接'}
export const color=s=>({pass:'green',success:'green',completed:'green',resolved:'green',warning:'red',failed:'red',timeout:'red',unknown:'orange',review:'orange',running:'blue',processing:'blue',open:'red'}[s]||'default')
export const options=map=>Object.entries(map).map(([value,label])=>({value,label}))
export const time=value=>value?new Date(value).toLocaleString('zh-CN',{hour12:false}):'—'
