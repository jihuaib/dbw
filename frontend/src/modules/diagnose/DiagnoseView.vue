<template>
    <div class="shell">
        <NnCard class="pane-left">
            <template #title><b>会话</b></template>
            <template #extra>
                <NnButton size="small" type="primary" @click="newSession">
                    <PlusOutlined /> 新建
                </NnButton>
            </template>
            <div class="sessions">
                <button v-for="s in sessions" :key="s.id" class="item"
                        :class="{ active: current && current.id === s.id }"
                        @click="open(s)"
                        @contextmenu.prevent="onSessionMenu($event, s)">
                    <span class="truncate t">{{ s.title }}</span>
                    <span class="mono dim m">{{ s.turn_count }} 轮</span>
                </button>
                <NnEmpty v-if="!sessions.length" description="还没有会话" simple />
                <NnContextMenu ref="ctxRef" v-model:open="ctxOpen"
                               :title="ctxSession ? ctxSession.title : ''"
                               :meta="ctxSession ? `${ctxSession.turn_count} 轮` : ''"
                               hint="删除会同时清掉该会话独占的采集纪元、回显与冻结答案">
                    <button class="ctx-item danger" @click="removeSession">
                        <DeleteOutlined /> 删除会话及关联记录
                    </button>
                </NnContextMenu>
            </div>
        </NnCard>

        <div class="pane-main">
            <NnCard v-if="!current"><NnEmpty description="新建一个会话开始提问" /></NnCard>
            <template v-else>
                <div ref="threadRef" class="thread" @scroll.passive="onThreadScroll">
                    <div v-if="!turns.length" class="starters">
                        <NnAlert v-if="!devices.length" type="warning" show-icon
                                 style="margin-bottom: 12px">
                            设备清单为空。先去「设备与拓扑」添加 CNetNexus 设备。
                        </NnAlert>
                        <p class="dim">试试这些问题 —— AI 会自己决定要采哪些命令：</p>
                        <NnSpace wrap>
                            <NnButton v-for="s in STARTERS" :key="s" size="small"
                                      @click="ask(s)">{{ s }}</NnButton>
                        </NnSpace>
                    </div>

                    <div v-for="t in turns" :key="t.id" class="turn">
                        <div class="bubble ask">
                            <span class="mono dim">#{{ t.seq }}</span>
                            <span>{{ t.question }}</span>
                        </div>

                        <div class="bubble reply">
                            <div class="head">
                                <NnTag :color="LEVEL[t.fallback_level]?.color || 'blue'">
                                    {{ LEVEL[t.fallback_level]?.label || t.fallback_level }}
                                </NnTag>
                                <NnTag v-if="t.answer.confidence"
                                       :color="CONF[t.answer.confidence] || 'blue'">
                                    置信度 {{ t.answer.confidence }}
                                </NnTag>

                            </div>

                            <Markdown class="answer" :source="t.answer.text" />

                            <div class="fp mono">
                                <div><span class="k">诊断指纹</span><b>{{ t.fingerprint }}</b></div>
                                <div><span class="k">snapshot</span>{{ t.snapshot_hash }}</div>
                                <div>
                                    <span class="k">采集</span>{{ (t.plan || []).length }} 条命令
                                    （编排 {{ t.plan_engine }}）· 纪元 #{{ t.epoch_id }}
                                </div>
                            </div>

                            <NnSpace style="margin-top: 10px" wrap>
                                <NnButton size="small" variant="text"
                                          @click="tog(t.id, 'trace')">
                                    执行轨迹
                                </NnButton>
                                <NnButton size="small" variant="text"
                                          @click="tog(t.id, 'plan')">
                                    采集计划（{{ (t.plan || []).length }}）
                                </NnButton>
                                <NnButton size="small" variant="text"
                                          :loading="loadingEpoch === t.epoch_id"
                                          @click="loadEpoch(t)">
                                    证据快照
                                </NnButton>
                                <NnButton size="small" variant="text"
                                          :loading="loadingPrompt === t.id"
                                          @click="loadPrompt(t)">
                                    模型交互
                                </NnButton>
                            </NnSpace>

                            <div v-if="on(t.id, 'trace')" class="sub">
                                <div v-for="(x, i) in t.trace" :key="i" class="trace">
                                    <span class="mono st">{{ x.step }}</span>
                                    <span>{{ x.detail }}</span>
                                </div>
                            </div>

                            <div v-if="on(t.id, 'plan')" class="sub">
                                <NnTable :columns="planColumns" :data-source="withKey(t.plan || [])"
                                         row-key="_k" size="small" bordered
                                         :scroll="{ y: 260 }" />
                            </div>

                            <div v-if="prompts[t.id]" class="sub">
                                <NnTabs v-model:active-key="pTab">
                                    <NnTabPane v-if="(prompts[t.id].rounds || []).length"
                                               key="rounds" tab="Agent 逐轮交互">
                                        <div v-for="r in prompts[t.id].rounds" :key="r.round"
                                             class="round">
                                            <div class="round-head mono">
                                                第 {{ r.round }} 轮
                                                <NnTag v-if="r.cached" color="green" size="small">
                                                    缓存命中（未重复调模型）
                                                </NnTag>
                                            </div>
                                            <details>
                                                <summary class="dim">送给模型的内容</summary>
                                                <pre class="pre-box tall">{{ pretty(r.user) }}</pre>
                                            </details>
                                            <details>
                                                <summary class="dim">模型原始回复</summary>
                                                <pre class="pre-box tall">{{ pretty(r.raw) }}</pre>
                                            </details>
                                        </div>
                                    </NnTabPane>
                                    <NnTabPane key="system" tab="系统提示词">
                                        <pre class="pre-box tall">{{ prompts[t.id].prompt_system }}</pre>
                                    </NnTabPane>
                                    <NnTabPane key="user" tab="送给模型的内容">
                                        <pre class="pre-box tall">{{ pretty(prompts[t.id].prompt_user) }}</pre>
                                    </NnTabPane>
                                    <NnTabPane key="raw" tab="模型原始回复">
                                        <pre class="pre-box tall">{{ pretty(prompts[t.id].model_raw) }}</pre>
                                    </NnTabPane>
                                </NnTabs>
                            </div>

                            <div v-if="epochs[t.epoch_id]" class="sub">
                                <NnTabs v-model:active-key="evTab">
                                    <NnTabPane key="norm" tab="归一化后（送进 AI 的字节）">
                                        <pre class="pre-box tall">{{ epochs[t.epoch_id].snapshot }}</pre>
                                    </NnTabPane>
                                    <NnTabPane key="raw" tab="原始回显对照">
                                        <NnTable :columns="capColumns"
                                                 :data-source="epochs[t.epoch_id].captures"
                                                 row-key="id" size="small" bordered
                                                 :scroll="{ y: 300 }" />
                                    </NnTabPane>
                                    <NnTabPane key="full" tab="设备命令与完整回显">
                                        <div class="caps">
                                            <details v-for="c in epochs[t.epoch_id].captures"
                                                     :key="c.id" class="cap">
                                                <summary>
                                                    <span class="mono">{{ c.device }} # {{ c.command }}</span>
                                                    <NnTag :color="c.ok ? 'green' : 'red'" size="small">
                                                        {{ c.ok ? 'OK' : (c.error || '失败') }}
                                                    </NnTag>
                                                </summary>
                                                <pre class="pre-box tall">{{ c.raw_text || '（无回显）' }}</pre>
                                            </details>
                                        </div>
                                    </NnTabPane>
                                </NnTabs>
                            </div>
                        </div>
                    </div>
                    <!-- 正在进行的这一轮：实时显示每一步 -->
                    <div v-if="running" class="turn">
                        <div class="bubble ask">
                            <span class="mono dim">进行中</span>
                            <span>{{ running.question }}</span>
                        </div>
                        <div class="bubble reply live">
                            <div class="head">
                                <NnTag color="processing">正在诊断</NnTag>
                                <span class="dim mono">{{ elapsed }}s</span>
                            </div>
                            <template v-for="(ev, i) in liveFeed" :key="i">
                                <div v-if="ev.kind === 'stage'" class="pstep"
                                     :class="{ cur: i === liveFeed.length - 1 }">
                                    <span class="mono pst">{{ ev.stage }}</span>
                                    <span class="pdt">{{ ev.detail }}</span>
                                </div>
                                <details v-else-if="ev.kind === 'tool'" class="cap live-tool"
                                         :open="!ev.done">
                                    <summary>
                                        <span class="mono">{{ ev.device }} # {{ ev.command }}</span>
                                        <NnTag v-if="!ev.done" color="processing" size="small">
                                            执行中
                                        </NnTag>
                                        <NnTag v-else :color="ev.ok ? 'green' : 'red'" size="small">
                                            {{ ev.ok ? 'OK' : '失败' }}
                                        </NnTag>
                                    </summary>
                                    <pre v-if="ev.output" class="pre-box">{{ ev.output }}</pre>
                                </details>
                                <Markdown v-else-if="ev.kind === 'text'"
                                          class="answer streaming" :source="ev.text" />
                            </template>
                            <div v-if="!liveFeed.length" class="dim"
                                 style="font-size: 12.5px">正在启动…</div>
                        </div>
                    </div>

                </div>

                <NnCard class="composer" variant="plain">
                    <NnTextarea v-model:value="draft" :rows="3"
                                placeholder="描述故障现象或直接提问。Ctrl+Enter 发送。"
                                @keydown="onKey" />
                    <div class="row-between" style="margin-top: 10px">
                        <span class="dim" style="font-size: 12.5px">
                            Agent 多轮取证 · 会话历史（问题＋结论摘要）随指纹一起冻结，追问也保持确定
                        </span>
                        <NnButton type="primary" :disabled="!draft.trim() || asking"
                                  :loading="asking" @click="ask(draft)">
                            <SendOutlined /> {{ asking ? '诊断中…' : '发送' }}
                        </NnButton>
                    </div>
                </NnCard>
            </template>
        </div>
    </div>
</template>

<script setup>
import { computed, h, nextTick, onMounted, ref } from 'vue';
import {
    DeleteOutlined, PlusOutlined, SendOutlined,
    confirmDialogService, notificationService
} from 'netnexus-ui';
import { collectApi, deviceApi, diagnoseApi } from '../../shared/api.js';
import Markdown from '../../shared/Markdown.vue';

const STARTERS = [
    '整网现在有什么问题',
    'LEAF2 好像连不上了，帮我看看',
    'LLDP 邻居为什么少了',
    'OSPF 邻居起来了吗'
];

const LEVEL = {
    F0: { label: 'F0 指纹冻结命中（零模型调用）', color: 'green' },
    F3: { label: 'F3 自洽投票', color: 'cyan' },
    F4: { label: 'F4 模型兜底（仅证据陈述）', color: 'orange' },
    AI: { label: 'AI 生成并冻结', color: 'blue' }
};
const CONF = { 确认: 'green', 高: 'cyan', 中: 'orange', 需人工确认: 'red' };

const sessions = ref([]);
const current = ref(null);
const turns = ref([]);
const draft = ref('');
const asking = ref(false);
const expanded = ref({});
const epochs = ref({});
const loadingEpoch = ref(null);
const evTab = ref('norm');
const prompts = ref({});
const loadingPrompt = ref(null);
const pTab = ref('system');
const threadRef = ref(null);
const devices = ref([]);
const running = ref(null);
const ctxRef = ref(null);
const ctxOpen = ref(false);
const ctxSession = ref(null);
const elapsed = ref(0);
const startedAt = ref(0);

// 把 steps（阶段行）与 events（工具调用/回显/流式文本）合成一条对话流
const liveFeed = computed(() => {
    if (!running.value) return [];
    const feed = [];
    for (const s of running.value.steps || []) {
        feed.push({ kind: 'stage', stage: s.stage, detail: s.detail });
    }
    const tools = new Map();
    for (const ev of running.value.events || []) {
        if (ev.kind === 'tool_start') {
            const key = `${ev.payload.device}#${ev.payload.command}`;
            const item = { kind: 'tool', done: false, ok: false, output: '', ...ev.payload };
            tools.set(key, item);
            feed.push(item);
        } else if (ev.kind === 'tool_end') {
            const key = `${ev.payload.device}#${ev.payload.command}`;
            const item = tools.get(key);
            if (item) Object.assign(item, { done: true, ok: ev.payload.ok, output: ev.payload.output });
        } else if (ev.kind === 'delta') {
            const last = feed[feed.length - 1];
            if (last && last.kind === 'text') last.text += ev.payload.text;
            else feed.push({ kind: 'text', text: ev.payload.text });
        } else if (ev.kind === 'answer') {
            const last = feed[feed.length - 1];
            if (last && last.kind === 'text') last.text = ev.payload.text;
            else feed.push({ kind: 'text', text: ev.payload.text });
        }
    }
    return feed;
});


const planColumns = [
    { title: '设备', dataIndex: 'device', width: 90 },
    {
        title: '命令', dataIndex: 'command', width: 300,
        customRender: ({ text }) => h('span', { class: 'mono' }, text)
    },
    { title: '为什么采这一条', dataIndex: 'reason', ellipsis: true }
];

const capColumns = [
    { title: '设备', dataIndex: 'device', width: 84 },
    {
        title: '命令', dataIndex: 'command', width: 240,
        customRender: ({ text }) => h('span', { class: 'mono' }, text)
    },
    {
        title: '原始 SHA', dataIndex: 'raw_sha', width: 110,
        customRender: ({ text }) => h('span', { class: 'mono' }, String(text).slice(0, 10))
    },
    {
        title: '归一后 SHA', dataIndex: 'norm_sha', width: 110,
        customRender: ({ text }) => h('span', { class: 'mono' }, String(text).slice(0, 10))
    },
    {
        title: '擦除了什么', dataIndex: 'applied', ellipsis: true,
        customRender: ({ text }) => h('span', { class: 'dim' }, (text || []).join(' · ') || '—')
    }
];

const withKey = rows => (rows || []).map((r, i) => ({ ...r, _k: i }));
const on = (id, k) => Boolean(expanded.value[`${id}:${k}`]);
const tog = (id, k) => {
    expanded.value = { ...expanded.value, [`${id}:${k}`]: !on(id, k) };
};

function onSessionMenu(event, s) {
    ctxSession.value = s;
    ctxOpen.value = true;
    nextTick(() => ctxRef.value && ctxRef.value.openAt(event));
}

function removeSession() {
    const target = ctxSession.value;
    ctxOpen.value = false;
    if (!target) return;
    confirmDialogService.confirm({
        title: `删除会话「${target.title}」？`,
        content: `会话的 ${target.turn_count} 轮问答、独占的采集纪元与回显、`
            + '由它冻结的答案都会一并删除。被其他会话共享的记录不受影响。',
        okText: '删除',
        okType: 'danger',
        cancelText: '取消',
        onOk: async () => {
            await diagnoseApi.deleteSession(target.id);
            notificationService.success('已删除会话及关联记录');
            if (current.value && current.value.id === target.id) {
                current.value = null;
                turns.value = [];
            }
            await refreshSessions();
            if (!current.value && sessions.value.length) await open(sessions.value[0]);
        }
    });
}

async function refreshSessions() {
    sessions.value = await diagnoseApi.sessions();
}

async function newSession() {
    const s = await diagnoseApi.createSession('');
    await refreshSessions();
    await open(s);
}

async function open(s) {
    current.value = s;
    turns.value = await diagnoseApi.turns(s.id);
    stickBottom.value = true;
    scrollDown(true);
}

const stickBottom = ref(true);

function onThreadScroll() {
    const el = threadRef.value;
    if (!el) return;
    stickBottom.value = el.scrollHeight - el.scrollTop - el.clientHeight < 60;
}

function scrollDown(force = false) {
    nextTick(() => {
        const el = threadRef.value;
        if (el && (force || stickBottom.value)) el.scrollTop = el.scrollHeight;
    });
}

function onKey(event) {
    if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
        event.preventDefault();
        if (draft.value.trim()) ask(draft.value);
    }
}

async function ask(question) {
    if (!current.value || asking.value) return;
    asking.value = true;
    running.value = { question, steps: [], events: [] };
    startedAt.value = Date.now();
    draft.value = '';
    stickBottom.value = true;
    scrollDown(true);
    try {
        const { task_id } = await diagnoseApi.ask(current.value.id, { question });
        // 轮询进度 —— 几十秒的等待必须让人看见在做什么
        for (;;) {
            await new Promise(r => setTimeout(r, 500));
            const t = await diagnoseApi.task(task_id);
            running.value = { question, steps: t.steps, events: t.events || [] };
            elapsed.value = Math.round((Date.now() - startedAt.value) / 1000);
            scrollDown();
            if (t.status === 'done') {
                turns.value = [...turns.value, t.turn];
                break;
            }
            if (t.status === 'failed') {
                notificationService.error(t.error || '诊断失败');
                break;
            }
        }
        await refreshSessions();
    } catch (err) {
        notificationService.error(err.message);
    } finally {
        running.value = null;
        asking.value = false;
        scrollDown();
    }
}

function pretty(v) {
    if (v == null || v === '') return '（空）';
    try {
        const obj = typeof v === 'string' ? JSON.parse(v) : v;
        return JSON.stringify(obj, null, 2);
    } catch {
        return String(v);
    }
}

async function loadPrompt(t) {
    if (prompts.value[t.id]) {
        const next = { ...prompts.value };
        delete next[t.id];
        prompts.value = next;
        return;
    }
    loadingPrompt.value = t.id;
    try {
        const data = await diagnoseApi.turnPrompt(t.id);
        pTab.value = (data.rounds || []).length ? 'rounds' : 'system';
        prompts.value = { ...prompts.value, [t.id]: data };
    } catch (err) {
        notificationService.error(err.message);
    } finally {
        loadingPrompt.value = null;
    }
}

async function loadEpoch(t) {
    if (epochs.value[t.epoch_id]) {
        const next = { ...epochs.value };
        delete next[t.epoch_id];
        epochs.value = next;
        return;
    }
    loadingEpoch.value = t.epoch_id;
    try {
        epochs.value = { ...epochs.value, [t.epoch_id]: await collectApi.epoch(t.epoch_id) };
    } catch (err) {
        notificationService.error(err.message);
    } finally {
        loadingEpoch.value = null;
    }
}

onMounted(async () => {
    try {
        devices.value = await deviceApi.list();
        await refreshSessions();
        if (sessions.value.length) await open(sessions.value[0]);
        else await newSession();
    } catch (err) {
        notificationService.error(`后端未就绪：${err.message}`);
    }
});
</script>

<style scoped>
.shell {
    display: grid;
    grid-template-columns: minmax(240px, 300px) 1fr;
    gap: 16px;
    align-items: start;
    height: calc(100vh - 132px);
}

.field {
    margin-bottom: 14px;
    padding-bottom: 14px;
    border-bottom: 1px solid var(--nn-color-border, #e5e7eb);
}

.label {
    font-size: 12px;
    color: var(--nn-color-text-secondary, #6b7280);
    margin-bottom: 6px;
}

.desc {
    font-size: 12px;
    line-height: 1.65;
    margin: 8px 0 4px;
}

.sessions {
    display: flex;
    flex-direction: column;
    gap: 4px;
    max-height: 38vh;
    overflow-y: auto;
}

.item {
    display: flex;
    flex-direction: column;
    gap: 2px;
    width: 100%;
    padding: 8px 10px;
    border: 1px solid transparent;
    border-radius: 5px;
    background: none;
    cursor: pointer;
    text-align: left;
    color: inherit;
    font: inherit;
}

.item:hover {
    background: var(--nn-color-fill-quaternary, rgba(0, 0, 0, 0.03));
}

.item.active {
    border-color: var(--nn-color-primary, #1668dc);
    background: var(--nn-color-primary-bg, rgba(22, 104, 220, 0.08));
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

.ctx-item:hover {
    background: var(--nn-color-fill-quaternary, rgba(0, 0, 0, 0.04));
}

.ctx-item.danger {
    color: var(--nn-color-error, #dc4446);
}

.t {
    font-size: 13px;
    font-weight: 500;
}

.m {
    font-size: 11px;
}

.pane-main {
    display: flex;
    flex-direction: column;
    gap: 12px;
    min-height: 0;
    height: 100%;
}

.thread {
    flex: 1;
    min-height: 0;
    overflow-y: auto;
    display: flex;
    flex-direction: column;
    gap: 18px;
    padding-right: 4px;
}

.starters {
    padding: 8px 2px;
}

.turn {
    display: flex;
    flex-direction: column;
    gap: 10px;
}

.bubble {
    border-radius: 8px;
    padding: 12px 16px;
    border: 1px solid var(--nn-color-border, #e5e7eb);
    background: var(--nn-color-bg-container, #fff);
}

.bubble.ask {
    align-self: flex-end;
    max-width: 76%;
    display: flex;
    gap: 10px;
    align-items: baseline;
    background: var(--nn-color-primary-bg, rgba(22, 104, 220, 0.08));
    border-color: var(--nn-color-primary-border, rgba(22, 104, 220, 0.3));
}

.head {
    display: flex;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;
    margin-bottom: 10px;
}

.answer {
    margin: 0;
}

.fp {
    margin-top: 12px;
    padding: 10px 12px;
    border-radius: 5px;
    border: 1px solid var(--nn-color-success-border, rgba(56, 158, 13, 0.35));
    background: var(--nn-color-success-bg, rgba(56, 158, 13, 0.05));
    font-size: 11.5px;
    line-height: 1.9;
    word-break: break-all;
}

.fp .k {
    display: inline-block;
    min-width: 6.2em;
    color: var(--nn-color-text-secondary, #6b7280);
}

.sub {
    margin-top: 12px;
    padding-top: 12px;
    border-top: 1px dashed var(--nn-color-border, #e5e7eb);
}

.trace {
    display: grid;
    grid-template-columns: 92px 1fr;
    gap: 12px;
    font-size: 12.5px;
    padding: 3px 0;
    align-items: baseline;
}

.st {
    color: var(--nn-color-primary, #1668dc);
}

.composer {
    flex: none;
}

.round {
    padding: 8px 0;
    border-bottom: 1px dashed var(--nn-color-border, #e5e7eb);
}

.round-head {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 12.5px;
    margin-bottom: 4px;
    color: var(--nn-color-primary, #1668dc);
}

.round details,
.cap {
    margin: 4px 0;
}

.round summary,
.cap summary {
    cursor: pointer;
    font-size: 12.5px;
    display: flex;
    align-items: center;
    gap: 8px;
}

.caps {
    max-height: 420px;
    overflow-y: auto;
}

.live-tool {
    margin: 6px 0;
    padding: 6px 10px;
    border: 1px solid var(--nn-color-border, #e5e7eb);
    border-radius: 6px;
    background: var(--nn-color-fill-quaternary, rgba(0, 0, 0, 0.02));
}

.live-tool pre {
    max-height: 220px;
    overflow-y: auto;
    margin-top: 6px;
}

.answer.streaming {
    margin-top: 8px;
    border-top: 1px dashed var(--nn-color-border, #e5e7eb);
    padding-top: 8px;
}

.bubble.reply.live {
    border-color: var(--nn-color-primary, #1668dc);
}

.pstep {
    display: grid;
    grid-template-columns: 92px 1fr 70px;
    gap: 12px;
    font-size: 12.5px;
    padding: 3px 0;
    align-items: baseline;
    opacity: 0.6;
}

.pstep.cur {
    opacity: 1;
    font-weight: 500;
}

.pst {
    color: var(--nn-color-primary, #1668dc);
}

.pdt {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.pct {
    text-align: right;
}

@media (max-width: 1080px) {
    .shell {
        grid-template-columns: 1fr;
        height: auto;
    }
}
</style>
