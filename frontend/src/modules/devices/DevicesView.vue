<template>
    <div class="stack-md">
        <NnAlert type="info" show-icon>
            设备清单是真机接入的入口，支持 <b>SSH</b> 与 <b>Telnet</b>。
            维护好设备后点「一键发现拓扑」，系统逐台跑 LLDP 并双向确认成边；
            拓扑会作为诊断上下文一并送给大模型。
        </NnAlert>

        <NnCard>
            <template #title><b>设备清单 · {{ devices.length }}</b></template>
            <template #extra>
                <NnSpace>
                    <NnButton size="small" :loading="calibrating" :disabled="!devices.length"
                              @click="calibrate">
                        实测标定易变位置
                    </NnButton>
                    <NnButton size="small" type="primary" @click="openForm()">
                        <PlusOutlined /> 新增设备
                    </NnButton>
                </NnSpace>
            </template>

            <NnTable :columns="columns" :data-source="devices" row-key="id"
                     size="small" bordered>
                <template #emptyText>
                    <span class="dim">
                        还没有设备。点「新增设备」填入 CNetNexus 的 Telnet 地址即可。
                    </span>
                </template>
            </NnTable>
            <p class="dim" style="font-size: 12px; margin: 12px 0 0">
                执行顺序按 <span class="mono">(role, name)</span> 全序固定 ——
                并发只影响耗时，不影响顺序，快照哈希才稳定。
            </p>
        </NnCard>

        <NnCard>
            <template #title><b>网络拓扑</b></template>
            <template #extra>
                <NnButton size="small" type="primary" :loading="discovering"
                          :disabled="!devices.length" @click="discover">
                    <ClusterOutlined /> 一键发现拓扑（LLDP）
                </NnButton>
            </template>

            <div v-if="topo && topo.last_run" class="grid-stats" style="margin-bottom: 14px">
                <NnStatistic title="设备" :value="topo.last_run.devices" />
                <NnStatistic title="链路" :value="topo.last_run.links" />
                <NnStatistic title="双向确认" :value="topo.last_run.confirmed" />
                <NnStatistic title="解析引擎" :value="topo.last_run.engine" />
            </div>

            <TopoGraph v-if="topo" :nodes="topo.nodes" :edges="topo.edges" @node-menu="onNodeMenu" />
            <NnContextMenu ref="nodeCtxRef" v-model:open="nodeCtxOpen"
                           :title="ctxNode ? ctxNode.name : ''"
                           :meta="ctxDevice ? `${ctxDevice.protocol.toUpperCase()} ${ctxDevice.host}:${ctxDevice.port}` : '清单中无此设备'">
                <button class="ctx-item" :disabled="!ctxDevice" @click="ctxLogin">登录连接设备</button>
                <button class="ctx-item" :disabled="!ctxDevice" @click="ctxTest">测试连通性</button>
                <button class="ctx-item" :disabled="!ctxDevice" @click="ctxEdit">编辑设备</button>
            </NnContextMenu>
            <TerminalModal v-model:open="termOpen" :device="termDevice" />

            <div v-if="topo && topo.last_run && topo.last_run.log.length" style="margin-top: 14px">
                <NnButton size="small" variant="text" @click="showLog = !showLog">
                    {{ showLog ? '收起' : '展开' }}发现日志（{{ topo.last_run.log.length }}）
                </NnButton>
                <div v-if="showLog" style="margin-top: 8px">
                    <div v-for="(l, i) in topo.last_run.log" :key="i" class="log"
                         :class="`lv-${l.level}`">
                        <span class="mono lv">{{ l.level.toUpperCase() }}</span>
                        <span class="mono dev">{{ l.device }}</span>
                        <span>{{ l.msg }}</span>
                    </div>
                </div>
            </div>

            <NnTabs v-if="topo && topo.edges.length" v-model:active-key="tab"
                    style="margin-top: 14px">
                <NnTabPane key="links" tab="链路明细">
                    <NnTable :columns="linkColumns" :data-source="withKey(topo.edges)"
                             row-key="_k" size="small" bordered />
                </NnTabPane>
                <NnTabPane key="context" tab="送给大模型的拓扑上下文">
                    <pre class="pre-box tall">{{ context }}</pre>
                    <p class="dim" style="font-size: 12px; margin: 10px 0 0">
                        这段文本会拼进证据快照，参与 snapshot_hash 计算 ——
                        拓扑变了，诊断指纹随之变化。
                    </p>
                </NnTabPane>
            </NnTabs>
        </NnCard>

        <NnCard v-if="profiles.length">
            <template #title><b>易变位置标定（实测，非猜测）</b></template>
            <template #extra>
                <span class="dim">
                    {{ profiles.filter(p => p.count).length }} 条命令有易变位置，
                    共 {{ profiles.reduce((a, p) => a + p.count, 0) }} 个
                </span>
            </template>
            <p class="dim" style="margin-top: 0">
                对每条命令多采几次、token 级比对，<b>实际在变的位置</b>才记为易变。
                这是规则表的兜底 —— 没人想到的计数器、倒计时、老化时间，靠它擦掉。
                格式无关、厂商无关。
            </p>
            <NnTable :columns="profColumns"
                     :data-source="withKey(profiles.filter(p => p.count))"
                     row-key="_k" size="small" bordered :scroll="{ y: 260 }" />
        </NnCard>

        <NnCard v-if="caps.length">
            <template #title><b>命令能力（手册 × 实机 交叉校验）</b></template>
            <template #extra>
                <span class="dim">
                    不支持 {{ caps.filter(c => !c.supported).length }} /
                    {{ caps.length }} 条
                </span>
            </template>
            <p class="dim" style="margin-top: 0">
                手册说有，这台设备未必有。探测过的结果会记下来，
                <b>采集编排层不再提议这些命令</b> —— 也就不会有错误文本混进证据快照。
            </p>
            <NnTable :columns="capColumns" :data-source="withKey(caps)" row-key="_k"
                     size="small" bordered :scroll="{ y: 300 }" />
        </NnCard>

        <NnModal v-model:open="formOpen" :width="720"
                 :ok-text="editing ? '保存' : '创建'" cancel-text="取消"
                 :confirm-loading="saving" @ok="save">
            <template #title>{{ editing ? `编辑 ${editing.name}` : '新增设备' }}</template>
            <NnForm layout="vertical" :model="form">
                <NnRow :gutter="16">
                    <NnCol :span="8">
                        <NnFormItem label="设备名" required>
                            <NnInput v-model:value="form.name" placeholder="LEAF1" />
                        </NnFormItem>
                    </NnCol>
                    <NnCol :span="8">
                        <NnFormItem label="角色">
                            <NnSelect v-model:value="form.role" :options="roleOptions" />
                        </NnFormItem>
                    </NnCol>
                    <NnCol :span="8">
                        <NnFormItem label="设备类型（自动填好接入参数）">
                            <NnSelect v-model:value="vendorProfile" :options="profileOptions"
                                      @change="onProfile" />
                        </NnFormItem>
                    </NnCol>

                    <NnCol :span="8">
                        <NnFormItem label="接入协议">
                            <NnSelect v-model:value="form.protocol" :options="protoOptions"
                                      @change="onProto" />
                        </NnFormItem>
                    </NnCol>
                    <NnCol :span="16">
                        <NnFormItem label=" ">
                            <div v-if="profileNote" class="dim" style="font-size: 12px">
                                {{ profileNote }}
                            </div>
                        </NnFormItem>
                    </NnCol>

                    <NnCol :span="10">
                        <NnFormItem label="管理地址">
                            <NnInput v-model:value="form.host" placeholder="10.0.0.11" />
                        </NnFormItem>
                    </NnCol>
                    <NnCol :span="4">
                        <NnFormItem label="端口">
                            <NnInputNumber v-model:value="form.port" style="width: 100%" />
                        </NnFormItem>
                    </NnCol>
                    <NnCol :span="5">
                        <NnFormItem label="用户名">
                            <NnInput v-model:value="form.username" />
                        </NnFormItem>
                    </NnCol>
                    <NnCol :span="5">
                        <NnFormItem label="密码">
                            <NnInputPassword v-model:value="form.password"
                                             :placeholder="editing && editing.password_set
                                                 ? '已设置（留空不修改）' : ''" />
                        </NnFormItem>
                    </NnCol>

                    <NnCol :span="6">
                        <NnFormItem label="厂商">
                            <NnInput v-model:value="form.vendor" placeholder="H3C" />
                        </NnFormItem>
                    </NnCol>
                    <NnCol :span="6">
                        <NnFormItem label="型号">
                            <NnInput v-model:value="form.model" />
                        </NnFormItem>
                    </NnCol>
                    <NnCol :span="6">
                        <NnFormItem label="关分屏命令">
                            <NnInput v-model:value="form.pager_cmd"
                                     placeholder="留空自动尝试" />
                        </NnFormItem>
                    </NnCol>
                    <NnCol :span="6">
                        <NnFormItem label="LLDP 命令">
                            <NnInput v-model:value="form.lldp_cmd"
                                     placeholder="留空自动尝试" />
                        </NnFormItem>
                    </NnCol>

                    <NnCol :span="24"><div class="sect">上报配置（syslog / SNMP trap，端口即设备身份）</div></NnCol>
                    <NnCol :span="8">
                        <NnFormItem label="上报目标地址（设备能访问到的本机）">
                            <NnInput v-model:value="form.report_host" :placeholder="suggestedHost || '例如 192.168.1.10'" />
                        </NnFormItem>
                    </NnCol>
                    <NnCol :span="4">
                        <NnFormItem label="Syslog 端口">
                            <NnInputNumber v-model:value="form.syslog_port" :min="0" :max="65535" style="width: 100%" />
                        </NnFormItem>
                    </NnCol>
                    <NnCol :span="4">
                        <NnFormItem label="Trap 端口">
                            <NnInputNumber v-model:value="form.trap_port" :min="0" :max="65535" style="width: 100%" />
                        </NnFormItem>
                    </NnCol>
                    <NnCol :span="8">
                        <NnFormItem label="&nbsp;">
                            <span class="dim" style="font-size: 12px">端口 0 = 不下发该项；服务器会自动监听设备配置的端口</span>
                        </NnFormItem>
                    </NnCol>
                    <NnCol :span="12">
                        <NnFormItem label="Syslog 下发命令模板（{host} / {port} 占位）">
                            <NnInput v-model:value="form.syslog_cmd" placeholder="来自设备类型预设，可手改" />
                        </NnFormItem>
                    </NnCol>
                    <NnCol :span="12">
                        <NnFormItem label="Trap 下发命令模板">
                            <NnInput v-model:value="form.trap_cmd" placeholder="来自设备类型预设，可手改" />
                        </NnFormItem>
                    </NnCol>

                    <NnCol :span="6">
                        <NnFormItem label="启用">
                            <NnSwitch v-model:checked="form.enabled" />
                        </NnFormItem>
                    </NnCol>
                    <NnCol :span="18">
                        <NnFormItem label="备注">
                            <NnInput v-model:value="form.note" />
                        </NnFormItem>
                    </NnCol>
                </NnRow>
            </NnForm>
        </NnModal>

        <NnModal v-model:open="testOpen" :width="800" ok-text="关闭"
                 @ok="testOpen = false">
            <template #title>连通性测试 · {{ testing ? '进行中' : (testTarget || '') }}</template>
            <NnSpin :spinning="testing">
                <template v-if="testResult">
                    <NnAlert :type="testResult.ok ? 'success' : 'error'" show-icon
                             style="margin-bottom: 12px">
                        {{ testResult.ok
                            ? `连通正常（${testResult.protocol}），命令已执行`
                            : testResult.error }}
                    </NnAlert>
                    <div class="mono dim" style="margin-bottom: 6px">
                        {{ testResult.command }}
                    </div>
                    <pre class="pre-box tall">{{ testResult.output || '（无回显）' }}</pre>
                </template>
            </NnSpin>
        </NnModal>
    </div>
</template>

<script setup>
import { computed, h, nextTick, onMounted, reactive, ref } from 'vue';
import {
    PlusOutlined, ClusterOutlined, NnButton, NnSpace, NnTag, notificationService
} from 'netnexus-ui';
import { collectApi, deviceApi, eventsApi } from '../../shared/api.js';
import { confirmAsync } from '../../shared/dialog.js';
import TopoGraph from './TopoGraph.vue';
import TerminalModal from './TerminalModal.vue';

const ROLE_COLOR = { SPINE: 'purple', CORE: 'purple', LEAF: 'blue', BORDER: 'orange',
    ACCESS: 'cyan', OTHER: '' };

const devices = ref([]);
const topo = ref(null);
const context = ref('');
const options = ref({ roles: [], protocols: [] });
const formOpen = ref(false);
const editing = ref(null);
const saving = ref(false);
const discovering = ref(false);
const showLog = ref(false);
const tab = ref('links');
const testOpen = ref(false);
const testing = ref(false);
const testResult = ref(null);
const testTarget = ref('');
const vendorProfile = ref('cnetnexus');
const probing = ref(null);
const caps = ref([]);
const calibrating = ref(false);
const profiles = ref([]);

const form = reactive({
    name: '', role: 'LEAF', protocol: 'ssh', host: '', port: 22,
    username: 'admin', password: '', enable_password: '', vendor: '', model: '',
    pager_cmd: '', lldp_cmd: '', enabled: true, note: '',
    report_host: '', syslog_port: 0, trap_port: 0, syslog_cmd: '', trap_cmd: ''
});
const suggestedHost = ref('');
const pushing = ref(null);
const nodeCtxRef = ref(null);
const nodeCtxOpen = ref(false);
const ctxNode = ref(null);
const termOpen = ref(false);
const termDevice = ref(null);
const ctxDevice = computed(() =>
    ctxNode.value ? (devices.value.find(d => d.name === ctxNode.value.name) || null) : null);

function onNodeMenu({ event, node }) {
    ctxNode.value = node;
    nodeCtxOpen.value = true;
    nextTick(() => nodeCtxRef.value && nodeCtxRef.value.openAt(event));
}

function ctxLogin() {
    nodeCtxOpen.value = false;
    if (!ctxDevice.value) return;
    termDevice.value = ctxDevice.value;
    termOpen.value = true;
}

function ctxTest() {
    nodeCtxOpen.value = false;
    if (ctxDevice.value) test(ctxDevice.value);
}

function ctxEdit() {
    nodeCtxOpen.value = false;
    if (ctxDevice.value) openForm(ctxDevice.value);
}

const roleOptions = computed(() => options.value.roles.map(r => ({ label: r, value: r })));
const protoOptions = computed(() =>
    options.value.protocols.map(p => ({ label: p.toUpperCase(), value: p })));

const withKey = rows => (rows || []).map((r, i) => ({ ...r, _k: i }));

const columns = [
    {
        title: '设备', dataIndex: 'name', width: 110,
        customRender: ({ text }) => h('b', { class: 'mono' }, text)
    },
    {
        title: '角色', dataIndex: 'role', width: 88,
        customRender: ({ text }) => h(NnTag, { color: ROLE_COLOR[text] || '' }, () => text)
    },
    {
        title: '接入', dataIndex: 'protocol', width: 180,
        customRender: ({ record }) => h('span', { class: 'mono' },
            `${record.protocol}://${record.host || '—'}:${record.port}`)
    },
    { title: '用户名', dataIndex: 'username', width: 90 },
    {
        title: '凭据', dataIndex: 'password_set', width: 80,
        customRender: ({ text }) =>
            h(NnTag, { color: text ? 'green' : 'orange' }, () => (text ? '已设置' : '未设置'))
    },
    { title: '厂商/型号', dataIndex: 'vendor', width: 130,
        customRender: ({ record }) =>
            h('span', {}, [record.vendor, record.model].filter(Boolean).join(' ') || '—') },
    {
        title: '启用', dataIndex: 'enabled', width: 70,
        customRender: ({ text }) =>
            h(NnTag, { color: text ? 'green' : '' }, () => (text ? '是' : '否'))
    },
    { title: '最近状态', dataIndex: 'last_status', ellipsis: true },
    {
        title: '操作', dataIndex: 'id', width: 270, key: 'ops',
        customRender: ({ record }) =>
            h(NnSpace, null, () => [
                h(NnButton, { size: 'small', onClick: () => test(record) }, () => '测试'),
                h(NnButton, { size: 'small', loading: probing.value === record.id,
                    onClick: () => probe(record) }, () => '探测'),
                h(NnButton, { size: 'small', onClick: () => openForm(record) }, () => '编辑'),
                h(NnButton, { size: 'small', loading: pushing.value === record.id,
                    disabled: !(record.syslog_port || record.trap_port),
                    onClick: () => pushReporting(record) }, () => '下发上报'),
                h(NnButton, { size: 'small', variant: 'text',
                    onClick: () => remove(record) }, () => '删除')
            ])
    }
];

const profColumns = [
    { title: '设备', dataIndex: 'device', width: 100 },
    {
        title: '命令', dataIndex: 'command', width: 320,
        customRender: ({ text }) => h('span', { class: 'mono' }, text)
    },
    { title: '易变位置数', dataIndex: 'count', width: 110, align: 'right' },
    { title: '采样次数', dataIndex: 'samples', width: 100, align: 'right' },
    {
        title: '位置（行:列）', dataIndex: 'positions', ellipsis: true,
        customRender: ({ text }) => h('span', { class: 'mono dim' },
            (text || []).slice(0, 12).map(p => `${p[0]}:${p[1]}`).join(' '))
    }
];

const capColumns = [
    { title: '设备', dataIndex: 'device', width: 100 },
    {
        title: '命令', dataIndex: 'command', width: 320,
        customRender: ({ text }) => h('span', { class: 'mono' }, text)
    },
    {
        title: '支持', dataIndex: 'supported', width: 90,
        customRender: ({ text }) =>
            h(NnTag, { color: text ? 'green' : 'red' }, () => (text ? '支持' : '不支持'))
    },
    { title: '设备回应', dataIndex: 'reason', ellipsis: true }
];

const linkColumns = [
    { title: '本端', dataIndex: 'a', width: 110 },
    { title: '本端口', dataIndex: 'a_port', width: 130,
        customRender: ({ text }) => h('span', { class: 'mono' }, text) },
    { title: '对端', dataIndex: 'b', width: 110 },
    { title: '对端口', dataIndex: 'b_port', width: 130,
        customRender: ({ text }) => h('span', { class: 'mono' }, text) },
    {
        title: '双向确认', dataIndex: 'confirmed',
        customRender: ({ text }) => h('span', { class: text ? 'dim' : 'warn-text' },
            text ? '是' : '单向 —— 对端看不到本端')
    }
];

const profileOptions = computed(() =>
    (options.value.vendor_profiles || []).map(p => ({ label: p.label, value: p.id })));
const profileNote = computed(() => {
    const p = (options.value.vendor_profiles || []).find(x => x.id === vendorProfile.value);
    return p ? p.note : '';
});

function onProto(v) {
    form.port = v === 'telnet' ? 23 : 22;
}

function onProfile(id) {
    const p = (options.value.vendor_profiles || []).find(x => x.id === id);
    if (!p) return;
    form.protocol = p.protocol;
    form.port = p.port;
    form.pager_cmd = p.pager_cmd;
    form.lldp_cmd = p.lldp_cmd;
    form.syslog_cmd = p.syslog_cmd || '';
    form.trap_cmd = p.trap_cmd || '';
    if (id !== 'generic' && !form.vendor) form.vendor = p.label;
}

function openForm(record) {
    editing.value = record || null;
    if (record) {
        Object.assign(form, { ...record, password: '', enable_password: '' });
    } else {
        Object.assign(form, {
            name: '', role: 'LEAF', protocol: 'telnet', host: '', port: 23,
            username: '', password: '', enable_password: '', vendor: '', model: '',
            pager_cmd: '', lldp_cmd: '', enabled: true, note: '',
            report_host: suggestedHost.value, syslog_port: 0, trap_port: 0,
            syslog_cmd: '', trap_cmd: ''
        });
        onProfile(vendorProfile.value);
    }
    formOpen.value = true;
}

async function save() {
    saving.value = true;
    try {
        if (editing.value) await deviceApi.update(editing.value.id, { ...form });
        else await deviceApi.create({ ...form });
        formOpen.value = false;
        await refresh();
        notificationService.success(editing.value ? '已保存' : '已创建');
    } catch (err) {
        notificationService.error(err.message);
    } finally {
        saving.value = false;
    }
}

async function pushReporting(record) {
    pushing.value = record.id;
    try {
        const r = await deviceApi.pushReporting(record.id);
        const bad = r.results.filter(x => !x.ok);
        if (bad.length) notificationService.warning(`${record.name}：${bad.map(x => x.command).join('；')} 被拒绝`);
        else notificationService.success(`${record.name}：已下发 ${r.results.length} 条上报命令`);
    } catch (err) {
        notificationService.error(err.message);
    } finally {
        pushing.value = null;
    }
}

async function remove(record) {
    const ok = await confirmAsync({
        title: `删除设备 ${record.name}？`,
        content: '与它相关的拓扑链路会一并删除。',
        okText: '删除', cancelText: '取消'
    });
    if (!ok) return;
    await deviceApi.remove(record.id);
    await refresh();
}

async function calibrate() {
    calibrating.value = true;
    try {
        const r = await collectApi.calibrate({ rounds: 3, gap_ms: 1500 });
        profiles.value = await collectApi.profiles();
        notificationService.success(
            `标定完成：${r.devices.length} 台设备，实测出 ${r.total_positions} 个易变位置`);
    } catch (err) {
        notificationService.error(err.message);
    } finally {
        calibrating.value = false;
    }
}

async function probe(record) {
    probing.value = record.id;
    try {
        const r = await deviceApi.probe(record.id);
        caps.value = await deviceApi.capabilities();
        notificationService.success(
            `${r.device}：探测 ${r.total} 条，支持 ${r.supported} / 不支持 ${r.unsupported}`
            + `（另有 ${r.skipped} 条需带参数，未探测）`);
    } catch (err) {
        notificationService.error(err.message);
    } finally {
        probing.value = null;
    }
}

async function test(record) {
    testTarget.value = record.name;
    testOpen.value = true;
    testing.value = true;
    testResult.value = null;
    try {
        testResult.value = await deviceApi.test(record.id, '');
    } catch (err) {
        testResult.value = { ok: false, error: err.message, command: '', output: '' };
    } finally {
        testing.value = false;
        await refresh();
    }
}

async function discover() {
    discovering.value = true;
    try {
        const r = await deviceApi.discover();
        await refresh();
        notificationService.success(
            `发现 ${r.links} 条链路，双向确认 ${r.confirmed} 条（解析引擎 ${r.engine}）`);
        showLog.value = true;
    } catch (err) {
        notificationService.error(err.message);
    } finally {
        discovering.value = false;
    }
}

async function refresh() {
    devices.value = await deviceApi.list();
    topo.value = await deviceApi.topology();
    context.value = (await deviceApi.topologyContext()).text;
}

onMounted(async () => {
    try { suggestedHost.value = (await eventsApi.suggestHost()).host; } catch { /* 可选 */ }
    try {
        options.value = await deviceApi.options();
        caps.value = await deviceApi.capabilities();
        profiles.value = await collectApi.profiles();
        await refresh();
    } catch (err) {
        notificationService.error(`后端未就绪：${err.message}`);
    }
});
</script>

<style scoped>
.log {
    display: grid;
    grid-template-columns: 56px 92px 1fr;
    gap: 10px;
    font-size: 12.5px;
    padding: 2px 0;
    align-items: baseline;
}

.lv {
    font-size: 10.5px;
    letter-spacing: 0.05em;
}

.lv-warn .lv {
    color: var(--nn-color-warning, #d48806);
}

.lv-error .lv {
    color: var(--nn-color-error, #d64545);
}

.lv-info .lv {
    color: var(--nn-color-text-secondary, #6b7280);
}
.sect {
    font-size: 12px;
    font-weight: 600;
    color: var(--nn-color-text-secondary, #6b7280);
    margin: 4px 0 8px;
    padding-top: 8px;
    border-top: 1px dashed var(--nn-color-border, #e5e7eb);
}
.ctx-item {
    display: flex;
    align-items: center;
    gap: 8px;
    width: 100%;
    padding: 8px 12px;
    border: 0;
    border-radius: 5px;
    background: none;
    cursor: pointer;
    font: inherit;
    font-size: 13px;
    text-align: left;
    color: inherit;
}

.ctx-item:hover:not(:disabled) {
    background: var(--nn-color-fill-quaternary, rgba(0, 0, 0, 0.04));
}

.ctx-item:disabled {
    opacity: 0.45;
    cursor: not-allowed;
}
</style>
