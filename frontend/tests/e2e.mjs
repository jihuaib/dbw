import { chromium } from 'playwright';

const errors = [];
const b = await chromium.launch();
const page = await b.newPage({ viewport: { width: 1560, height: 1040 } });
page.on('console', m => { if (m.type() === 'error') errors.push('C: ' + m.text().slice(0, 160)); });
page.on('pageerror', e => errors.push('P: ' + String(e).slice(0, 160)));

const step = async (name, fn) => {
    try { await fn(); console.log('✓', name); }
    catch (e) { console.log('✗', name, '→', String(e).split('\n')[0].slice(0, 150)); }
};

await step('默认落在诊断页', async () => {
    await page.goto('http://127.0.0.1:5178/', { waitUntil: 'networkidle' });
    await page.waitForURL(/#\/diagnose/, { timeout: 8000 });
    const t = await page.locator('.nn-menu').innerText();
    for (const x of ['诊断', '知识库', '设备与拓扑', '一致性验证'])
        if (!t.includes(x)) throw new Error('导航缺 ' + x);
    if (t.includes('设置')) throw new Error('设置应在右上角弹窗，不在侧边栏');
});

await step('知识库：CNetNexus CLI 手册已导入', async () => {
    await page.goto('http://127.0.0.1:5178/#/kb', { waitUntil: 'networkidle' });
    await page.waitForFunction(
        () => /命令清单 · [1-9]\d*/.test(document.body.innerText), { timeout: 15000 });
    const t = await page.locator('body').innerText();
    const m = t.match(/命令清单 · (\d+)/);
    if (!m || Number(m[1]) < 50) throw new Error('命令数 ' + (m ? m[1] : '?'));
    // 命令表分页，用搜索框验证具体命令存在
    await page.getByPlaceholder('搜索命令或用途').fill('lldp');
    await page.keyboard.press('Enter');
    await page.waitForFunction(
        () => document.body.innerText.includes('show lldp neighbors'), { timeout: 8000 });
    console.log('    → ' + m[1] + ' 条 CNetNexus 只读命令');
});

await step('设备页：真机清单（Telnet）', async () => {
    await page.goto('http://127.0.0.1:5178/#/devices', { waitUntil: 'networkidle' });
    // 等设备表格真正渲染出来（SPINE1 也会出现在拓扑图里，不能拿它当判据）
    await page.waitForFunction(
        () => document.body.innerText.includes('telnet://'), { timeout: 15000 });
    const t = await page.locator('body').innerText();
    for (const d of ['SPINE1', 'LEAF1', 'LEAF2', 'LEAF3'])
        if (!t.includes(d)) throw new Error('缺设备 ' + d);
});

await step('一键 LLDP 拓扑发现（真机）', async () => {
    await page.getByRole('button', { name: /一键发现拓扑/ }).click();
    await page.waitForSelector('.topo svg', { timeout: 90000 });
    // 链路数取决于当前是否注入着故障，只要求发现到链路即可
    await page.waitForFunction(
        () => document.querySelectorAll('.topo .link').length >= 2, { timeout: 90000 });
    const links = await page.locator('.topo .link').count();
    const t = await page.locator('body').innerText();
    if (!t.includes('双向确认')) throw new Error('无双向确认统计');
    console.log('    → 拓扑 ' + links + ' 条链路（真机 LLDP）');
});
await page.screenshot({ path: '/tmp/n-topo.png' });

await step('拓扑上下文可送给大模型', async () => {
    await page.getByRole('tab', { name: '送给大模型的拓扑上下文' }).click();
    await page.waitForFunction(
        () => document.body.innerText.includes('# 网络拓扑'), { timeout: 8000 });
});

await step('拓扑：滚轮缩放 / 拖动节点 / 平移画布', async () => {
    const center = sel => page.locator(sel).first().evaluate(el => {
        const b = el.getBoundingClientRect(); return { x: b.left + b.width / 2, y: b.top + b.height / 2 };
    });
    await page.locator('.topo .canvas').scrollIntoViewIfNeeded();
    await page.waitForTimeout(400);
    const g = page.locator('.topo .canvas > g');
    const t0 = await g.getAttribute('transform');
    const c = await center('.topo .canvas');
    await page.mouse.move(c.x, c.y);
    await page.mouse.wheel(0, -300);
    await page.waitForTimeout(300);
    if (t0 === await g.getAttribute('transform')) throw new Error('滚轮未缩放');
    const node = page.locator('.topo .device').first();
    const n0 = await node.getAttribute('transform');
    const nc = await center('.topo .device rect');
    await page.mouse.move(nc.x, nc.y); await page.mouse.down();
    await page.mouse.move(nc.x + 100, nc.y + 50, { steps: 6 }); await page.mouse.up();
    await page.waitForTimeout(200);
    if (n0 === await node.getAttribute('transform')) throw new Error('节点未随拖动移动');
    const t1 = await g.getAttribute('transform');
    const box = await page.locator('.topo .canvas').boundingBox();
    await page.mouse.move(box.x + 30, box.y + box.height - 30); await page.mouse.down();
    await page.mouse.move(box.x + 130, box.y + box.height - 80, { steps: 6 }); await page.mouse.up();
    if (t1 === await g.getAttribute('transform')) throw new Error('画布未平移');
    await page.getByRole('button', { name: '重排' }).click();
});

await step('拓扑：右键设备 → 登录连接设备（网页终端，真机回显）', async () => {
    const c = await page.locator('.topo .device rect').first().evaluate(el => {
        const b = el.getBoundingClientRect(); return { x: b.left + b.width / 2, y: b.top + b.height / 2 };
    });
    await page.mouse.click(c.x, c.y, { button: 'right' });
    await page.waitForSelector('.nn-context-menu', { timeout: 6000 });
    await page.getByRole('button', { name: '登录连接设备' }).click();
    await page.waitForFunction(() => document.body.innerText.includes('已连接'), { timeout: 15000 });
    await page.waitForTimeout(1200);
    await page.keyboard.type('show version');
    await page.keyboard.press('Enter');
    await page.waitForFunction(() => {
        const t = window.__detopsTerm; if (!t) return false;
        const buf = t.buffer.active; let s = '';
        for (let i = 0; i < buf.length; i++) s += (buf.getLine(i)?.translateToString(true) || '') + '\n';
        return /Version/.test(s);
    }, { timeout: 8000 });
    // xterm 会吃掉 Escape，用弹窗的关闭按钮
    await page.locator('.nn-modal-close').last().click();
    await page.locator('.nn-modal').last().waitFor({ state: 'hidden', timeout: 6000 });
});

await step('设备表单支持 SSH / Telnet + 厂商预设', async () => {
    await page.getByRole('button', { name: /新增设备/ }).click();
    await page.waitForSelector('text=接入协议', { timeout: 6000 });
    const t = await page.locator('body').innerText();
    if (!t.includes('设备类型')) throw new Error('无厂商预设');
    if (!t.includes('CNetNexus')) throw new Error('无 CNetNexus 预设');
    await page.getByRole('button', { name: '取消' }).last().click();
    await page.waitForTimeout(400);
});

await step('设备命令能力探测（手册 × 实机）', async () => {
    await page.goto('http://127.0.0.1:5178/#/devices', { waitUntil: 'networkidle' });
    await page.waitForFunction(
        () => document.body.innerText.includes('SPINE1'), { timeout: 10000 });
    await page.getByRole('button', { name: '探测' }).first().click();
    await page.waitForFunction(
        () => /支持 \d+ \/ 不支持/.test(document.body.innerText), { timeout: 120000 });
    await page.waitForFunction(
        () => document.body.innerText.includes('命令能力'), { timeout: 10000 });
});

await step('诊断：实时进度 + 真机采集 + AI', async () => {
    await page.goto('http://127.0.0.1:5178/#/diagnose', { waitUntil: 'networkidle' });
    await page.locator('.pane-left').getByRole('button', { name: /新建/ }).click();
    await page.waitForSelector('.starters', { timeout: 8000 });
    await page.locator('.starters').getByRole('button', { name: /好像连不上了/ }).click();
    // 点完立刻要有反馈 —— 不能干等
    await page.waitForSelector('.bubble.reply.live', { timeout: 6000 });
    await page.waitForFunction(
        () => document.body.innerText.includes('① 提取信息'), { timeout: 10000 });
    await page.waitForSelector('.fp', { timeout: 180000 });
    const t = await page.locator('.thread').innerText();
    if (!t.includes('诊断指纹')) throw new Error('无指纹卡');
    if (!t.includes('snapshot')) throw new Error('无快照哈希');
});
await page.screenshot({ path: '/tmp/n-diagnose.png' });

await step('展开执行轨迹与采集计划', async () => {
    await page.getByRole('button', { name: '执行轨迹' }).click();
    await page.waitForSelector('.trace', { timeout: 6000 });
    await page.getByRole('button', { name: /采集计划/ }).click();
    await page.waitForTimeout(500);
});

await step('查看证据快照（送进 AI 的字节）', async () => {
    await page.getByRole('button', { name: '证据快照' }).click();
    await page.waitForFunction(
        () => document.body.innerText.includes('# 证据快照'), { timeout: 15000 });
    const t = await page.locator('body').innerText();
    if (!t.includes('# 网络拓扑')) throw new Error('快照里没有拓扑上下文');
});

await step('设备命令与完整回显（原始字节可见）', async () => {
    await page.getByRole('tab', { name: '设备命令与完整回显' }).click();
    await page.waitForSelector('.cap', { timeout: 8000 });
    await page.locator('.cap summary').first().click();
    await page.waitForFunction(
        () => document.querySelector('.cap pre')?.innerText.length > 0,
        { timeout: 6000 });
});

await step('模型交互（提示词 + 原始回复可见）', async () => {
    await page.getByRole('button', { name: '模型交互' }).click();
    await page.waitForFunction(
        () => document.body.innerText.includes('系统提示词'), { timeout: 10000 });
    const t = await page.locator('body').innerText();
    if (!t.includes('模型原始回复')) throw new Error('无模型原始回复页签');
    // agent 模式下应有逐轮交互
    if (t.includes('Agent 逐轮交互')) {
        await page.getByRole('tab', { name: 'Agent 逐轮交互' }).click();
        await page.waitForFunction(
            () => /第 1 轮/.test(document.body.innerText), { timeout: 6000 });
        await page.locator('.round details summary').first().click();
    }
    await page.getByRole('tab', { name: '系统提示词' }).click();
    await page.waitForFunction(
        () => document.body.innerText.includes('只读命令'), { timeout: 6000 });
});

await step('Syslog 页：服务器监听端口 + 事件', async () => {
    await page.goto('http://127.0.0.1:5178/#/syslog', { waitUntil: 'networkidle' });
    await page.waitForFunction(
        () => document.body.innerText.includes('监听中'), { timeout: 10000 });
    const t = await page.locator('.body').innerText();   // 只看主区域，不含侧边栏
    for (const x of ['默认监听端口', '当前监听', 'Syslog 事件'])
        if (!t.includes(x)) throw new Error('Syslog 页缺 ' + x);
    if (/Trap|MIB/.test(t)) throw new Error('Syslog 页不应出现 Trap/MIB 内容');
});

await step('SNMP 页：Trap 监听 + MIB 编译 + 右键详情弹窗', async () => {
    await page.goto('http://127.0.0.1:5178/#/snmp', { waitUntil: 'networkidle' });
    await page.waitForFunction(
        () => /\d+\/\d+ 编译通过/.test(document.body.innerText)
            && document.body.innerText.includes('当前监听'), { timeout: 10000 });
    const t = await page.locator('.body').innerText();
    for (const x of ['Trap 服务器', 'community', 'Trap 事件', '导入 MIB', '编译全部'])
        if (!t.includes(x)) throw new Error('SNMP 页缺 ' + x);
    // 搜索定位 → 详情弹窗
    await page.getByPlaceholder('按名称或 OID 搜索').fill('linkDown');
    await page.keyboard.press('Enter');
    await page.waitForFunction(
        () => document.body.innerText.includes('IF-MIB::linkDown'), { timeout: 6000 });
    await page.locator('.hit', { hasText: 'IF-MIB::linkDown' }).first().click();
    await page.waitForFunction(
        () => document.body.innerText.includes('1.3.6.1.6.3.1.1.5.3'), { timeout: 6000 });
    await page.locator('.nn-modal-close').last().click();
    await page.waitForTimeout(300);
    // 树节点右键 → 上下文菜单
    const node = page.locator('.node').first();
    await node.click({ button: 'right' });
    await page.waitForSelector('.nn-context-menu', { timeout: 6000 });
    await page.getByRole('button', { name: /查看详情/ }).click();
    await page.waitForSelector('.nn-modal-close', { timeout: 6000 });
    await page.locator('.nn-modal-close').last().click();
});

await step('设备表单含上报配置（目标地址 / 端口 / 命令模板）', async () => {
    await page.goto('http://127.0.0.1:5178/#/devices', { waitUntil: 'networkidle' });
    await page.waitForFunction(
        () => document.body.innerText.includes('SPINE1'), { timeout: 10000 });
    await page.getByRole('button', { name: '编辑' }).first().click();
    await page.waitForSelector('text=上报配置', { timeout: 6000 });
    const t = await page.locator('body').innerText();
    for (const x of ['上报目标地址', 'Syslog 端口', 'Trap 端口', '下发命令模板'])
        if (!t.includes(x)) throw new Error('设备表单缺 ' + x);
    await page.getByRole('button', { name: '取消' }).last().click();
    await page.waitForTimeout(300);
});

await step('设置弹窗：模型接入 / 一致性策略 / 版本锚', async () => {
    await page.goto('http://127.0.0.1:5178/#/diagnose', { waitUntil: 'networkidle' });
    const gear = page.locator('button[aria-label="设置"]');
    await gear.waitFor({ state: 'visible', timeout: 8000 });
    try {
        await gear.click({ timeout: 8000 });
    } catch (err) {
        const box = await gear.boundingBox();
        const top = await page.evaluate(([x, y]) => {
            const el = document.elementFromPoint(x, y);
            return el ? (el.className?.baseVal ?? el.className) + ' <' + el.tagName + '>' : 'none';
        }, [box.x + box.width / 2, box.y + box.height / 2]);
        console.log('    ! 齿轮被遮挡，顶层元素:', top, '— 改用强制点击');
        await gear.click({ force: true });
    }
    await page.waitForSelector('input[type=password]', { timeout: 8000 });
    const t = await page.locator('body').innerText();
    for (const x of ['服务商', 'Base URL', 'API Key'])
        if (!t.includes(x)) throw new Error('模型接入面板缺 ' + x);
    // 服务商下拉里应能选到 DeepSeek / GLM
    await page.locator('.nn-select').first().click();
    await page.waitForFunction(
        () => /DeepSeek/.test(document.body.innerText), { timeout: 6000 });
    const opts = await page.locator('body').innerText();
    for (const x of ['DeepSeek', 'GLM'])
        if (!opts.includes(x)) throw new Error('服务商下拉缺 ' + x);
    // 收起下拉：点弹窗标题区，而不是 Escape（下拉若已自行关闭，Escape 会关掉整个弹窗）
    await page.locator('.nn-navigation-modal-heading').click({ timeout: 8000 });
    await page.waitForTimeout(200);
    // 切到一致性策略与版本锚面板
    await page.getByRole('tab', { name: /一致性策略/ }).click({ timeout: 8000 });
    await page.waitForFunction(
        () => document.body.innerText.includes('F0'), { timeout: 6000 });
    await page.getByRole('tab', { name: /版本锚/ }).click({ timeout: 8000 });
    await page.waitForFunction(
        () => document.body.innerText.includes('NORM-'), { timeout: 6000 });
    await page.screenshot({ path: '/tmp/n-settings.png' });
    await page.locator('.nn-navigation-modal-close').click({ timeout: 8000 });
    await page.waitForTimeout(400);
});

await step('会话右键删除（级联清理关联记录）', async () => {
    await page.goto('http://127.0.0.1:5178/#/diagnose', { waitUntil: 'networkidle' });
    await page.waitForSelector('.pane-left .item', { timeout: 8000 });
    const before = await page.locator('.pane-left .item').count();
    await page.locator('.pane-left .item').first().click({ button: 'right' });
    await page.waitForSelector('.nn-context-menu', { timeout: 6000 });
    await page.getByRole('button', { name: /删除会话及关联记录/ }).click();
    await page.waitForSelector('.nn-confirm-dialog', { timeout: 6000 });
    const body = await page.locator('.nn-confirm-dialog').innerText();
    if (!body.includes('冻结')) throw new Error('确认框未说明会清理冻结答案');
    await page.locator('.nn-confirm-button-danger').click();
    await page.waitForFunction(
        (n) => document.querySelectorAll('.pane-left .item').length < n,
        before, { timeout: 8000 });
});

console.log('\n--- errors ---');
console.log(errors.length ? errors.slice(0, 8).join('\n') : '(none)');
await b.close();
