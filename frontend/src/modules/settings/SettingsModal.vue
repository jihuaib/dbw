<template>
    <NnNavigationModal :open="open" v-model:active-key="active" :items="items"
                       title="设置" :width="860" :height="600"
                       @update:open="v => emit('update:open', v)"
                       @cancel="emit('update:open', false)">
        <template #default="{ item }">
            <div v-if="item && item.key === 'model'" class="panel">
                <NnAlert :type="form.api_key_set ? 'success' : 'warning'" show-icon
                         style="margin-bottom: 16px">
                    <template v-if="form.api_key_set">
                        AI 已就绪。一致性由<b>指纹冻结</b>保证：同一故障只真正调用一次模型。
                    </template>
                    <template v-else>
                        还没配置 API Key。系统仍可运行，但会降级到 <b>F4 证据陈述</b>。
                    </template>
                </NnAlert>

                <NnSettingsSection description="模型身份（服务商 + Base URL + 模型 id）参与诊断指纹，换任何一项旧冻结答案自动失效">
                    <NnSettingsItem title="服务商" :description="currentPreset ? currentPreset.hint : ''">
                        <template #actions>
                            <NnSelect v-model:value="form.preset" :options="presetOptions"
                                      style="width: 220px" @change="onPreset" />
                        </template>
                    </NnSettingsItem>
                    <NnSettingsItem title="模型">
                        <template #actions>
                            <NnSelect v-if="modelOptions.length" v-model:value="form.model"
                                      :options="modelOptions" style="width: 220px" />
                            <NnInput v-else v-model:value="form.model"
                                     placeholder="填入模型 id" style="width: 220px" />
                        </template>
                    </NnSettingsItem>
                    <NnSettingsItem title="Base URL" description="OpenAI 兼容端点；Anthropic 官方 SDK 不需要">
                        <template #actions>
                            <NnInput v-model:value="form.base_url" style="width: 320px"
                                     :disabled="form.provider === 'anthropic'" />
                        </template>
                    </NnSettingsItem>
                    <NnSettingsItem title="API Key"
                                    description="存在本地 SQLite，仅本机使用，页面不回显明文">
                        <template #actions>
                            <NnInputPassword v-model:value="apiKey" style="width: 320px"
                                             :placeholder="form.api_key_set
                                                 ? `已配置：${form.api_key_mask}（留空则不修改）`
                                                 : 'sk-...'" />
                        </template>
                    </NnSettingsItem>
                </NnSettingsSection>

                <div class="row-actions">
                    <NnAlert v-if="testResult" :type="testResult.ok ? 'success' : 'error'"
                             show-icon style="flex: 1">
                        {{ testResult.ok
                            ? `连通正常：${testResult.provider} · ${testResult.model}`
                            : testResult.error }}
                    </NnAlert>
                    <NnSpace>
                        <NnButton :loading="testing" @click="test">连通性测试</NnButton>
                        <NnButton type="primary" :loading="saving" @click="save">保存</NnButton>
                    </NnSpace>
                </div>
            </div>

            <div v-else-if="item && item.key === 'policy'" class="panel">
                <NnSettingsSection description="核心机制：一致性不来自「让模型稳定」，而来自「同一输入只调一次模型，答案冻结」">
                    <NnSettingsItem title="思考深度 effort" description="仅 Anthropic 支持；其它服务商忽略">
                        <template #actions>
                            <NnSelect v-model:value="form.effort" :options="effortOptions"
                                      style="width: 160px"
                                      :disabled="form.provider !== 'anthropic'" />
                        </template>
                    </NnSettingsItem>
                    <NnSettingsItem title="自洽投票次数 k（兜底 F3）"
                                    description="首次生成采样 k 次按根因取多数票；k=1 即不投票">
                        <template #actions>
                            <NnSelect v-model:value="form.vote_k" :options="voteOptions"
                                      style="width: 160px" />
                        </template>
                    </NnSettingsItem>
                    <NnSettingsItem title="首答自动冻结"
                                    description="关闭后首答需人工确认才冻结，适合对结论质量要求高的场合">
                        <template #actions>
                            <NnSwitch v-model:checked="form.auto_freeze" />
                        </template>
                    </NnSettingsItem>
                </NnSettingsSection>

                <NnSettingsSection title="兜底阶梯" description="赛题设计要求 1 的交付物">
                    <div class="ladder">
                        <div v-for="l in ladder" :key="l.level" class="rung">
                            <span class="mono lv">{{ l.level }}</span>
                            <span class="nm">{{ l.name }}</span>
                            <span class="dim ds">{{ l.desc }}</span>
                        </div>
                    </div>
                </NnSettingsSection>

                <div class="row-actions">
                    <span />
                    <NnButton type="primary" :loading="saving" @click="save">保存</NnButton>
                </div>
            </div>

            <div v-else-if="item && item.key === 'versions'" class="panel">
                <NnSettingsSection description="任一版本变化都会让相关指纹失效 —— 口径变了必须重诊，这是特性不是缺陷">
                    <NnDescriptions v-if="meta" bordered size="small" :column="1">
                        <NnDescriptionsItem label="归一化">
                            <span class="mono">{{ meta.versions.normalize }}</span>
                        </NnDescriptionsItem>
                        <NnDescriptionsItem label="提示词">
                            <span class="mono">{{ meta.versions.prompt }}</span>
                        </NnDescriptionsItem>
                        <NnDescriptionsItem label="采集编排">
                            <span class="mono">{{ meta.versions.plan }}</span>
                        </NnDescriptionsItem>
                        <NnDescriptionsItem label="命令清单指纹">
                            <span class="mono">{{ meta.kb.catalog_digest }}</span>
                        </NnDescriptionsItem>
                        <NnDescriptionsItem label="模型身份">
                            <span class="mono">{{ meta.llm.provider }} · {{ meta.llm.model }}</span>
                        </NnDescriptionsItem>
                        <NnDescriptionsItem label="模型调用缓存">
                            {{ meta.llm.cached_calls }} 条
                        </NnDescriptionsItem>
                    </NnDescriptions>
                </NnSettingsSection>
            </div>
        </template>
    </NnNavigationModal>
</template>

<script setup>
import { computed, h, inject, reactive, ref, watch } from 'vue';
import {
    notificationService, ApiOutlined, SafetyOutlined, InfoCircleOutlined
} from 'netnexus-ui';
import { metaApi, settingsApi } from '../../shared/api.js';

const props = defineProps({ open: { type: Boolean, default: false } });
const emit = defineEmits(['update:open']);

const reloadMeta = inject('reloadMeta', () => {});
const active = ref('model');
const items = [
    { key: 'model', label: '模型接入', description: '服务商 / 模型 / API Key',
      icon: () => h(ApiOutlined) },
    { key: 'policy', label: '一致性策略', description: '兜底阶梯与冻结参数',
      icon: () => h(SafetyOutlined) },
    { key: 'versions', label: '版本锚', description: '进指纹的口径版本',
      icon: () => h(InfoCircleOutlined) }
];

const apiKey = ref('');
const saving = ref(false);
const testing = ref(false);
const testResult = ref(null);
const meta = ref(null);
const ladder = ref([]);

const form = reactive({
    api_key_set: false, api_key_mask: '', api_key_from_env: false,
    provider: 'anthropic', preset: 'claude', base_url: '',
    model: 'claude-opus-5', effort: 'high', vote_k: 1, auto_freeze: true,
    presets: []
});

const presetOptions = computed(() =>
    (form.presets || []).map(p => ({ label: p.label, value: p.id })));
const currentPreset = computed(() =>
    (form.presets || []).find(p => p.id === form.preset) || null);
const modelOptions = computed(() =>
    (currentPreset.value ? currentPreset.value.models : []).map(m => ({ label: m, value: m })));
const effortOptions = ['low', 'medium', 'high', 'xhigh', 'max']
    .map(v => ({ label: v, value: v }));
const voteOptions = [1, 3, 5].map(v => ({ label: `k = ${v}`, value: v }));

function onPreset(id) {
    const p = (form.presets || []).find(x => x.id === id);
    if (!p) return;
    form.provider = p.provider;
    form.base_url = p.base_url;
    if (p.models.length) form.model = p.models[0];
}

async function load() {
    try {
        Object.assign(form, await settingsApi.read());
        const m = await metaApi.meta();
        meta.value = m;
        ladder.value = m.ladder;
    } catch (err) {
        notificationService.error(err.message);
    }
}

async function save() {
    saving.value = true;
    try {
        const body = {
            provider: form.provider, preset: form.preset, base_url: form.base_url,
            model: form.model, effort: form.effort,
            vote_k: form.vote_k, auto_freeze: form.auto_freeze
        };
        if (apiKey.value.trim()) body.api_key = apiKey.value.trim();
        Object.assign(form, await settingsApi.save(body));
        apiKey.value = '';
        await load();
        reloadMeta();
        notificationService.success('已保存');
    } catch (err) {
        notificationService.error(err.message);
    } finally {
        saving.value = false;
    }
}

async function test() {
    testing.value = true;
    testResult.value = null;
    try {
        testResult.value = await settingsApi.test();
    } catch (err) {
        testResult.value = { ok: false, error: err.message };
    } finally {
        testing.value = false;
    }
}

watch(() => props.open, v => { if (v) load(); });
</script>

<style scoped>
.panel {
    padding: 20px 24px;
    overflow-y: auto;
    height: 100%;
    box-sizing: border-box;
}

.row-actions {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
    margin-top: 16px;
    padding-top: 14px;
    border-top: 1px solid var(--nn-color-border, #e5e7eb);
}

.ladder {
    display: flex;
    flex-direction: column;
    gap: 6px;
}

.rung {
    display: grid;
    grid-template-columns: 40px 92px 1fr;
    gap: 12px;
    align-items: baseline;
    padding: 8px 12px;
    border: 1px solid var(--nn-color-border, #e5e7eb);
    border-radius: 5px;
    font-size: 13px;
}

.lv {
    color: var(--nn-color-primary, #1668dc);
    font-weight: 600;
}

.nm {
    font-weight: 600;
}

.ds {
    font-size: 12.5px;
    line-height: 1.5;
}
</style>
