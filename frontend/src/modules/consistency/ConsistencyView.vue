<template>
    <div class="stack-md">
        <NnAlert type="info" show-icon>
            赛题判据的直接检验。<b>三个条件缺一不可</b>：
            期间原始回显必须真的变过（否则什么都没检验）、快照哈希必须唯一、正文必须逐字相同。
        </NnAlert>

        <NnCard>
            <template #title><b>运行验证</b></template>
            <NnForm layout="vertical" :model="form">
                <NnRow :gutter="16">
                    <NnCol :span="12">
                        <NnFormItem label="提问">
                            <NnInput v-model:value="form.question" />
                        </NnFormItem>
                    </NnCol>
                    <NnCol :span="3">
                        <NnFormItem label="轮数">
                            <NnSelect v-model:value="form.rounds" :options="roundOptions" />
                        </NnFormItem>
                    </NnCol>
                    <NnCol :span="3">
                        <NnFormItem label="轮间隔">
                            <NnSelect v-model:value="form.gap_ms" :options="gapOptions" />
                        </NnFormItem>
                    </NnCol>
                    <NnCol :span="3">
                        <NnFormItem label=" ">
                            <NnButton type="primary" :loading="running" block @click="run">
                                开始
                            </NnButton>
                        </NnFormItem>
                    </NnCol>
                </NnRow>
                <NnSegmented v-model:value="form.mode" :options="modeOptions" />
            </NnForm>
        </NnCard>

        <NnSpin :spinning="running">
            <NnCard v-if="result">
                <template #title><b>结果</b></template>
                <template #extra>
                    <NnTag :color="verdict.color">{{ verdict.label }}</NnTag>
                </template>

                <NnAlert :type="verdict.type" show-icon style="margin-bottom: 14px">
                    {{ verdict.text }}
                </NnAlert>

                <div class="grid-stats" style="margin-bottom: 16px">
                    <NnStatistic title="原始回显变化" :value="driftText" />
                    <NnStatistic title="归一后仍变化" :value="`${result.raw_drift.norm_changed || 0} 条`" />
                    <NnStatistic title="SSR 快照稳定率" :value="`${(result.ssr * 100).toFixed(0)}%`" />
                    <NnStatistic title="不同指纹数" :value="result.distinct_fingerprints" />
                    <NnStatistic title="正文字节一致" :value="result.byte_identical ? '是' : '否'" />
                </div>

                <NnTable :columns="roundColumns" :data-source="result.results"
                         row-key="round" size="small" bordered />

                <div style="margin-top: 16px">
                    <b>原始回显漂移明细</b>
                    <span class="dim" style="margin-left: 8px; font-size: 12px">
                        第 1 轮 vs 最后一轮 —— 变了才说明输入真的在动
                    </span>
                    <NnTable :columns="driftColumns" :data-source="driftRows"
                             row-key="_k" size="small" bordered style="margin-top: 8px"
                             :scroll="{ y: 320 }" />
                </div>
            </NnCard>
        </NnSpin>

        <NnCard>
            <template #title><b>冻结答案</b></template>
            <template #extra>
                <NnButton size="small" @click="loadFrozen"><ReloadOutlined /> 刷新</NnButton>
            </template>
            <p class="dim" style="margin-top: 0">
                兜底 F0 的底账。指纹命中即原样返回，<b>零模型调用</b> ——
                这就是「同一输入只调一次模型」的落地。
            </p>
            <NnTable :columns="frozenColumns" :data-source="frozen" row-key="fingerprint"
                     size="small" bordered>
                <template #emptyText>
                    <span class="dim">还没有冻结答案。先在「诊断」页问一次。</span>
                </template>
            </NnTable>
        </NnCard>
    </div>
</template>

<script setup>
import { computed, h, onMounted, reactive, ref } from 'vue';
import { ReloadOutlined, NnButton, NnSpace, NnTag, notificationService } from 'netnexus-ui';
import { diagnoseApi } from '../../shared/api.js';
import { confirmAsync } from '../../shared/dialog.js';

const result = ref(null);
const running = ref(false);
const frozen = ref([]);

const form = reactive({
    question: 'LEAF2 好像连不上了，帮我看看',
    rounds: 5,
    mode: 'cross',
    gap_ms: 2000
});

const modeOptions = [
    { label: '多会话交互', value: 'cross' },
    { label: '单会话多次交互', value: 'single' }
];
const roundOptions = [3, 5, 8, 10].map(v => ({ label: `${v} 轮`, value: v }));
const gapOptions = [1500, 2000, 3000, 5000].map(v => ({ label: `${v / 1000}s`, value: v }));

const driftText = computed(() => {
    const d = result.value && result.value.raw_drift;
    return d ? `${d.changed} / ${d.total} 条` : '—';
});
const driftRows = computed(() =>
    ((result.value && result.value.raw_drift.rows) || []).map((r, i) => ({ ...r, _k: i })));

const verdict = computed(() => {
    const r = result.value;
    if (!r) return { type: 'info', color: 'blue', label: '', text: '' };
    if (!r.consistent) {
        return {
            type: 'error', color: 'red', label: '不一致',
            text: `同一问题 ${r.rounds} 次，得到 ${r.distinct_fingerprints} 个不同指纹。需要排查。`
        };
    }
    if (!r.input_really_changed) {
        return {
            type: 'warning', color: 'orange', label: '结论无效',
            text: '期间原始回显没有发生变化 —— 输入压根没漂移，此时「指纹一致」证明不了归一化层有效。请调大轮间隔重试。'
        };
    }
    const where = r.mode === 'single' ? '同一会话里连问' : '各在一个全新会话里问';
    return {
        type: 'success', color: 'green', label: '一致',
        text: `同一问题${where} ${r.rounds} 次：期间 ${r.raw_drift.changed} 条命令的原始回显字节已改变，`
            + `但快照哈希唯一、诊断指纹唯一、正文逐字相同。`
    };
});

const roundColumns = [
    { title: '轮', dataIndex: 'round', width: 50 },
    { title: '会话', dataIndex: 'session_id', width: 64 },
    { title: '纪元', dataIndex: 'epoch_id', width: 64 },
    { title: '兜底级', dataIndex: 'fallback_level', width: 80 },
    { title: '根因数', dataIndex: 'root_causes', width: 74, align: 'right' },
    {
        title: 'snapshot_hash', dataIndex: 'snapshot_hash', width: 200,
        customRender: ({ text }) => h('span', { class: 'mono' }, String(text).slice(0, 24))
    },
    {
        title: '诊断指纹', dataIndex: 'fingerprint',
        customRender: ({ text }) => h('span', { class: 'mono' }, String(text).slice(0, 32))
    }
];

const driftColumns = [
    { title: '设备', dataIndex: 'device', width: 84 },
    {
        title: '命令', dataIndex: 'command', width: 260,
        customRender: ({ text }) => h('span', { class: 'mono' }, text)
    },
    {
        title: '第 1 轮', dataIndex: 'raw_a', width: 100,
        customRender: ({ text }) => h('span', { class: 'mono' }, text)
    },
    {
        title: '最后一轮', dataIndex: 'raw_b', width: 100,
        customRender: ({ text }) => h('span', { class: 'mono' }, text)
    },
    {
        title: '原始回显', dataIndex: 'raw_changed', width: 100,
        customRender: ({ text }) =>
            h('span', { class: text ? 'warn-text' : 'dim' }, text ? '★ 已变' : '未变')
    },
    {
        title: '归一化后', dataIndex: 'norm_changed', width: 100,
        customRender: ({ text }) =>
            h('span', { class: text ? 'warn-text' : 'dim' }, text ? '❌ 仍在变' : '✓ 稳定')
    }
];

const frozenColumns = [
    {
        title: '指纹', dataIndex: 'fingerprint', width: 190,
        customRender: ({ text }) => h('span', { class: 'mono' }, String(text).slice(0, 24))
    },
    { title: '问题（归一化后）', dataIndex: 'question_norm', ellipsis: true },
    {
        title: 'snapshot', dataIndex: 'snapshot_hash', width: 130,
        customRender: ({ text }) => h('span', { class: 'mono' }, String(text).slice(0, 14))
    },
    { title: '模型', dataIndex: 'model', width: 130 },
    { title: '命中', dataIndex: 'hit_count', width: 66, align: 'right' },
    {
        title: '状态', dataIndex: 'verified', width: 90,
        customRender: ({ text }) =>
            h(NnTag, { color: text ? 'green' : 'orange' }, () => (text ? '已确认' : '未确认'))
    },
    {
        title: '操作', dataIndex: 'fingerprint', width: 150, key: 'ops',
        customRender: ({ record }) =>
            h(NnSpace, null, () => [
                h(NnButton, { size: 'small',
                    onClick: () => verify(record) }, () => (record.verified ? '取消确认' : '确认')),
                h(NnButton, { size: 'small', variant: 'text',
                    onClick: () => unfreeze(record) }, () => '解冻')
            ])
    }
];

async function run() {
    running.value = true;
    result.value = null;
    try {
        result.value = await diagnoseApi.check({ ...form });
        await loadFrozen();
    } catch (err) {
        notificationService.error(err.message);
    } finally {
        running.value = false;
    }
}

async function loadFrozen() {
    frozen.value = await diagnoseApi.frozen();
}

async function verify(record) {
    await diagnoseApi.verify(record.fingerprint, !record.verified);
    await loadFrozen();
}

async function unfreeze(record) {
    const ok = await confirmAsync({
        title: '解冻这条答案？',
        content: '解冻后同一故障会重新调用一次模型生成新答案，并再次冻结。',
        okText: '解冻', cancelText: '取消'
    });
    if (!ok) return;
    await diagnoseApi.unfreeze(record.fingerprint);
    await loadFrozen();
}

onMounted(async () => {
    try {
        await loadFrozen();
    } catch (err) {
        notificationService.error(err.message);
    }
});
</script>
