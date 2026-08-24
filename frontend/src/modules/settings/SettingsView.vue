<template>
    <div class="stack-md">
        <NnAlert :type="form.api_key_set ? 'success' : 'warning'" show-icon>
            <template v-if="form.api_key_set">
                AI 已就绪。诊断由模型完成，一致性由<b>指纹冻结</b>保证：
                同一故障只会真正调用一次模型，之后永远命中冻结答案。
            </template>
            <template v-else>
                还没配置 API Key。系统仍可运行，但会降级到 <b>F4 证据陈述</b> ——
                只列采到了什么，不给根因判断。填入 Key 后即可看到完整效果。
            </template>
        </NnAlert>

        <NnCard>
            <template #title><b>模型接入</b></template>
            <template #extra>
                <NnSpace>
                    <NnButton size="small" :loading="testing" @click="test">连通性测试</NnButton>
                    <NnButton size="small" type="primary" :loading="saving" @click="save">
                        保存
                    </NnButton>
                </NnSpace>
            </template>

            <NnForm layout="vertical" :model="form">
                <NnRow :gutter="18">
                    <NnCol :span="8">
                        <NnFormItem label="服务商">
                            <NnSelect v-model:value="form.preset" :options="presetOptions"
                                      @change="onPreset" />
                            <div class="dim" style="font-size: 12px; margin-top: 6px">
                                {{ currentPreset ? currentPreset.hint : '' }}
                            </div>
                        </NnFormItem>
                    </NnCol>
                    <NnCol :span="8">
                        <NnFormItem label="模型">
                            <NnSelect v-model:value="form.model" :options="modelOptions"
                                      allow-clear />
                            <NnInput v-if="!modelOptions.length" v-model:value="form.model"
                                     placeholder="填入模型 id" style="margin-top: 6px" />
                        </NnFormItem>
                    </NnCol>
                    <NnCol :span="8">
                        <NnFormItem label="Base URL">
                            <NnInput v-model:value="form.base_url"
                                     :disabled="form.provider === 'anthropic'"
                                     placeholder="OpenAI 兼容端点" />
                        </NnFormItem>
                    </NnCol>
                </NnRow>

                <NnRow :gutter="18">
                    <NnCol :span="24">
                        <NnFormItem label="API Key">
                            <NnInputPassword v-model:value="apiKey"
                                             :placeholder="form.api_key_set
                                                 ? `已配置：${form.api_key_mask}（留空则不修改）`
                                                 : 'sk-ant-...'" />
                            <div class="dim" style="font-size: 12px; margin-top: 6px">
                                存在本地 SQLite，仅本机使用，页面不回显明文。
                                <b>模型身份（服务商 + Base URL + 模型 id）参与诊断指纹</b> ——
                                换任何一项，旧的冻结答案自动失效，因为诊断口径变了。
                                <template v-if="form.api_key_from_env">
                                    当前值来自环境变量 ANTHROPIC_API_KEY。
                                </template>
                            </div>
                        </NnFormItem>
                    </NnCol>
                </NnRow>

                <NnRow :gutter="18">
                    <NnCol :span="8">
                        <NnFormItem label="思考深度 effort">
                            <NnSelect v-model:value="form.effort" :options="effortOptions"
                                      :disabled="form.provider !== 'anthropic'" />
                            <div class="dim" style="font-size: 12px; margin-top: 6px">
                                仅 Anthropic 支持；其它服务商忽略此项。
                            </div>
                        </NnFormItem>
                    </NnCol>
                    <NnCol :span="8">
                        <NnFormItem label="自洽投票次数 k（兜底 F3）">
                            <NnSelect v-model:value="form.vote_k" :options="voteOptions" />
                            <div class="dim" style="font-size: 12px; margin-top: 6px">
                                首次生成时采样 k 次，按根因取多数票。k=1 即不投票。
                            </div>
                        </NnFormItem>
                    </NnCol>
                    <NnCol :span="8">
                        <NnFormItem label="首答自动冻结">
                            <NnSwitch v-model:checked="form.auto_freeze" />
                            <div class="dim" style="font-size: 12px; margin-top: 6px">
                                关闭后首答需人工确认才冻结，适合对结论质量要求高的场合。
                            </div>
                        </NnFormItem>
                    </NnCol>
                </NnRow>
            </NnForm>

            <NnAlert v-if="testResult" :type="testResult.ok ? 'success' : 'error'"
                     show-icon style="margin-top: 10px">
                {{ testResult.ok
                    ? `连通正常：${testResult.provider} · ${testResult.model}`
                    : testResult.error }}
            </NnAlert>
        </NnCard>

        <NnCard>
            <template #title><b>一致性兜底阶梯</b></template>
            <template #extra><span class="dim">赛题设计要求 1 的交付物</span></template>
            <div class="ladder">
                <div v-for="l in ladder" :key="l.level" class="rung">
                    <span class="mono lv">{{ l.level }}</span>
                    <span class="nm">{{ l.name }}</span>
                    <span class="dim ds">{{ l.desc }}</span>
                </div>
            </div>
            <p class="dim" style="font-size: 12.5px; margin: 14px 0 0">
                核心机制：<b>一致性不来自「让模型稳定」，而来自「同一输入只调一次模型，答案冻结」</b>。
                指纹 = SHA256(归一化提问 ‖ 快照哈希 ‖ 模型 ‖ 命令清单 ‖ 提示词版本 ‖ 归一化版本)。
            </p>
        </NnCard>

        <NnCard>
            <template #title><b>版本锚</b></template>
            <NnDescriptions bordered size="small" v-if="meta">
                <NnDescriptionsItem label="归一化">
                    <span class="mono">{{ meta.versions.normalize }}</span>
                </NnDescriptionsItem>
                <NnDescriptionsItem label="提示词">
                    <span class="mono">{{ meta.versions.prompt }}</span>
                </NnDescriptionsItem>
                <NnDescriptionsItem label="采集编排">
                    <span class="mono">{{ meta.versions.plan }}</span>
                </NnDescriptionsItem>
                <NnDescriptionsItem label="命令清单指纹" :span="3">
                    <span class="mono">{{ meta.kb.catalog_digest }}</span>
                </NnDescriptionsItem>
                <NnDescriptionsItem label="模型身份">
                    <span class="mono">
                        {{ meta.llm.provider }} · {{ meta.llm.model }}
                    </span>
                </NnDescriptionsItem>
                <NnDescriptionsItem label="模型调用缓存">
                    {{ meta.llm.cached_calls }} 条
                </NnDescriptionsItem>
            </NnDescriptions>
        </NnCard>
    </div>
</template>

<script setup>
import { computed, inject, onMounted, reactive, ref } from 'vue';
import { notificationService } from 'netnexus-ui';
import { metaApi, settingsApi } from '../../shared/api.js';

const reloadMeta = inject('reloadMeta', () => {});
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

function onPreset(id) {
    const p = (form.presets || []).find(x => x.id === id);
    if (!p) return;
    form.provider = p.provider;
    form.base_url = p.base_url;
    if (p.models.length) form.model = p.models[0];
}
const effortOptions = ['low', 'medium', 'high', 'xhigh', 'max']
    .map(v => ({ label: v, value: v }));
const voteOptions = [1, 3, 5].map(v => ({ label: `k = ${v}`, value: v }));

async function load() {
    Object.assign(form, await settingsApi.read());
    const m = await metaApi.meta();
    meta.value = m;
    ladder.value = m.ladder;
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

onMounted(load);
</script>

<style scoped>
.ladder {
    display: flex;
    flex-direction: column;
    gap: 6px;
}

.rung {
    display: grid;
    grid-template-columns: 46px 96px 1fr;
    gap: 14px;
    align-items: baseline;
    padding: 9px 12px;
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
