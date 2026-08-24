<template>
    <div class="stack-md">
        <NnCard>
            <template #title><b>Trap 服务器</b></template>
            <template #extra>
                <NnButton size="small" type="primary" :loading="saving" @click="apply">应用并重启监听</NnButton>
            </template>
            <div v-if="recv" class="row">
                <NnTag :color="recv.trap.running ? 'green' : 'red'">
                    {{ recv.trap.running ? '监听中' : '未运行' }}
                </NnTag>
                <span class="lbl">默认监听端口</span>
                <NnInputNumber v-model:value="port" :min="1" :max="65535" size="small" style="width: 120px" />
                <span class="lbl">community</span>
                <NnInput v-model:value="communities" size="small" style="width: 200px" placeholder="逗号分隔" />
                <span class="lbl">当前监听</span>
                <span class="mono dim">UDP {{ (recv.trap.ports || []).join(' / ') }}</span>
            </div>
            <p class="dim note">
                只收 trap（SNMPv2c），不做任何 SNMP 下发。设备各自的上报目标与端口在「设备与拓扑 → 编辑」里配置并下发。
                trap 报文只有数字 OID，符号化解码依赖下方编译出的 MIB 索引。
            </p>
        </NnCard>

        <NnCard>
            <template #title><b>Trap 事件</b></template>
            <template #extra>
                <NnSpace>
                    <NnTag color="blue">{{ events.length }} 条</NnTag>
                    <NnButton size="small" @click="loadEvents">刷新</NnButton>
                </NnSpace>
            </template>
            <NnTable :columns="evColumns" :data-source="events" row-key="id" size="small" bordered
                     :scroll="{ y: 300 }" :pagination="{ pageSize: 30 }">
                <template #expandedRowRender="{ record }">
                    <div class="vb">
                        <div class="mono dim" style="margin-bottom: 6px">trap OID: {{ record.trap_oid }}</div>
                        <div v-for="(v, i) in record.varbinds" :key="i" class="vb-row">
                            <span class="mono vb-name">{{ v.name }}</span>
                            <span class="mono">{{ v.value }}</span>
                            <span class="mono dim vb-oid">{{ v.oid }}</span>
                        </div>
                    </div>
                </template>
            </NnTable>
        </NnCard>

        <NnCard>
            <template #title><b>MIB</b></template>
            <template #extra>
                <NnSpace>
                    <input ref="fileRef" type="file" accept=".mib,.txt,.my,.smi" multiple
                           style="display: none" @change="onPick" />
                    <NnButton size="small" @click="fileRef && fileRef.click()"><UploadOutlined /> 导入 MIB</NnButton>
                    <NnButton size="small" type="primary" :loading="compiling" @click="compile">编译全部</NnButton>
                </NnSpace>
            </template>
            <div v-if="status" class="row" style="margin-bottom: 10px">
                <NnTag :color="status.ok ? 'green' : (status.compiled ? 'orange' : 'red')">
                    {{ status.compiled }}/{{ status.total }} 编译通过
                </NnTag>
                <NnTag color="blue">OID 索引 {{ status.oid_count }} 条</NnTag>
                <span class="dim mono" v-if="status.compiled_at">{{ status.compiled_at }}</span>
            </div>
            <div class="grid">
                <div>
                    <NnTable :columns="srcColumns" :data-source="rows" row-key="file" size="small"
                             bordered :pagination="{ pageSize: 8 }" />
                    <p class="dim note">
                        编译对象是这里的全部文件，依赖按 IMPORTS 自动解析，单个失败不影响其它。
                        接入真实设备时导入厂商 MIB 后重新编译，同名以导入的为准。
                    </p>
                </div>
                <div class="tree-pane">
                    <div class="row" style="margin-bottom: 8px">
                        <NnInputSearch v-model:value="q" placeholder="按名称或 OID 搜索" size="small"
                                       style="width: 260px" @search="doSearch" />
                        <span class="dim" style="font-size: 12px">右键节点查看详情</span>
                    </div>
                    <div v-if="hits.length" class="hits">
                        <button v-for="hit in hits" :key="hit.oid" class="hit" @click="jump(hit)">
                            <span class="mono">{{ hit.module }}::{{ hit.name }}</span>
                            <span class="mono dim">{{ hit.oid }}</span>
                        </button>
                    </div>
                    <NnTree v-else :tree-data="treeData" v-model:expanded-keys="expandedKeys"
                            v-model:selected-keys="selectedKeys" block-node
                            @expand="onExpand" @rightClick="onRightClick">
                        <template #title="node">
                            <span class="node" @contextmenu.prevent="ctxFor($event, node)">
                                <span class="mono nm">{{ node.name }}</span>
                                <span class="mono dim">({{ node.oid.split('.').pop() }})</span>
                                <NnTag v-if="node.cls" size="small" :color="CLS_COLOR[node.cls] || 'default'">
                                    {{ CLS_LABEL[node.cls] || node.cls }}
                                </NnTag>
                            </span>
                        </template>
                    </NnTree>
                    <NnEmpty v-if="!treeData.length && !hits.length" description="编译后这里显示 OID 树" simple />
                </div>
            </div>
        </NnCard>

        <NnContextMenu ref="ctxRef" v-model:open="ctxOpen"
                       :title="ctxNode ? ctxNode.name : ''" :meta="ctxNode ? ctxNode.oid : ''">
            <button class="ctx-item" @click="showDetail"><InfoCircleOutlined /> 查看详情</button>
            <button class="ctx-item" @click="copyOid">复制 OID</button>
        </NnContextMenu>

        <NnModal v-model:open="detailOpen" :title="detail ? `${detail.module}::${detail.name}` : 'MIB 节点'"
                 :footer="null" :width="640">
            <NnDescriptions v-if="detail" bordered size="small" :column="1">
                <NnDescriptionsItem label="OID"><span class="mono">{{ detail.oid }}</span></NnDescriptionsItem>
                <NnDescriptionsItem label="类别">{{ CLS_LABEL[detail.class] || detail.class || '—' }}</NnDescriptionsItem>
                <NnDescriptionsItem label="节点类型">{{ detail.nodetype || '—' }}</NnDescriptionsItem>
                <NnDescriptionsItem label="语法"><span class="mono">{{ detail.syntax || '—' }}</span></NnDescriptionsItem>
                <NnDescriptionsItem label="访问">{{ detail.access || '—' }}</NnDescriptionsItem>
                <NnDescriptionsItem label="状态">{{ detail.status || '—' }}</NnDescriptionsItem>
                <NnDescriptionsItem v-if="detail.objects" label="通知携带对象">
                    <span class="mono">{{ detail.objects.join(', ') }}</span>
                </NnDescriptionsItem>
                <NnDescriptionsItem label="描述"><pre class="desc">{{ detail.description || '—' }}</pre></NnDescriptionsItem>
            </NnDescriptions>
        </NnModal>
    </div>
</template>

<script setup>
import { h, nextTick, onMounted, onUnmounted, ref } from 'vue';
import {
    NnTag as Tag, NnButton as Btn, UploadOutlined, InfoCircleOutlined, notificationService
} from 'netnexus-ui';
import { eventsApi, mibsApi } from '../../shared/api.js';

const CLS_LABEL = {
    objecttype: '对象', notificationtype: '通知', objectidentity: '标识', moduleidentity: '模块',
    notificationgroup: '通知组', objectgroup: '对象组', modulecompliance: '合规', type: '类型'
};
const CLS_COLOR = { objecttype: 'blue', notificationtype: 'purple', objectidentity: 'cyan', moduleidentity: 'green' };

const recv = ref(null);
const port = ref(1162);
const communities = ref('');
const saving = ref(false);
const events = ref([]);
const fileRef = ref(null);
const rows = ref([]);
const status = ref(null);
const compiling = ref(false);
const treeData = ref([]);
const expandedKeys = ref([]);
const selectedKeys = ref([]);
const q = ref('');
const hits = ref([]);
const ctxRef = ref(null);
const ctxOpen = ref(false);
const ctxNode = ref(null);
const detailOpen = ref(false);
const detail = ref(null);
let timer = null;

const evColumns = [
    { title: '设备', dataIndex: 'device', width: 100, customRender: ({ text, record }) => text || record.source_ip },
    { title: 'Trap', dataIndex: 'event', width: 260, customRender: ({ text }) => h('span', { class: 'mono' }, text) },
    { title: 'varbinds', dataIndex: 'message', ellipsis: true },
    { title: '时间', dataIndex: 'created_at', width: 165, customRender: ({ text }) => h('span', { class: 'mono dim' }, text) }
];

const srcColumns = [
    { title: '模块', dataIndex: 'module', width: 200, customRender: ({ text }) => h('span', { class: 'mono' }, text) },
    { title: '来源', dataIndex: 'origin', width: 70,
      customRender: ({ text }) => h(Tag, { color: text === 'user' ? 'blue' : 'default' }, () => (text === 'user' ? '导入' : '自带')) },
    { title: '编译', width: 130,
      customRender: ({ record }) => {
          const m = record.mod;
          if (!m) return h('span', { class: 'dim' }, '未编译');
          return h(Tag, { color: m.status === 'compiled' ? 'green' : 'red' },
              () => (m.status === 'compiled' ? `通过 · ${m.symbols}` : m.status));
      } },
    { title: '错误', ellipsis: true, customRender: ({ record }) => h('span', { class: 'dim' }, record.mod ? record.mod.error : '') },
    { title: '', width: 64,
      customRender: ({ record }) => (record.origin === 'user'
          ? h(Btn, { size: 'small', variant: 'text', danger: true, onClick: () => remove(record) }, () => '删除') : null) }
];

function toNode(n) {
    return { key: n.oid, oid: n.oid, name: n.name, module: n.module, cls: n.class, title: n.name,
             isLeaf: !n.has_children, children: n.has_children ? [] : undefined };
}

async function loadEvents() {
    try {
        events.value = await eventsApi.list('trap');
        recv.value = await eventsApi.receivers();
        if (recv.value.defaults && !saving.value) {
            port.value = recv.value.defaults.trap;
            if (!communities.value) communities.value = (recv.value.trap.communities || []).join(',');
        }
    } catch (err) {
        notificationService.error(err.message);
    }
}

async function loadMibs() {
    const [srcs, st] = await Promise.all([mibsApi.sources(), mibsApi.status()]);
    const mods = new Map((st.modules || []).map(m => [m.module, m]));
    rows.value = srcs.map(s => ({ ...s, mod: mods.get(s.module) }));
    status.value = st;
    treeData.value = (await mibsApi.tree('')).map(toNode);
}

async function apply() {
    saving.value = true;
    try {
        await eventsApi.startReceivers({
            trap_port: port.value,
            communities: communities.value.split(',').map(s => s.trim()).filter(Boolean)
        });
        notificationService.success('Trap 监听已重启');
        await loadEvents();
    } catch (err) {
        notificationService.error(err.message);
    } finally {
        saving.value = false;
    }
}

async function compile() {
    compiling.value = true;
    try {
        const st = await mibsApi.compile();
        notificationService[st.ok ? 'success' : 'warning'](`编译完成：${st.compiled}/${st.total} 通过，OID ${st.oid_count} 条`);
        expandedKeys.value = [];
        await loadMibs();
    } catch (err) {
        notificationService.error(err.message);
    } finally {
        compiling.value = false;
    }
}

async function onPick(event) {
    const files = Array.from(event.target.files || []);
    event.target.value = '';
    let ok = 0;
    for (const f of files) {
        const fd = new FormData();
        fd.append('file', f);
        try { await mibsApi.upload(fd); ok++; }
        catch (err) { notificationService.error(`${f.name}: ${err.message}`); }
    }
    if (ok) notificationService.success(`已导入 ${ok} 个 MIB，点「编译全部」生效`);
    await loadMibs();
}

async function remove(row) {
    await mibsApi.deleteSource(row.file);
    await loadMibs();
}

function findNode(list, key) {
    for (const n of list) {
        if (n.key === key) return n;
        if (n.children && n.children.length) {
            const hit = findNode(n.children, key);
            if (hit) return hit;
        }
    }
    return null;
}

async function ensureChildren(node) {
    if (node && node.children && node.children.length === 0) {
        node.children = (await mibsApi.tree(node.oid)).map(toNode);
    }
}

async function onExpand(keys, info) {
    const key = info && info.node ? (info.node.key || info.node.oid) : null;
    await ensureChildren(key ? findNode(treeData.value, key) : null);
}

function ctxFor(event, node) {
    ctxNode.value = node;
    ctxOpen.value = true;
    nextTick(() => ctxRef.value && ctxRef.value.openAt(event));
}

function onRightClick(info) {
    const node = info && info.node ? info.node : null;
    if (node && info.event) ctxFor(info.event, node);
}

async function showDetail() {
    ctxOpen.value = false;
    if (!ctxNode.value) return;
    try {
        detail.value = await mibsApi.node(ctxNode.value.oid);
        detailOpen.value = true;
    } catch (err) {
        notificationService.error(err.message);
    }
}

async function copyOid() {
    ctxOpen.value = false;
    try { await navigator.clipboard.writeText(ctxNode.value.oid); notificationService.success('已复制'); }
    catch { notificationService.warning(ctxNode.value.oid); }
}

async function doSearch() {
    hits.value = q.value.trim() ? await mibsApi.search(q.value.trim()) : [];
}

async function jump(hit) {
    hits.value = [];
    q.value = '';
    const parts = hit.oid.split('.');
    const keys = [];
    for (let i = 1; i < parts.length; i++) {
        const node = findNode(treeData.value, parts.slice(0, i).join('.'));
        if (node) { await ensureChildren(node); keys.push(node.oid); }
    }
    expandedKeys.value = keys;
    selectedKeys.value = [hit.oid];
    detail.value = await mibsApi.node(hit.oid);
    detailOpen.value = true;
}

onMounted(async () => {
    await Promise.all([loadEvents(), loadMibs()]);
    timer = setInterval(loadEvents, 4000);
});
onUnmounted(() => clearInterval(timer));
</script>

<style scoped>
.row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.lbl { font-size: 12.5px; color: var(--nn-color-text-secondary, #6b7280); }
.note { font-size: 12px; line-height: 1.7; margin: 10px 0 0; }
.grid { display: grid; grid-template-columns: minmax(360px, 1fr) minmax(360px, 1.2fr); gap: 16px; align-items: start; }
.tree-pane :deep(.nn-tree) { max-height: 460px; overflow: auto; }
.node { display: inline-flex; align-items: center; gap: 6px; font-size: 12.5px; }
.nm { font-weight: 500; }
.hits { display: flex; flex-direction: column; gap: 4px; max-height: 460px; overflow: auto; }
.hit { display: flex; justify-content: space-between; gap: 12px; padding: 6px 10px;
       border: 1px solid var(--nn-color-border, #e5e7eb); border-radius: 5px; background: none;
       cursor: pointer; font: inherit; font-size: 12.5px; color: inherit; text-align: left; }
.hit:hover { background: var(--nn-color-fill-quaternary, rgba(0, 0, 0, 0.03)); }
.desc { margin: 0; white-space: pre-wrap; font-family: inherit; font-size: 12.5px; line-height: 1.6; }
.vb { padding: 4px 8px; }
.vb-row { display: grid; grid-template-columns: 260px 1fr 220px; gap: 12px; font-size: 12px; padding: 2px 0; }
.vb-name { color: var(--nn-color-primary, #1668dc); }
.vb-oid { text-align: right; }
.ctx-item { display: flex; align-items: center; gap: 8px; width: 100%; padding: 8px 12px; border: 0;
            border-radius: 5px; background: none; cursor: pointer; font: inherit; font-size: 13px;
            text-align: left; color: inherit; }
.ctx-item:hover { background: var(--nn-color-fill-quaternary, rgba(0, 0, 0, 0.04)); }
@media (max-width: 1100px) { .grid { grid-template-columns: 1fr; } }
</style>
