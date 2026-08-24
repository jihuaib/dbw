<template>
    <div class="stack-md">
        <NnAlert type="info" show-icon>
            知识库只回答一件事：<b>这台设备上有哪些只读命令可用、各自看什么</b>。
            回显怎么解读交给 AI，不再自建解析器体系 —— 所以加一条命令的成本是 0。
            写命令永不进清单，诊断不该改设备状态。
        </NnAlert>

        <NnCard>
            <template #title>
                <b>资料</b>
                <span class="dim" style="margin-left: 10px">
                    支持 .docx / .md / .txt
                </span>
            </template>
            <template #extra>
                <NnSpace>
                    <NnSelect v-model:value="engine" :options="engineOptions"
                              style="width: 200px" />
                    <NnButton size="small" :loading="loadingSample" @click="loadSample">
                        导入内置 CLI 手册（{{ sampleCount }} 份）
                    </NnButton>
                    <NnButton size="small" type="primary" @click="pick">
                        <ImportOutlined /> 导入资料
                    </NnButton>
                </NnSpace>
            </template>

            <input ref="fileRef" type="file" accept=".docx,.md,.markdown,.txt"
                   style="display: none" @change="onFile" />

            <NnTable :columns="docColumns" :data-source="docs" row-key="id"
                     size="small" bordered>
                <template #emptyText>
                    <span class="dim">还没有资料。导入一份 Word 或 Markdown 手册。</span>
                </template>
            </NnTable>
        </NnCard>

        <NnCard>
            <template #title><b>命令清单 · {{ commands.length }}</b></template>
            <template #extra>
                <NnSpace>
                    <NnInputSearch v-model:value="q" placeholder="搜索命令或用途"
                                   allow-clear style="width: 240px" @search="loadCommands" />
                    <NnTag color="cyan" class="mono" v-if="summary">
                        清单指纹 {{ summary.catalog_digest.slice(0, 16) }}
                    </NnTag>
                </NnSpace>
            </template>

            <NnTable :columns="cmdColumns" :data-source="commands" row-key="id"
                     size="small" bordered :scroll="{ y: 420 }">
                <template #emptyText>
                    <span class="dim">清单为空。先导入资料。</span>
                </template>
            </NnTable>
            <p class="dim" style="font-size: 12px; margin: 12px 0 0">
                清单指纹参与诊断指纹计算：启用/停用任何一条命令，都会让既有冻结答案失效 ——
                因为可用证据变了，结论就不该沿用。<br />
                标了<b>必需参数</b>的命令不能裸发（设备会回 Incomplete），
                编排层只在能从提问里取到真实参数值时才选它，<b>绝不猜</b>。
            </p>
        </NnCard>
    </div>
</template>

<script setup>
import { h, inject, onMounted, ref } from 'vue';
import { ImportOutlined, NnButton, NnSpace, NnSwitch, notificationService } from 'netnexus-ui';
import { kbApi } from '../../shared/api.js';
import { confirmAsync } from '../../shared/dialog.js';

const reloadMeta = inject('reloadMeta', () => {});
const docs = ref([]);
const commands = ref([]);
const summary = ref(null);
const q = ref('');
const engine = ref('auto');
const fileRef = ref(null);
const loadingSample = ref(false);
const sampleCount = ref(0);

const engineOptions = [
    { label: '自动识别（推荐）', value: 'auto' },
    { label: 'Markdown 表格提取', value: 'table' },
    { label: '正则提取（散文式文档）', value: 'rule' },
    { label: 'AI 提取（最全，需 Key）', value: 'ai' }
];

const docColumns = [
    { title: '名称', dataIndex: 'name', ellipsis: true },
    { title: '类型', dataIndex: 'kind', width: 80 },
    { title: '提取引擎', dataIndex: 'engine', width: 100 },
    { title: '字符数', dataIndex: 'chars', width: 90, align: 'right' },
    { title: '命令数', dataIndex: 'command_count', width: 90, align: 'right' },
    {
        title: 'SHA256', dataIndex: 'sha256_short', width: 130,
        customRender: ({ text }) => h('span', { class: 'mono' }, text)
    },
    {
        title: '操作', dataIndex: 'id', width: 80, key: 'ops',
        customRender: ({ record }) =>
            h(NnButton, { size: 'small', variant: 'text', onClick: () => remove(record) },
                () => '删除')
    }
];

const cmdColumns = [
    {
        title: '启用', dataIndex: 'enabled', width: 70,
        customRender: ({ record }) =>
            h(NnSwitch, {
                checked: record.enabled, size: 'small',
                'onUpdate:checked': v => toggle(record, v)
            })
    },
    {
        title: '命令', dataIndex: 'command', width: 240,
        customRender: ({ text }) => h('span', { class: 'mono' }, text)
    },
    {
        title: '完整语法', dataIndex: 'syntax', width: 280, ellipsis: true,
        customRender: ({ text }) => h('span', { class: 'mono dim' }, text || '—')
    },
    { title: '用途', dataIndex: 'purpose', ellipsis: true },
    {
        title: '必需参数', dataIndex: 'required', width: 150,
        customRender: ({ text }) =>
            (text || []).length
                ? h('span', { class: 'mono warn-text' }, text.join(' / '))
                : h('span', { class: 'dim' }, '—')
    }
];

function pick() {
    if (fileRef.value) {
        fileRef.value.value = '';
        fileRef.value.click();
    }
}

async function onFile(event) {
    const file = event.target.files && event.target.files[0];
    if (!file) return;
    const fd = new FormData();
    fd.append('file', file);
    fd.append('engine', engine.value);
    try {
        const res = await kbApi.upload(fd);
        await refresh();
        reloadMeta();
        notificationService.success(
            `已导入，提取 ${res.found} 条命令，新增 ${res.added} 条`);
        if (res.warn) notificationService.warning(res.warn);
    } catch (err) {
        notificationService.error(err.message);
    }
}

async function loadSample() {
    loadingSample.value = true;
    try {
        const files = await kbApi.samples();
        if (!files.length) throw new Error('没有内置资料');
        const out = await kbApi.importSamples(files, engine.value);
        await refresh();
        reloadMeta();
        notificationService.success(
            `${out.files} 份文档已导入，提取 ${out.found} 条，新增 ${out.added} 条命令`);
    } catch (err) {
        notificationService.error(err.message);
    } finally {
        loadingSample.value = false;
    }
}

async function remove(record) {
    const ok = await confirmAsync({
        title: `删除「${record.name}」？`,
        content: '该资料提取出的命令会一并删除，命令清单指纹随之变化。',
        okText: '删除', cancelText: '取消'
    });
    if (!ok) return;
    await kbApi.deleteDoc(record.id);
    await refresh();
    reloadMeta();
}

async function toggle(record, enabled) {
    await kbApi.toggle(record.id, enabled);
    await refresh();
    reloadMeta();
}

async function loadCommands() {
    commands.value = await kbApi.commands(q.value);
}

async function refresh() {
    [docs.value, summary.value] = await Promise.all([kbApi.docs(), kbApi.summary()]);
    await loadCommands();
}

onMounted(async () => {
    try {
        sampleCount.value = (await kbApi.samples()).length;
        await refresh();
    } catch (err) {
        notificationService.error(`后端未就绪：${err.message}`);
    }
});
</script>
