// Captures only local UI. WebSocket traffic is intercepted, so no SSH is opened.
const { chromium } = require(process.env.OPSPILOT_PLAYWRIGHT_MODULE || 'playwright');
const fs = require('fs');
const os = require('os');
const path = require('path');

(async () => {
  const auth = JSON.parse(fs.readFileSync(path.join(os.tmpdir(), 'opspilot-screenshot-auth.json'), 'utf8'));
  const root = path.resolve(__dirname, '..');
  const output = path.join(root, 'docs', 'images');
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
  const context = await browser.newContext({ viewport: { width: 1600, height: 1000 }, locale: 'zh-CN', deviceScaleFactor: 1 });
  const page = await context.newPage();
  await page.routeWebSocket('**/ws/**', socket => socket.close({ code: 1000, reason: 'Screenshot only; no SSH connection' }));
  async function capture(file) {
    await page.waitForTimeout(1200);
    await page.evaluate(() => {
      for (const table of document.querySelectorAll('table')) {
        const headers = [...table.querySelectorAll('thead th')].map(th => th.textContent.trim());
        const privateColumns = headers.map((text, i) => /主机名称|连接地址|端口|备注|标签|类别|提交时间|^ID$/.test(text) ? i : -1).filter(i => i >= 0);
        for (const row of table.querySelectorAll('tbody tr')) {
          for (const index of privateColumns) if (row.children[index]) row.children[index].setAttribute('data-private-screenshot', 'true');
        }
      }
    });
    const mask = [page.locator('.header-right .ant-tag'), page.locator('[data-private-screenshot]'), page.locator('.console-info')];
    await page.screenshot({ path: path.join(output, file), fullPage: true, mask, maskColor: '#353b45' });
    console.log('Saved redacted screenshot:', file);
  }
  try {
    await page.goto('http://127.0.0.1:9000/login', { waitUntil: 'networkidle' });
    await capture('01-login.png');
    const login = await context.request.post('http://127.0.0.1:8000/users/login/', { data: auth });
    if (!login.ok()) throw new Error('Screenshot login failed: ' + login.status());
    const token = (await login.json()).access;
    await page.evaluate(token => sessionStorage.setItem('opspilot-ai', JSON.stringify({ token, remember: false })), token);
    const assets = await context.request.get('http://127.0.0.1:8000/host/', { headers: { Authorization: 'jwt ' + token } });
    const hosts = await assets.json();
    for (const [route, file] of [['host','02-assets.png'], ['multi_exec','04-batch-exec.png'], ['tasks','06-tasks.png'], ['ai_chat','05-ai-assistant.png']]) {
      await page.goto('http://127.0.0.1:9000/opspilot/' + route, { waitUntil: 'networkidle' });
      await capture(file);
    }
    if (hosts.length) {
      await page.goto('http://127.0.0.1:9000/opspilot/console/' + hosts[0].id, { waitUntil: 'networkidle' });
      await capture('03-console.png');
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error.message); process.exitCode = 1; });
