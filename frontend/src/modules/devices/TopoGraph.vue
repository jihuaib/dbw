<template>
    <div class="topo" @contextmenu.prevent>
        <div v-if="nodes.length" class="toolbar">
            <NnSpace>
                <NnButton size="small" @click="zoomBy(1.2)">放大</NnButton>
                <NnButton size="small" @click="zoomBy(1 / 1.2)">缩小</NnButton>
                <NnButton size="small" @click="fitView">适应</NnButton>
                <NnButton size="small" @click="resetLayout">重排</NnButton>
            </NnSpace>
            <span class="dim hint">滚轮缩放 · 拖动空白平移 · 拖动设备移动 · 右键设备操作 · 缩放 {{ Math.round(view.k * 100) }}%</span>
        </div>

        <svg v-if="nodes.length" ref="svgRef" class="canvas" role="img" aria-label="LLDP 发现的网络拓扑"
             @wheel.prevent="onWheel" @pointerdown="onCanvasDown" @pointermove="onMove"
             @pointerup="onUp" @pointerleave="onUp">
            <g :transform="`translate(${view.x} ${view.y}) scale(${view.k})`">
                <g>
                    <line v-for="(e, i) in edges" :key="'e' + i"
                          :x1="P(e.a).x" :y1="P(e.a).y" :x2="P(e.b).x" :y2="P(e.b).y"
                          :class="['link', e.confirmed ? 'ok' : 'half']" />
                    <text v-for="(e, i) in edges" :key="'la' + i"
                          :x="lerp(P(e.a), P(e.b), 0.26).x" :y="lerp(P(e.a), P(e.b), 0.26).y - 3"
                          class="port" text-anchor="middle">{{ e.a_port }}</text>
                    <text v-for="(e, i) in edges" :key="'lb' + i"
                          :x="lerp(P(e.a), P(e.b), 0.74).x" :y="lerp(P(e.a), P(e.b), 0.74).y - 3"
                          class="port" text-anchor="middle">{{ e.b_port }}</text>
                </g>
                <g v-for="n in nodes" :key="n.name" class="device"
                   :transform="`translate(${P(n.name).x} ${P(n.name).y})`"
                   @pointerdown.stop="onNodeDown($event, n)"
                   @contextmenu.prevent.stop="emit('node-menu', { event: $event, node: n })">
                    <rect x="-52" y="-20" width="104" height="40" rx="6"
                          :class="['node', `role-${n.role}`, n.known ? '' : 'unknown', dragging === n.name ? 'drag' : '']" />
                    <text y="-3" class="name" text-anchor="middle">{{ n.name }}</text>
                    <text y="11" class="role" text-anchor="middle">{{ n.role }}</text>
                </g>
            </g>
        </svg>
        <NnEmpty v-else description="还没有拓扑。先添加设备，再点「一键发现拓扑」。" />

        <div v-if="nodes.length" class="legend dim">
            <span><i class="sw ok"></i>双向确认</span>
            <span><i class="sw half"></i>单向邻接（对端看不到本端，本身就是故障现象）</span>
            <span><i class="sw box-unknown"></i>LLDP 发现但清单中无此设备</span>
        </div>
    </div>
</template>

<script setup>
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue';

const props = defineProps({
    nodes: { type: Array, default: () => [] },
    edges: { type: Array, default: () => [] }
});
const emit = defineEmits(['node-menu']);

const ROW = { SPINE: 0, CORE: 0, BORDER: 1, LEAF: 1, ACCESS: 2, OTHER: 2, UNKNOWN: 3 };
const W = 900;
const STORE_KEY = 'detops.topo.positions';

const svgRef = ref(null);
const view = reactive({ x: 0, y: 0, k: 1 });
const manual = reactive({});          // 手动拖过的节点位置（持久化）
const dragging = ref(null);
let drag = null;                       // { kind: 'node'|'pan', ... }

function loadManual() {
    try {
        const saved = JSON.parse(localStorage.getItem(STORE_KEY) || '{}');
        Object.assign(manual, saved);
    } catch { /* 存储不可用就只在内存里拖 */ }
}

function saveManual() {
    try { localStorage.setItem(STORE_KEY, JSON.stringify(manual)); } catch { /* noop */ }
}

/** 自动布局：按角色分层，SPINE/CORE 在上，LEAF/BORDER 居中，接入与未知在下。 */
const autoPos = computed(() => {
    const rows = {};
    props.nodes.forEach(n => {
        const r = ROW[n.role] ?? 2;
        (rows[r] = rows[r] || []).push(n);
    });
    const keys = Object.keys(rows).map(Number).sort((a, b) => a - b);
    const pos = {};
    keys.forEach((r, ri) => {
        const list = rows[r];
        const y = 60 + ri * 120;
        list.forEach((n, i) => { pos[n.name] = { x: (W / (list.length + 1)) * (i + 1), y }; });
    });
    return pos;
});

const P = name => manual[name] || autoPos.value[name] || { x: 0, y: 0 };
const lerp = (a, b, t) => ({ x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t });

function clientToWorld(ev) {
    const r = svgRef.value.getBoundingClientRect();
    return { x: (ev.clientX - r.left - view.x) / view.k, y: (ev.clientY - r.top - view.y) / view.k };
}

function zoomAt(factor, cx, cy) {
    const k = Math.min(4, Math.max(0.25, view.k * factor));
    // 以指针为中心缩放：保持指针下的世界坐标不动
    view.x = cx - (cx - view.x) * (k / view.k);
    view.y = cy - (cy - view.y) * (k / view.k);
    view.k = k;
}

function zoomBy(factor) {
    const r = svgRef.value ? svgRef.value.getBoundingClientRect() : { width: W, height: 400 };
    zoomAt(factor, r.width / 2, r.height / 2);
}

function onWheel(ev) {
    const r = svgRef.value.getBoundingClientRect();
    zoomAt(ev.deltaY < 0 ? 1.1 : 1 / 1.1, ev.clientX - r.left, ev.clientY - r.top);
}

function fitView() {
    if (!svgRef.value || !props.nodes.length) return;
    const pts = props.nodes.map(n => P(n.name));
    const minX = Math.min(...pts.map(p => p.x)) - 70, maxX = Math.max(...pts.map(p => p.x)) + 70;
    const minY = Math.min(...pts.map(p => p.y)) - 40, maxY = Math.max(...pts.map(p => p.y)) + 40;
    const r = svgRef.value.getBoundingClientRect();
    const k = Math.min(4, Math.max(0.25, Math.min(r.width / (maxX - minX), r.height / (maxY - minY))));
    view.k = k;
    view.x = (r.width - (maxX - minX) * k) / 2 - minX * k;
    view.y = (r.height - (maxY - minY) * k) / 2 - minY * k;
}

function resetLayout() {
    Object.keys(manual).forEach(k => delete manual[k]);
    saveManual();
    nextTick(fitView);
}

function onNodeDown(ev, n) {
    if (ev.button !== 0) return;
    const w = clientToWorld(ev);
    const p = P(n.name);
    drag = { kind: 'node', name: n.name, dx: p.x - w.x, dy: p.y - w.y };
    dragging.value = n.name;
    ev.currentTarget.ownerSVGElement.setPointerCapture(ev.pointerId);
}

function onCanvasDown(ev) {
    if (ev.button !== 0) return;
    drag = { kind: 'pan', sx: ev.clientX - view.x, sy: ev.clientY - view.y };
    svgRef.value.setPointerCapture(ev.pointerId);
}

function onMove(ev) {
    if (!drag) return;
    if (drag.kind === 'node') {
        const w = clientToWorld(ev);
        manual[drag.name] = { x: w.x + drag.dx, y: w.y + drag.dy };
    } else {
        view.x = ev.clientX - drag.sx;
        view.y = ev.clientY - drag.sy;
    }
}

function onUp() {
    if (drag && drag.kind === 'node') saveManual();
    drag = null;
    dragging.value = null;
}

onMounted(() => {
    loadManual();
    nextTick(fitView);
});
watch(() => props.nodes.length, () => nextTick(fitView));

defineExpose({ fitView });
</script>

<style scoped>
.topo {
    border: 1px solid var(--nn-color-border, #e5e7eb);
    border-radius: 6px;
    padding: 10px 12px 12px;
    background: var(--nn-color-bg-container, #fff);
}

.toolbar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    flex-wrap: wrap;
    margin-bottom: 8px;
}

.hint {
    font-size: 12px;
}

.canvas {
    display: block;
    width: 100%;
    height: 420px;
    border-radius: 4px;
    background:
        linear-gradient(var(--nn-color-fill-quaternary, rgba(0, 0, 0, 0.03)) 1px, transparent 1px) 0 0 / 24px 24px,
        linear-gradient(90deg, var(--nn-color-fill-quaternary, rgba(0, 0, 0, 0.03)) 1px, transparent 1px) 0 0 / 24px 24px;
    cursor: grab;
    touch-action: none;
    user-select: none;
}

.canvas:active {
    cursor: grabbing;
}

.device {
    cursor: pointer;
}

.link {
    stroke-width: 1.6;
}

.link.ok {
    stroke: var(--nn-color-primary, #1668dc);
    opacity: 0.55;
}

.link.half {
    stroke: var(--nn-color-warning, #d48806);
    stroke-dasharray: 5 4;
}

.node {
    fill: var(--nn-color-bg-container, #fff);
    stroke: var(--nn-color-border, #e5e7eb);
    stroke-width: 1.2;
    filter: drop-shadow(0 1px 1px rgba(0, 0, 0, 0.08));
}

.node.role-SPINE,
.node.role-CORE {
    fill: var(--nn-color-primary-bg, rgba(22, 104, 220, 0.1));
    stroke: var(--nn-color-primary, #1668dc);
}

.node.unknown {
    stroke-dasharray: 4 3;
    stroke: var(--nn-color-warning, #d48806);
}

.node.drag {
    stroke-width: 2;
    stroke: var(--nn-color-primary, #1668dc);
}

.name {
    font-size: 12px;
    font-weight: 600;
    fill: var(--nn-color-text, #1f2329);
    pointer-events: none;
}

.role {
    font-size: 9.5px;
    fill: var(--nn-color-text-secondary, #6b7280);
    pointer-events: none;
}

.port {
    font-size: 8.5px;
    fill: var(--nn-color-text-secondary, #6b7280);
}

.legend {
    display: flex;
    gap: 20px;
    flex-wrap: wrap;
    font-size: 12px;
    margin-top: 10px;
    padding-top: 10px;
    border-top: 1px solid var(--nn-color-border, #e5e7eb);
}

.legend i {
    display: inline-block;
    width: 20px;
    height: 0;
    border-top: 2px solid;
    vertical-align: middle;
    margin-right: 6px;
}

.legend .ok { border-color: var(--nn-color-primary, #1668dc); }
.legend .half { border-top-style: dashed; border-color: var(--nn-color-warning, #d48806); }
.legend .box-unknown { width: 14px; height: 10px; border: 1px dashed var(--nn-color-warning, #d48806); }
</style>
