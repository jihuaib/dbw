<template>
    <div class="stack-md">
        <NnCard>
            <template #title><b>Syslog 服务器</b></template>
            <template #extra>
                <NnButton size="small" type="primary" :loading="saving" @click="apply">应用并重启监听</NnButton>
            </template>
            <div v-if="recv" class="row">
                <NnTag :color="recv.syslog.running ? 'green' : 'red'">
                    {{ recv.syslog.running ? '监听中' : '未运行' }}
                </NnTag>
                <span class="lbl">默认监听端口</span>
                <NnInputNumber v-model:value="port" :min="1" :max="65535" size="small" style="width: 120px" />
                <span class="lbl">当前监听</span>
                <span class="mono dim">UDP {{ (recv.syslog.ports || []).join(' / ') }}</span>
            </div>
            <p class="dim note">
                这里只管服务器该听哪个端口。设备各自的上报目标地址与端口在「设备与拓扑 → 编辑」里配置并下发；
                设备配置了独立端口的会自动一并监听，事件按端口归属到设备。
            </p>
        </NnCard>

        <NnCard>
            <template #title><b>Syslog 事件</b></template>
            <template #extra>
                <NnSpace>
                    <NnTag color="blue">{{ events.length }} 条</NnTag>
                    <NnButton size="small" @click="load">刷新</NnButton>
                </NnSpace>
            </template>
            <NnTable :columns="columns" :data-source="events" row-key="id" size="small" bordered
                     :scroll="{ y: 480 }" :pagination="{ pageSize: 50 }">
                <template #expandedRowRender="{ record }">
                    <pre class="pre-box">{{ record.raw }}</pre>
                </template>
            </NnTable>
            <p class="dim note">
                事件同时是诊断输入：归一化后的事件摘要随提问送给 Agent，摘要哈希参与诊断指纹 ——
                新类型事件出现即触发重诊，反复出现的同类事件不改变输入；CLI 审计类事件不进诊断。
            </p>
        </NnCard>
    </div>
</template>

<script setup>
import { h, onMounted, onUnmounted, ref } from 'vue';
import { NnTag as Tag, notificationService } from 'netnexus-ui';
import { eventsApi } from '../../shared/api.js';

const events = ref([]);
const recv = ref(null);
const port = ref(5514);
const saving = ref(false);
let timer = null;

const SEV_COLOR = { error: 'red', crit: 'red', warning: 'orange', notice: 'blue', info: 'cyan', debug: 'default' };

const columns = [
    { title: '设备', dataIndex: 'device', width: 100,
      customRender: ({ text, record }) => text || record.source_ip },
    { title: '级别', dataIndex: 'severity', width: 90,
      customRender: ({ text }) => (text ? h(Tag, { color: SEV_COLOR[text] || 'default' }, () => text) : '—') },
    { title: '模块/事件', width: 220,
      customRender: ({ record }) => h('span', { class: 'mono' }, `${record.module}/${record.event}`) },
    { title: '内容', dataIndex: 'message', ellipsis: true },
    { title: '时间', dataIndex: 'created_at', width: 165,
      customRender: ({ text }) => h('span', { class: 'mono dim' }, text) }
];

async function load() {
    try {
        events.value = await eventsApi.list('syslog');
        recv.value = await eventsApi.receivers();
        if (recv.value.defaults && !saving.value) port.value = recv.value.defaults.syslog;
    } catch (err) {
        notificationService.error(err.message);
    }
}

async function apply() {
    saving.value = true;
    try {
        await eventsApi.startReceivers({ syslog_port: port.value });
        notificationService.success('Syslog 监听已重启');
        await load();
    } catch (err) {
        notificationService.error(err.message);
    } finally {
        saving.value = false;
    }
}

onMounted(() => { load(); timer = setInterval(load, 4000); });
onUnmounted(() => clearInterval(timer));
</script>

<style scoped>
.row {
    display: flex;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;
}

.lbl {
    font-size: 12.5px;
    color: var(--nn-color-text-secondary, #6b7280);
}

.note {
    font-size: 12px;
    line-height: 1.7;
    margin: 10px 0 0;
}
</style>
