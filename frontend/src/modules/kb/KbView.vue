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
                    <NnButton size="small" type="primary" :disabled="Boolean(job)" @click="pick">
                        <ImportOutlined /> 导入文件
                    </NnButton>
                    <NnButton size="small" :disabled="Boolean(job)" @click="pickDir">
                        <ImportOutlined /> 导入文件夹
                    </NnButton>
                </NnSpace>
            </template>

            <input ref="fileRef" type="file" accept=".docx,.md,.markdown,.txt" multiple
                   style="display: none" @change="onFiles" />
            <input ref="dirRef" type="file" webkitdirectory directory multiple
                   style="display: none" @change="onFiles" />

            <div class="dir-row">
                <NnInput v-model:value="serverDir" size="small" style="width: 380px"
                         placeholder="服务器上的目录路径（手册放在服务器时无需经浏览器上传）" />
                <NnButton size="small" :disabled="!serverDir.trim() || Boolean(job)" @click="importDir">
                    导入服务器目录（递归）
                </NnButton>
            </div>

            <div v-if="job" class="job">
                <NnProgress :percent="job.total ? Math.round((job.done / job.total) * 100) : 0"
                            :status="job.status === 'done' ? 'success' : 'active'" />
                <div class="job-line mono dim">
                    {{ job.done }}/{{ job.total }} · 提取 {{ job.found }} · 新增 {{ job.added }}
                    · 重复跳过 {{ job.skipped }} · 失败 {{ job.failed }}
                    <span v-if="job.current"> · 正在处理 {{ job.current }}</span>
                </div>
                <div v-if="job.errors.length" class="job-errors">
                    <div v-for="(e, i) in job.errors" :key="i" class="dim" style="font-size: 12px">
                        ✗ {{ e.file }}：{{ e.error }}
                    </div>
                </div>
            </div>

            <NnTable :columns="docColumns" :data-source="docs" row-key="id"
                     size="small" bordered :pagination="{ pageSize: 20 }">
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

const DOC_EXT = /\.(docx|md|markdown|txt)$/i;
const CHUNK = 20;                 // 每次请求带的文件数：文件多时分批传，服务端逐个落库
const dirRef = ref(null);
const serverDir = ref('');
const job = ref(null);

function pickDir() {
    dirRef.value && dirRef.value.click();
}

async function pollJob(jobId, agg) {
    for (;;) {
        const j = await kbApi.job(jobId);
        job.value = {
            ...j,
            done: agg.done + j.done, total: agg.total, found: agg.found + j.found,
            added: agg.added + j.added, skipped: agg.skipped + j.skipped,
            failed: agg.failed + j.failed, errors: [...agg.errors, ...j.errors].slice(0, 30),
            status: 'running'
        };
        if (j.status === 'done') {
            agg.done += j.done; agg.found += j.found; agg.added += j.added;
            agg.skipped += j.skipped; agg.failed += j.failed;
            agg.errors = [...agg.errors, ...j.errors].slice(0, 30);
            return;
        }
        await new Promise(r => setTimeout(r, 700));
    }
}

async function onFiles(event) {
    const all = Array.from(event.target.files || []).filter(f => DOC_EXT.test(f.name));
    event.target.value = '';
    if (!all.length) { notificationService.warning('没有可导入的文档（.docx / .md / .markdown / .txt）'); return; }
    const agg = { done: 0, total: all.length, found: 0, added: 0, skipped: 0, failed: 0, errors: [] };
    job.value = { ...agg, status: 'running', current: '' };
    try {
        for (let i = 0; i < all.length; i += CHUNK) {
            const fd = new FormData();
            for (const f of all.slice(i, i + CHUNK)) fd.append('files', f, f.webkitRelativePath || f.name);
            fd.append('engine', engine.value);
            const { job_id } = await kbApi.uploadBatch(fd);
            await pollJob(job_id, agg);
        }
        job.value = { ...agg, status: 'done', current: '' };
        await refresh();
        reloadMeta();
        notificationService.success(`${agg.total} 份文档处理完成：提取 ${agg.found} 条，新增 ${agg.added} 条，重复跳过 ${agg.skipped}，失败 ${agg.failed}`);
    } catch (err) {
        notificationService.error(err.message);
        job.value = null;
    }
}

async function importDir() {
    const agg = { done: 0, total: 0, found: 0, added: 0, skipped: 0, failed: 0, errors: [] };
    try {
        const { job_id, total } = await kbApi.importDir(serverDir.value.trim(), engine.value);
        agg.total = total;
        job.value = { ...agg, status: 'running', current: '' };
        await pollJob(job_id, agg);
        job.value = { ...agg, status: 'done', current: '' };
        await refresh();
        reloadMeta();
        notificationService.success(`目录导入完成：${agg.total} 份，提取 ${agg.found} 条，新增 ${agg.added} 条，重复跳过 ${agg.skipped}，失败 ${agg.failed}`);
    } catch (err) {
        notificationService.error(err.message);
        job.value = null;
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
