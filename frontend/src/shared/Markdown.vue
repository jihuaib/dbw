<template>
    <div class="md" v-html="html" />
</template>

<script setup>
import { computed } from 'vue';
import { marked } from 'marked';
import DOMPurify from 'dompurify';

const props = defineProps({ source: { type: String, default: '' } });

marked.setOptions({ gfm: true, breaks: true });

// 模型输出不可信：先渲染再净化，去掉任何脚本/事件属性，链接一律新窗口
DOMPurify.addHook('afterSanitizeAttributes', node => {
    if (node.tagName === 'A') {
        node.setAttribute('target', '_blank');
        node.setAttribute('rel', 'noopener noreferrer');
    }
});

const html = computed(() => {
    try {
        return DOMPurify.sanitize(marked.parse(props.source || ''), { USE_PROFILES: { html: true } });
    } catch {
        return DOMPurify.sanitize((props.source || '').replace(/</g, '&lt;'));
    }
});
</script>

<style>
/* 非 scoped：v-html 生成的节点不带 scoped 属性 */
.md {
    font-size: 13.5px;
    line-height: 1.75;
    word-break: break-word;
}

.md > :first-child { margin-top: 0; }
.md > :last-child { margin-bottom: 0; }

.md h1, .md h2, .md h3, .md h4 {
    margin: 14px 0 6px;
    line-height: 1.4;
    font-weight: 600;
}

.md h1 { font-size: 16px; }
.md h2 { font-size: 15px; }
.md h3 { font-size: 14px; }
.md h4 { font-size: 13.5px; }

.md p { margin: 6px 0; }
.md ul, .md ol { margin: 6px 0; padding-left: 22px; }
.md li { margin: 2px 0; }
.md li > p { margin: 0; }

.md code {
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 12.5px;
    padding: 1px 5px;
    border-radius: 4px;
    background: var(--nn-color-fill-quaternary, rgba(0, 0, 0, 0.05));
}

.md pre {
    margin: 8px 0;
    padding: 10px 12px;
    border-radius: 6px;
    overflow-x: auto;
    background: var(--nn-color-fill-quaternary, rgba(0, 0, 0, 0.04));
    border: 1px solid var(--nn-color-border, #e5e7eb);
}

.md pre code {
    padding: 0;
    background: none;
    font-size: 12px;
    line-height: 1.6;
}

.md table {
    border-collapse: collapse;
    margin: 8px 0;
    font-size: 12.5px;
    display: block;
    max-width: 100%;
    overflow-x: auto;
}

.md th, .md td {
    border: 1px solid var(--nn-color-border, #e5e7eb);
    padding: 4px 10px;
    text-align: left;
    vertical-align: top;
}

.md th {
    font-weight: 600;
    background: var(--nn-color-fill-quaternary, rgba(0, 0, 0, 0.03));
}

.md blockquote {
    margin: 8px 0;
    padding: 4px 12px;
    border-left: 3px solid var(--nn-color-primary, #1668dc);
    color: var(--nn-color-text-secondary, #6b7280);
    background: var(--nn-color-primary-bg, rgba(22, 104, 220, 0.05));
}

.md hr {
    border: 0;
    border-top: 1px dashed var(--nn-color-border, #e5e7eb);
    margin: 12px 0;
}

.md strong { font-weight: 600; }
</style>
