<template>
    <div class="shell">
        <aside class="side">
            <div class="brand">
                <span class="mark">DetOps</span>
                <span class="sub">AI 运维诊断 · 一致性兜底</span>
            </div>
            <NnMenu :items="menuItems" :selected-keys="[route.name]" mode="inline"
                    @click="go" />
            <div class="foot">
                <div class="dim mono" v-if="meta">
                    NORM {{ meta.versions.normalize.split('-')[1] }} ·
                    PROMPT {{ meta.versions.prompt.split('-')[1] }}
                </div>
            </div>
        </aside>

        <div class="main">
            <header class="head">
                <div>
                    <h1>{{ title }}</h1>
                    <span class="dim">{{ hint }}</span>
                </div>
                <NnSpace align="center">
                    <NnTag v-if="meta" :color="meta.kb.enabled ? 'green' : 'orange'">
                        知识库 {{ meta.kb.enabled }} 条命令
                    </NnTag>
                    <NnTag :color="apiReady ? 'green' : 'red'">
                        {{ apiReady ? `${meta.settings.provider} · ${meta.settings.model}` : 'AI 未配置（走兜底）' }}
                    </NnTag>
                    <NnSegmented :value="preset" :options="themes" @change="onTheme" />
                    <NnTooltip title="设置">
                        <NnButton shape="circle" variant="text" aria-label="设置"
                                  @click="settingsOpen = true">
                            <SettingOutlined />
                        </NnButton>
                    </NnTooltip>
                </NnSpace>
            </header>
            <main class="body">
                <RouterView :key="route.name" @meta-changed="loadMeta" />
            </main>
            <SettingsModal v-model:open="settingsOpen" />
        </div>
    </div>
</template>

<script setup>
import { computed, h, onMounted, provide, ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import {
    APP_THEME_PRESET_OPTIONS, getThemeState, setThemePreset,
    SendOutlined, DatabaseOutlined, CloudServerOutlined,
    BellOutlined, ProfileOutlined, SafetyOutlined, SettingOutlined
} from 'netnexus-ui';
import { metaApi } from '../shared/api.js';
import SettingsModal from '../modules/settings/SettingsModal.vue';

const route = useRoute();
const router = useRouter();
const meta = ref(null);
const settingsOpen = ref(false);
const preset = ref(getThemeState().preset);
const themes = APP_THEME_PRESET_OPTIONS.map(o => ({ label: o.label, value: o.value }));

const PAGES = {
    diagnose: ['诊断', '提问 → 采集 → 归一化 → AI 诊断 → 指纹冻结'],
    kb: ['知识库', '导入 Word / Markdown，提取可下发的只读命令清单'],
    devices: ['设备与拓扑', 'SSH / Telnet 接入，一键 LLDP 发现拓扑'],
    syslog: ['Syslog', '服务器监听端口 · 设备上报的日志事件，作为诊断输入'],
    snmp: ['SNMP', 'Trap 服务器监听 · MIB 导入编译与 OID 树 · trap 事件'],
    consistency: ['一致性验证', '赛题判据的直接检验：同一故障必须给出同一答案']
};

const menuItems = [
    { key: 'diagnose', label: '诊断', icon: () => h(SendOutlined) },
    { key: 'kb', label: '知识库', icon: () => h(DatabaseOutlined) },
    { key: 'devices', label: '设备与拓扑', icon: () => h(CloudServerOutlined) },
    { key: 'syslog', label: 'Syslog', icon: () => h(BellOutlined) },
    { key: 'snmp', label: 'SNMP', icon: () => h(ProfileOutlined) },
    { key: 'consistency', label: '一致性验证', icon: () => h(SafetyOutlined) }
];

const title = computed(() => (PAGES[route.name] || ['DetOps'])[0]);
const hint = computed(() => (PAGES[route.name] || ['', ''])[1]);
const apiReady = computed(() => Boolean(meta.value && meta.value.settings.api_key_set));

function go(info) {
    const key = info && info.key ? info.key : info;
    if (key !== route.name) router.push({ name: key });
}

function onTheme(v) {
    preset.value = v;
    setThemePreset(v);
}

async function loadMeta() {
    try {
        meta.value = await metaApi.meta();
    } catch {
        meta.value = null;
    }
}
provide('reloadMeta', loadMeta);
onMounted(loadMeta);
</script>

<style scoped>
.shell {
    display: flex;
    height: 100%;
    background: var(--nn-color-bg-layout, #f5f6f8);
}

.side {
    width: 206px;
    flex: none;
    display: flex;
    flex-direction: column;
    border-right: 1px solid var(--nn-color-border, #e5e7eb);
    background: var(--nn-color-bg-container, #fff);
}

.brand {
    display: flex;
    flex-direction: column;
    gap: 2px;
    padding: 16px 18px 12px;
    border-bottom: 1px solid var(--nn-color-border, #e5e7eb);
}

.mark {
    font-weight: 700;
    letter-spacing: 0.04em;
    color: var(--nn-color-primary, #1668dc);
}

.sub {
    font-size: 11.5px;
    color: var(--nn-color-text-secondary, #6b7280);
}

.foot {
    margin-top: auto;
    padding: 12px 16px;
    border-top: 1px solid var(--nn-color-border, #e5e7eb);
}

.main {
    flex: 1;
    min-width: 0;
    display: flex;
    flex-direction: column;
}

.head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
    flex-wrap: wrap;
    padding: 14px 24px;
    border-bottom: 1px solid var(--nn-color-border, #e5e7eb);
    background: var(--nn-color-bg-container, #fff);
}

.head h1 {
    margin: 0 0 2px;
    font-size: 17px;
    font-weight: 600;
}

.head .dim {
    font-size: 12.5px;
}

.body {
    flex: 1;
    min-height: 0;
    overflow: auto;
    padding: 20px 24px 40px;
}
</style>
