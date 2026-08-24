"""提示词构造 —— 送进 AI 的字节必须是确定的。

这是整个一致性链条的收口：
    同一故障 → 同一归一化快照 → **同一 prompt 字节** → 同一指纹 → 冻结答案命中

所以这里绝不能出现：时间戳、会话历史、随机排序、设备实时状态之外的任何东西。
"""
from __future__ import annotations

from typing import Any, Dict, List

from ...core.canon import canonical_json, sha256_of
from ...core.config import NORMALIZE_VERSION, PROMPT_VERSION

SYSTEM = """你是网络设备故障诊断专家。你会收到一份**证据快照** —— 从设备上采集、
并已擦除易变量（计数器、uptime、老化倒计时等）的 CLI 回显。

你的任务：基于快照定位根因，并区分「根因」与「由根因引起的派生现象」。

硬约束：
1. 只使用快照里出现的事实。**绝不臆造**任何未出现在快照中的表项、接口、地址或状态。
2. 被标记为 <ELIDED:...> 的值是被有意擦除的易变量，不要围绕它们下结论。
3. 区分根因与派生：光模块告警导致链路 down、进而邻居丢失、进而路由消失，
   根因是光模块，后面几项都是派生。
4. 快照里写着「未采到」的命令，说明证据缺失，要在 gaps 里明确列出，
   并相应下调 confidence。
5. 多个根因并存时，按 (OSI 层级从低到高, 严重程度) 排列。
6. 回答要具体：指出是哪台设备、哪个接口/表项、什么状态，不要泛泛而谈。
7. 若快照显示一切正常，root_causes 就留空，不要硬找问题。"""

SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "一句话结论"},
        "root_causes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "device": {"type": "string"},
                    "object": {"type": "string", "description": "接口 / 邻居 / 表项等"},
                    "layer": {"type": "string", "enum": ["L1", "L2", "L3", "L4", "L7"]},
                    "severity": {"type": "string",
                                 "enum": ["critical", "major", "minor"]},
                    "statement": {"type": "string", "description": "根因是什么"},
                    "evidence": {"type": "array", "items": {"type": "string"},
                                 "description": "支撑它的命令回显，如 'LEAF1 display arp'"},
                    "advice": {"type": "string", "description": "怎么处理"},
                },
                "required": ["device", "object", "layer", "severity", "statement",
                             "evidence", "advice"],
                "additionalProperties": False,
            },
        },
        "derived": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "device": {"type": "string"},
                    "object": {"type": "string"},
                    "statement": {"type": "string"},
                    "caused_by": {"type": "string", "description": "由哪个根因引起"},
                },
                "required": ["device", "object", "statement", "caused_by"],
                "additionalProperties": False,
            },
        },
        "gaps": {"type": "array", "items": {"type": "string"},
                 "description": "证据缺口：哪些该看的没看到"},
        "confidence": {"type": "string", "enum": ["确认", "高", "中", "需人工确认"]},
    },
    "required": ["summary", "root_causes", "derived", "gaps", "confidence"],
    "additionalProperties": False,
}


def build(question_norm: str, snapshot: str) -> str:
    """用户段内容。顺序与字节都固定。"""
    return "\n".join([
        "用户问题：", question_norm, "",
        snapshot, "",
        "请基于以上证据快照给出诊断。",
    ])


def fingerprint(question_norm: str, snapshot_hash: str, model_identity: str,
                catalog_digest: str, history_hash: str = "", mode: str = "single",
                events_digest: str = "") -> str:
    """诊断指纹 —— 一致性契约的度量单位。

        SHA256( 归一化提问 ‖ 快照哈希 ‖ 模型身份 ‖ 命令清单
                ‖ 提示词版本 ‖ 归一化版本 ‖ 会话前缀 ‖ 模式 )

    把**会话前缀**并进来，追问才既能带上下文、又保持确定：
    「同一段对话 + 同一个追问 + 同一设备状态」必然得到同一答案。

    **事件摘要**（syslog/trap 去重归一后的哈希）同理：事件集合不变则指纹
    不变；新事件出现 = 设备状态变了 = 理应重诊。

    指纹相同 ⟹ 冻结答案原样返回 ⟹ 正文逐字相同。
    任何一项变了（换模型、改提示词、改归一化规则、知识库变动），指纹自动失效，
    这是有意的：诊断口径变了，旧答案就不该继续用。
    """
    return sha256_of("‖".join([question_norm, snapshot_hash, model_identity,
                               catalog_digest, PROMPT_VERSION, NORMALIZE_VERSION,
                               history_hash, mode, events_digest]))
