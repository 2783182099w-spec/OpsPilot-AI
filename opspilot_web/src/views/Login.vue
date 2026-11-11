<template>
  <main class="login">
    <section class="intro" aria-label="平台介绍">
      <div class="brand"><brand-mark/><div><strong>OpsPilot AI</strong><small>运维管理平台</small></div></div>
      <div class="intro-copy">
        <p class="eyebrow">基础设施运维工作台</p>
        <h1>主机、巡检与批量作业<span>集中管理。</span></h1>
        <p>从主机健康确认到批量作业执行，汇总运行状态与操作记录。</p>
        <ul class="modules">
          <li><cloud-server-outlined/> 资产管理</li>
          <li><audit-outlined/> 健康巡检</li>
          <li><bar-chart-outlined/> 监控告警</li>
        </ul>
      </div>
      <div class="illustration" aria-hidden="true">
        <svg viewBox="0 0 640 270" fill="none">
          <path d="M0 229H112L156 185H236L284 137H371L429 79H533L595 18H640M0 255H125L168 212H255L303 164H397L455 106H551L618 39H640M0 205H88L141 152H216L266 102H351L401 52H506L552 6" stroke="#81a6ff" stroke-opacity=".35"/>
          <path d="M0 229H112L156 185H236L284 137H371L429 79H533L595 18H640" stroke="#6ee1cf" stroke-width="2"/>
          <g fill="#101e3a" stroke="#87eed9" stroke-width="2"><circle cx="156" cy="185" r="5"/><circle cx="284" cy="137" r="5"/><circle cx="429" cy="79" r="5"/><circle cx="595" cy="18" r="5"/></g>
          <g stroke="#81a6ff" stroke-opacity=".24" stroke-dasharray="4 5"><path d="M156 185V270M284 137V270M429 79V270M595 18V270"/></g>
        </svg>
      </div>
    </section>
    <section class="form-side" aria-labelledby="login-heading">
      <div class="form-tools"><theme-toggle/></div>
      <div class="form-card">
        <div class="mobile-brand"><brand-mark/><span>OpsPilot AI</span></div>
        <h2 id="login-heading">登录运维工作台</h2>
        <p class="ops-muted">使用平台分配的账号登录。</p>
        <a-form :model="form" layout="vertical" @finish="submit">
          <a-form-item label="用户名" name="username" :rules="[{ required:true, message:'请输入用户名' }]">
            <a-input v-model:value="form.username" autocomplete="username" maxlength="150" size="large" placeholder="输入用户名">
              <template #prefix><user-outlined/></template>
            </a-input>
          </a-form-item>
          <a-form-item label="密码" name="password" :rules="[{ required:true, message:'请输入密码' }]">
            <a-input-password v-model:value="form.password" autocomplete="current-password" size="large" placeholder="输入密码">
              <template #prefix><lock-outlined/></template>
            </a-input-password>
          </a-form-item>
          <a-alert v-if="error" :message="error" type="error" show-icon class="login-error"/>
          <a-form-item><a-checkbox v-model:checked="form.remember">在此设备保持登录</a-checkbox></a-form-item>
          <a-button type="primary" html-type="submit" aria-label="登录" size="large" block :loading="loading">登录 <arrow-right-outlined aria-hidden="true"/></a-button>
        </a-form>
        <div class="form-note"><safety-certificate-outlined/> 遇到账号或权限问题，请联系平台管理员。</div>
      </div>
    </section>
  </main>
</template>

<script setup>
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useStore } from 'vuex'
import { ArrowRightOutlined, AuditOutlined, BarChartOutlined, CloudServerOutlined, LockOutlined, SafetyCertificateOutlined, UserOutlined } from '@ant-design/icons-vue'
import api from '@/api'
import BrandMark from '@/components/BrandMark.vue'
import ThemeToggle from '@/components/ThemeToggle.vue'

const router = useRouter()
const store = useStore()
const form = reactive({ username:'', password:'', remember:false })
const loading = ref(false)
const error = ref('')

async function submit() {
  if (loading.value) return
  loading.value = true
  error.value = ''
  try {
    const {data} = await api.post('/users/login/', { username:form.username.trim(), password:form.password })
    store.commit('setRemember', form.remember)
    store.commit('setToken', data.access)
    await router.replace({ name:'ShowCenter' })
  } catch (err) {
    const status = err?.response?.status
    if (status === 429) error.value = '登录尝试过于频繁，请稍后再试。'
    else if (!status || status >= 500) error.value = '暂时无法访问登录服务，请稍后重试。'
    else error.value = '用户名或密码不正确，或账号已停用。'
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login { display:grid; grid-template-columns:minmax(0,1.15fr) minmax(0,1fr); min-height:100dvh; background:var(--surface); }
.intro { position:relative; isolation:isolate; display:flex; flex-direction:column; justify-content:space-between; min-height:100%; overflow:hidden; padding:clamp(30px,5vw,80px); color:#f3f7ff; background:radial-gradient(ellipse at 85% 25%,#233e75 0%,transparent 59%),#111f3a; }
.intro::before { content:''; position:absolute; inset:0; z-index:-1; opacity:.28; background-image:linear-gradient(#5b77ac42 1px,transparent 1px),linear-gradient(90deg,#5b77ac42 1px,transparent 1px); background-size:40px 40px; }
.brand,.mobile-brand { display:flex; align-items:center; gap:13px; font-size:18px; font-weight:740; }
.brand small { display:block; color:#c0cdeb; font-size:11px; font-weight:500; margin-top:2px; }
.intro-copy { margin:clamp(44px,10vh,115px) 0 clamp(36px,6vh,80px); }
.eyebrow { color:#98e6d5; letter-spacing:.14em; font-size:13px; font-weight:700; }
.intro-copy h1 { margin:15px 0 20px; font-size:clamp(32px,4vw,63px); line-height:1.2; letter-spacing:-.04em; }
.intro-copy h1 span { display:block; color:#9db9ff; }
.intro-copy>p:last-of-type { max-width:430px; color:#c1cfea; line-height:1.85; }
.modules { display:flex; flex-wrap:wrap; gap:9px; list-style:none; padding:0; margin:28px 0 0; }
.modules li { display:flex; align-items:center; gap:7px; padding:7px 10px; border:1px solid #91abeb50; border-radius:7px; color:#d3e0ff; background:#15284f; font-size:12px; }
.illustration { margin:auto calc(-1 * clamp(30px,5vw,80px)) calc(-1 * clamp(30px,5vw,80px)); max-height:26vh; overflow:hidden; }
.illustration svg { width:100%; display:block; }
.form-side { position:relative; display:grid; place-items:center; padding:75px 34px; }
.form-tools { position:absolute; top:24px; right:30px; }
.form-card { width:min(100%,375px); }
.form-card h2 { margin:0 0 8px; font-size:29px; letter-spacing:-.025em; }
.form-card form { margin-top:34px; }
.form-note { display:flex; align-items:center; gap:7px; color:var(--muted); border-top:1px solid var(--line); margin-top:29px; padding-top:18px; font-size:12px; }
.login-error { margin-bottom:18px; }
.mobile-brand { display:none; }
@media(max-width:740px) {
  .login { display:block; }
  .intro { display:none; }
  .form-side { min-height:100dvh; padding:67px 23px 45px; }
  .form-tools { right:15px; top:13px; }
  .mobile-brand { display:flex; margin-bottom:35px; }
}
</style>
