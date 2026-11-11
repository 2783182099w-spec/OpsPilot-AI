// Uses local fixtures; never approves or releases a job.
const {chromium}=require(process.env.OPSPILOT_PLAYWRIGHT_MODULE||'playwright')
const fs=require('fs')
const fixture=JSON.parse(fs.readFileSync(process.env.OPSPILOT_MANAGED_FIXTURE,'utf8'))
const WEB='http://127.0.0.1:9000', API='http://127.0.0.1:8000'
;(async()=>{
  const browser=await chromium.launch({headless:true,executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'})
  const context=await browser.newContext({viewport:{width:1440,height:960}})
  const page=await context.newPage(), errors=[]
  page.on('pageerror',e=>errors.push(e.message))
  page.setDefaultTimeout(12000)
  await page.route('**/mtask/tasks/*/action',r=>{
    const action=r.request().postDataJSON()?.action
    if(['approve','release'].includes(action)){errors.push('Unexpected mutation: '+action);return r.abort()}
    return r.continue()
  })
  async function login(username){
    await page.goto(WEB+'/login')
    const response=await context.request.post(API+'/users/login/',{data:{username,password:fixture.password}})
    if(!response.ok())throw new Error('Fixture login failed')
    const token=(await response.json()).access
    await page.evaluate(token=>sessionStorage.setItem('opspilot-ai',JSON.stringify({token,remember:false})),token)
    await context.setExtraHTTPHeaders({Authorization:'jwt '+token})
    await page.goto(WEB+'/opspilot/jobs')
  }
  try{
    await login(fixture.admin)
    await page.getByRole('button',{name:'新建作业',exact:true}).click()
    const editor=page.locator('.ant-drawer-content:visible')
    await editor.locator('#managed-job-name').fill(fixture.job)
    await editor.getByRole('button',{name:'添加参数',exact:true}).click()
    const input=(surface,label)=>surface.getByText(label,{exact:true}).locator('..').locator('..').locator('input')
    await input(editor,'变量名').fill('service')
    await input(editor,'显示名称').fill('服务名称')
    await editor.getByRole('button',{name:'保存草稿',exact:true}).click()
    await editor.getByRole('button',{name:'发布当前版本',exact:true}).click()
    await page.locator('.ant-modal-confirm:visible').getByRole('button',{name:/^确\s*认$/}).click()
    await editor.getByText(/当前版本 v2 · 已发布/).waitFor()

    await login(fixture.operator)
    await page.locator('.job-card').filter({hasText:fixture.job}).getByRole('button',{name:'运行作业',exact:true}).click()
    const drawer=page.locator('.ant-drawer-content:visible')
    await drawer.locator('.ant-select-selector').first().click()
    for(const name of fixture.host_names){
      await page.locator('.ant-select-item-option:visible').filter({hasText:name}).click()
    }
    await drawer.getByText('目标主机',{exact:true}).click()
    await input(drawer,'服务名称').fill('nginx.service')
    await drawer.getByRole('button',{name:'生成执行计划',exact:true}).click()
    await drawer.getByText('核对脚本快照',{exact:true}).waitFor()
    await drawer.getByText(/提交后等待审批/).waitFor()
    await drawer.getByRole('button',{name:'确认并提交',exact:true}).click()
    await page.waitForURL(/\/tasks\?task=\d+/)
    const id=new URL(page.url()).searchParams.get('task')
    const task=await (await context.request.get(API+'/mtask/tasks/'+id)).json()
    if(task.status!=='awaiting_approval'||task.managed_job.version!==2||task.options.managed.parameters.service!=='nginx.service')throw new Error('Unexpected task state or version')
    await context.request.post(API+'/mtask/tasks/'+id+'/action',{data:{action:'cancel',confirmed:true}})

    await page.goto(WEB+'/opspilot/tasks?task='+fixture.held)
    const detail=page.locator('.ant-modal:visible')
    await detail.getByText('首批通过，等待人工核对并放行',{exact:true}).waitFor()
    await detail.getByText('阶段明细',{exact:true}).first().click()
    await detail.getByText('建立 SSH',{exact:true}).waitFor()
    await detail.getByRole('button',{name:'放行后续批次',exact:true}).click()
    const confirm=page.locator('.ant-modal-confirm:visible')
    await confirm.getByText('确认首批结果并放行',{exact:true}).waitFor()
    await confirm.getByRole('button',{name:/^返\s*回$/}).click()
    if(errors.length)throw new Error(errors.join('\n'))
    console.log('PASS: create draft, publish, parameter/target preview, submit to approval, stage evidence, release confirmation without dispatch')
  }catch(e){
    console.error(e.message)
    await page.screenshot({path:'.runtime/qa-managed-error.png'}).catch(()=>{})
    process.exitCode=1
  }finally{await browser.close()}
})()
