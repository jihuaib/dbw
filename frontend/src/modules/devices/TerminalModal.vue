<template>
    <NnModal :open="open" :title="device ? `终端 · ${device.name}（${device.protocol.toUpperCase()} ${device.host}:${device.port}）` : '终端'"
             :footer="null" :width="960" :mask-closable="false"
             @update:open="v => emit('update:open', v)" @cancel="emit('update:open', false)">
        <div class="bar">
            <NnTag :color="state === 'connected' ? 'green' : state === 'connecting' ? 'processing' : 'red'">
                {{ STATE_LABEL[state] }}
            </NnTag>
            <span class="dim" style="font-size: 12px">与诊断采集共用设备表里的接入参数；此处输入直接下发到设备，请谨慎。</span>
            <span style="flex: 1" />
            <NnButton size="small" @click="reconnect">重连</NnButton>
        </div>
        <div ref="host" class="term" />
    </NnModal>
</template>

<script setup>
import { nextTick, onBeforeUnmount, ref, watch } from 'vue';
import { Terminal } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import '@xterm/xterm/css/xterm.css';

const props = defineProps({
    open: { type: Boolean, default: false },
    device: { type: Object, default: null }
});
const emit = defineEmits(['update:open']);

const STATE_LABEL = { idle: '未连接', connecting: '连接中', connected: '已连接', closed: '已断开' };
const host = ref(null);
const state = ref('idle');
let term = null;
let fit = null;
let ws = null;
let onResize = null;

function wsUrl() {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    return `${proto}://${location.host}/api/devices/${props.device.id}/terminal`;
}

function connect() {
    if (!props.device) return;
    disconnect();
    state.value = 'connecting';
    term.clear();
    ws = new WebSocket(wsUrl());
    ws.binaryType = 'arraybuffer';
    ws.onopen = () => {
        state.value = 'connected';
        ws.send(JSON.stringify({ resize: [term.cols, term.rows] }));
        term.focus();
    };
    ws.onmessage = ev => {
        term.write(typeof ev.data === 'string' ? ev.data : new Uint8Array(ev.data));
    };
    ws.onclose = () => { state.value = 'closed'; };
    ws.onerror = () => { state.value = 'closed'; };
}

function disconnect() {
    if (ws) {
        try { ws.close(); } catch { /* noop */ }
        ws = null;
    }
}

function reconnect() {
    connect();
}

function mountTerm() {
    if (term) return;
    term = new Terminal({
        cursorBlink: true, fontSize: 13, convertEol: false,
        fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
        theme: { background: '#0f172a' }
    });
    fit = new FitAddon();
    term.loadAddon(fit);
    term.open(host.value);
    window.__detopsTerm = term;   // 供端到端测试读缓冲区（canvas 渲染无 DOM 文本）
    term.onData(data => {
        if (ws && ws.readyState === WebSocket.OPEN) ws.send(data);
    });
    onResize = () => {
        fit.fit();
        if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ resize: [term.cols, term.rows] }));
    };
    window.addEventListener('resize', onResize);
}

watch(() => props.open, async v => {
    if (v) {
        await nextTick();
        mountTerm();
        await nextTick();
        fit && fit.fit();
        connect();
    } else {
        disconnect();
        state.value = 'idle';
    }
});

onBeforeUnmount(() => {
    disconnect();
    if (onResize) window.removeEventListener('resize', onResize);
    if (term) term.dispose();
});
</script>

<style scoped>
.bar {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 8px;
}

.term {
    height: 460px;
    background: #0f172a;
    border-radius: 6px;
    padding: 6px;
}
</style>
