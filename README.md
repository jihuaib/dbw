# DetOps · AI 运维诊断的一致性兜底

H3C 赛题 6.4.3《AI大模型在运维场景的诊断一致性》。

> 交付物：**一套基于 Agent 运行的运维一致性兜底策略**（赛题设计要求 1）

验证环境是真机：[CNetNexus](https://github.com/jihuaib/CNetNexus) 容器化设备，
Telnet 接入，CLI 手册与拓扑全部来自设备本身。

---

## 核心机制

**诊断由 AI 完成。一致性不来自「让模型稳定」，而来自「同一输入只调一次模型，答案冻结」。**

```
用户提问（+ 会话历史：前几轮的问题与结论摘要）
  ↓ ① 提取信息     正则抽 IP / 接口 / MAC + 文本归一化（确定性）
  ↓ ② Agent 循环   开源方案 LangGraph（create_react_agent）跑主流 Agent 循环：
  │                模型自主决定调工具还是给结论、并行工具调用、逐 token 流式
  │                · 设备命令包装成 run_cli 工具：闭集校验、能力过滤、
  │                  参数不许猜、去重、总量预算 —— 框架管循环，笼子是我们的
  │                · temperature=0 + SQLite 语义键精确缓存：同一段对话
  │                  第二次出现直接回放，模型随机性移出重放路径
  │                · 轮数（recursion_limit）/ 命令总量有硬上限，必然终止
  ↓ ③ 采集         SSH / Telnet 逐台下发；多轮追加进**同一纪元**
  ↓ ④ 归一化       规则擦除（计数器 / uptime / 倒计时 / 列宽）
                   + **实测标定**兜底（多采几次，看哪些 token 真的在变）
                   → 全部轮次拼成一份快照 → snapshot_hash（含拓扑上下文）
  ↓ ⑤ 指纹
     SHA256(归一化提问 ‖ snapshot_hash ‖ 模型身份 ‖ 命令清单
            ‖ 提示词版本 ‖ 归一化版本 ‖ 会话前缀 ‖ 模式)
  ↓ ⑥ 冻结答案命中？
        ├─ 是 → 原样返回，**零模型调用**，字节一致
        └─ 否 → 结构化结论 → 按指纹冻结
```

会话历史进指纹：追问既带上下文，又保持确定 ——
「同一段对话 + 同一个追问 + 同一设备状态」必然得到同一答案。

**全程可观测**：诊断进行中，对话流实时显示每条工具调用（设备 # 命令）、
设备回显全文、逐 token 流出的结论；结束后回复卡片的「模型交互」与
「证据快照 → 设备命令与完整回显」保留完整记录。

---

## 一致性兜底阶梯

| 级 | 名称 | 触发 | 保证 |
|---|---|---|---|
| **F0** | 指纹冻结 | 指纹命中 | 原样返回，零模型调用，**字节一致** |
| **F1** | 快照复用 | 同一快照不同问法 | 事实一致 |
| **F2** | 结构校验 | 输出不合 schema | 固定次数重试，不放行脏输出 |
| **F3** | 自洽投票 | 首次生成 | k 次采样按根因取多数票 |
| **F4** | 模型兜底 | 无 Key / 超时 / 报错 | 降级为「证据陈述」，只列采到什么，不下结论 |
| **F5** | 缺证兜底 | 采集失败 | 列出缺口并下调置信度 |

---

## 真机实测

### 赛题完成度判据（全部实测）

环境：1×SPINE + 3×LEAF 真机容器（CNetNexus），OSPF 三层组网，DeepSeek。
每级判据都按题目要求跑「单会话多次 + 多会话」各 3 轮，轮间真实重新采集。

| 判据 | 故障环境 | 快照数 | 指纹数 | 字节一致 | SSR | 原始回显漂移 |
|---|---|---|---|---|---|---|
| **60%** 单设备确定故障 | LEAF2 GE-1 shutdown | 1 | 1 | 是 | 100% | 有 |
| **80%** 单设备多异常表项 | LEAF2：hello 不匹配 + 2 条坏静态路由表项 | 1 | 1 | 是 | 100% | 6-7 处 |
| **100%** ≥3 台 SPINE-LEAF 跨设备多异常 | LEAF2 shutdown + SPINE1 黑洞静态路由 + LEAF3 hello 不匹配 | 1 | 1 | 是 | 100% | 6 处 |

「原始回显漂移 N 处」是证明链的关键：轮次之间设备回显**确实变了**
（计数器、倒计时、LSA Age），归一化之后快照仍逐字节相同 —— 一致性是兜出来的，
不是因为输入恰好没动。

故障注入（`scripts/lab.sh`）：
```bash
./scripts/lab.sh fault / heal              # 60%：接口 shutdown
./scripts/lab.sh fault-multi / heal-multi  # 80%：单设备三处异常表项
./scripts/lab.sh fault-fabric / heal-fabric# 100%：三台设备各一处异常
```

100% 场景的诊断输出：三处独立异常全部找齐，每处给出层级（配置层/物理层）、
跨设备对照证据（如「SPINE1 侧 GE-2 是 UP 的，物理链路本身正常」）与处置建议；
黑洞静态路由还主动用 `show route ipv4 2.2.2.3 32` 验证了表项确实未生效。

### 一致性判据（赛题核心）

环境：4 台 CNetNexus 容器（1×SPINE + 3×LEAF），OSPF 全网 Full，Telnet 接入，
DeepSeek 作为诊断模型。注入故障 `LEAF2 GE-1 shutdown`。

| | 单会话多次交互 | 多会话交互 |
|---|---|---|
| 轮数 | 4 | 4 |
| **原始回显发生变化** | **10 / 28 条** | **8 / 28 条** |
| 归一化后仍变化 | 0 | 0 |
| SSR 快照稳定率 | **100%** | **100%** |
| 不同诊断指纹数 | **1** | **1** |
| 正文字节一致 | 是 | 是 |
| 兜底级别 | AI → F0 × 3 | F0 × 4 |

第二行是关键：**输入确实在漂移**，快照哈希却纹丝不动。首轮调一次模型，之后全部命中 F0。

### 一次真实诊断

注入故障：`LEAF2` 的上行口 `GE-1` shutdown。提问「LEAF2 好像连不上了，帮我看看什么问题」。

AI 自主挑了 23 条命令 × 4 台设备，27.5 秒给出：

```
根因：
  1. [L1/critical] LEAF2 · GE-1 接口 —— 链路层状态 DOWN，物理链路不通
     证据：LEAF2 · show if: GE-1  public  DOWN  DOWN  10.0.2.2/24
  2. [L3/critical] LEAF2 · BGP 模块 —— 模块未运行（Phase ON-DEMAND，IPC down）

派生现象（由根因引起，不必单独处理）：
  · LEAF2 · LLDP 邻居 —— 未发现任何邻居，因为 GE-1 链路 DOWN
  · LEAF2 · 路由表 —— 只有直连路由，因为 BGP 未运行
```

根因与派生分得清楚，证据都指到具体命令回显。

### 真机暴露的六个问题（模拟器一个都发现不了）

| 问题 | 现象 | 修法 |
|---|---|---|
| **读取过早结束** | 按需模块响应慢时读到空回显，同一命令时而有内容时而为空 | 改为**读到提示符为止**；空回显判为「未采到」走 F5 |
| **命令与回显错位** | `show dev ipc` 的回显里混进了下一条命令的内容 | 发送前**清空残留缓冲区** |
| **LLDP 自环** | 二层网桥把本机 LLDP 帧反射回来，设备成了自己的邻居 | 拓扑构建时过滤自环 |
| **手册说有、设备没有** | 文档写了 OSPF，旧镜像里根本没这个模块，命令回 `Invalid command`，错误文本被当证据喂给模型 | 拒绝识别 + **按设备记录命令能力**，编排层不再提议 |
| **大小写被抹平** | 命令统一转小写，`show if GE-1` 变成 `ge-1` 被设备拒绝 | 保留原始大小写，只用小写做匹配键 |
| **必需参数被当可选剥掉** | `show isis neighbor <tag>` 剥成裸基串，设备回 `Incomplete command`；AI 还会**猜**参数值（猜了个 `default`） | 识别必需参数；参数值**只能取自提问里的实体，绝不猜** |

前两个直接把 SSR 打到 0%。这类问题只有真机会给你。

---

## 支持的模型

页面可配，不改代码：

| 服务商 | 接入方式 | 常用模型 |
|---|---|---|
| **Claude** | 官方 Anthropic SDK，服务端强约束结构化输出 | claude-opus-5 / sonnet-5 |
| **DeepSeek** | OpenAI 兼容端点 | deepseek-chat / deepseek-reasoner |
| **智谱 GLM** | OpenAI 兼容端点 | glm-4.6 / glm-4-plus |
| **通义千问** | DashScope 兼容模式 | qwen-max / qwen-plus |
| **自定义** | 任意 OpenAI 兼容端点 | vLLM / Ollama / 自建网关 |

OpenAI 兼容端点只保证「返回合法 JSON」，不保证符合 schema，所以额外做字段校验，
不合规走 F2 重试。**模型身份（服务商 + Base URL + 模型 id）参与诊断指纹** ——
换任何一项，旧的冻结答案自动失效。

---

## 监控：Syslog 与 SNMP Trap（设备主动上报 → 诊断输入）

CLI 是「问一句答一句」；syslog / trap 是设备**主动喊话** —— 「刚才发生了什么」
这类问题只有它们能回答。设备侧只用两条既有配置命令（满足「设备侧少变动」）：

```
syslog server <host> [port <n>]        # RFC3164 UDP
snmp trap server <host> [port <n>]     # SNMPv2c，只收 trap，不做任何 SNMP 下发
```

**面向真实设备，没有写死的 MIB 或 CLI 清单**：CLI 命令来自导入的手册，
MIB 来自导入的源文件 —— 换厂商就换资料，代码不动。

页面分工：**Syslog 页** / **SNMP 页**只管服务器该监听哪个端口（SNMP 页另有
community 与 MIB）；每台设备的上报目标地址、独立端口、下发命令模板（按厂商
预设生成、可手改）放在**设备配置**里，「下发上报」按钮逐台推送。

**SNMP 页的 MIB 部分**：源文件 = 仓库自带的标准 MIB（`backend/mibs/src`，17 份）+
用户导入（同名覆盖）；「编译全部」用 pysmi 逐模块编译成 JSON 符号表，
依赖按 IMPORTS 自动解析，单个失败不拖累其它；产物是 OID 索引 →
**OID 树浏览**（懒加载、搜索定位；右键节点 → 详情弹窗：类别/语法/访问/描述/通知携带对象）。
trap 报文里只有数字 OID，解码就靠这份索引 —— 最长前缀匹配解回
`IF-MIB::linkDown` 这样的符号名，索引里没有的诚实带出数字尾巴。

接收：pysnmp 多端口收 trap（community 可配）、Python UDP 收 syslog；监听端口 =
页面配置的默认端口 ∪ 各设备配置的端口，事件按端口归属设备。

**事件进诊断**：归一化后的事件摘要随提问送给 Agent，摘要哈希参与诊断指纹。
三条为一致性立的规矩（都被实测逼出来过）：

1. **只记事件类型存在性，不记次数** —— 反复 flap 不改变输入，新类型事件
   出现才触发重诊（那正是「状态变了」的时刻）；
2. **CLI 审计事件排除** —— 采集每下发一条命令都会产生一条 cli/command
   syslog，观测行为自身的回声进了输入，指纹永不稳定；
3. **观测者自照镜子的命令不进证据闭集** —— `show cli history / client` 与
   `show line` 输出的是 CLI 会话状态，必然带着采集自己的痕迹，导入时排除。

**真实设备**：设备侧把 syslog / trap 发到本服务器的 IP（表单默认就是它），
端口都用默认的 5514 / 1162 即可，事件按**源 IP = 设备管理地址**归属；
实验环境/探测/`lab.sh` 那套只在本机存在 `nn-mgmt` 容器网络时才会启用，
真机部署不会触碰 docker。

实验环境（Docker Desktop）的特殊性：NAT 会把事件源 IP 揉成 127.0.0.1，所以
每台设备分配独立接收端口（端口即身份）；上报目标地址由后台从容器网段实测选出。

---

## 设备接入与拓扑

**厂商预设是数据不是代码**：`backend/vendors.json`（`DETOPS_VENDORS` 可指向别的文件），
每条含接入协议/端口、关分屏与 LLDP 命令、syslog / trap 上报命令模板（`{host}` `{port}` 占位）。
接入新厂商往里加一条即可，页面「设备类型」下拉自动出现；第一条为默认选中项。
仓库自带 CNetNexus（实验环境）、H3C Comware、Cisco IOS 三条预设与「手动填写」。

**证据排除命令**也是设置项（设置弹窗 → 一致性策略）：CLI 会话/审计类命令的输出
必然带着采集自身的痕迹，导入手册时排除；按厂商增删，代码里没有写死的命令名。

拓扑图：滚轮缩放、拖动空白平移、拖动设备节点（位置存浏览器本地）、
「适应 / 重排」；右键设备 → **登录连接设备**（网页终端：WebSocket 桥到设备的
telnet 原始套接字或 SSH shell，与诊断采集共用设备表里的接入参数）、
测试连通性、编辑设备。

- **Telnet**（原生 socket 实现，不依赖已移除的 telnetlib）与 **SSH**（paramiko）
- CNetNexus 只实现了 Telnet，SSH 是占位命令 —— 厂商预设已按此填好
- 设备页完整维护：地址、端口、凭据、厂商型号、关分屏命令、LLDP 命令、启停、连通性测试
- **命令能力探测**：把知识库命令在设备上跑一遍，记录支持与否 —— 手册与实机的交叉校验
- **实测标定**：多次采样找出真正易变的 token 位置，作为归一化规则的兜底
- **一键 LLDP 发现拓扑**：逐台跑 LLDP → 解析 → **双向确认才成边**
- LLDP 回显**不写死厂商格式**：优先交给 AI 抽结构化邻居（按回显哈希缓存），
  AI 不可用退到通用正则
- 拓扑拼进证据快照，参与 snapshot_hash —— 拓扑变了，诊断指纹随之变化

单向邻接在拓扑图上画成虚线：那是故障现象，不是解析失败。

---

## 知识库

导入 **Word（.docx）/ Markdown / txt**，提取只读命令清单。四种引擎，默认自动识别：

| 引擎 | 适用 |
|---|---|
| `table` | Markdown 表格式 CLI 文档（CNetNexus `docs/cli/*.md`） |
| `rule` | 散文式文档，正则扫 `display` / `show` 行 |
| `ai` | 让模型通读整篇后给出清单，最全，需 Key |

实测导入 CNetNexus 的 17 份 CLI 文档 → **68 条只读命令**。写命令永不进清单。

---

## 快速开始

```bash
./scripts/start.sh            # 一键起前后台（自动建 venv / npm install）
./scripts/start.sh down       # 停
./scripts/backend.sh  start|stop|restart|status|logs     # 后台 FastAPI :8099
./scripts/frontend.sh start|stop|restart|status|build|logs  # 前台 Vite :5178
./scripts/lab.sh up           # CNetNexus 实验环境（4 台容器，镜像按架构自动下载）
./scripts/lab.sh register     # 把 4 台设备注册到后台（telnet 127.0.0.1:2301~2304 + 上报端口并下发）
./scripts/test.sh [live|e2e]  # 测试
```

**部署到 Linux、从其它机器（如 Windows）访问**：

```bash
./scripts/start.sh prod       # 构建前端 + 后台 0.0.0.0:8099 单端口托管页面与 API
# 浏览器打开 http://<Linux IP>:8099
```

后台默认监听 `0.0.0.0`（`DETOPS_HOST` / `DETOPS_PORT` 可改）；开发模式 `start.sh up`
的 Vite 也对外监听 :5178。设备上报（syslog / trap）目标地址填 Linux 的 IP 即可，
监听端口默认 5514 / 1162（非特权端口，不需要 root）。防火墙放行 8099（或 5178）
与 5514、1162 及各设备配置的端口。

**敏感信息不入库**：API Key、设备凭据、模型调用缓存都在 `backend/data/`（已 gitignore）；
设置备份写到仓库之外的 `~/.detops/settings.bak.json`（可用 `DETOPS_HOME` 改）。
提交前 `git status` 里不应出现任何 `.db` / `settings.bak.json`。


```bash
# 1. 起 CNetNexus 实验环境（4 台容器，配好 IP / OSPF / LLDP / Telnet）
./scripts/lab.sh up          # down 销毁
                             # fault / heal          链路层故障（GE-1 shutdown）
                             # fault-mtu / heal-mtu  协议层故障（hello 间隔不一致）

# 2. 后端
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
cd backend && ../.venv/bin/python -m uvicorn app.main:app --port 8099

# 3. 前端
cd frontend && npm install && npm run dev     # http://localhost:5178
```

界面四步：

1. **设置**（右上角齿轮，弹窗）→ 选服务商、填 Key（不填也能跑，走 F4 兜底）
2. **知识库** → 「导入内置 CLI 手册」（CNetNexus 17 份 → 83 条只读命令）
3. **设备与拓扑**：
   - 添加设备（选「CNetNexus」预设自动填 telnet/23/`terminal length 0`）
   - **探测** —— 手册说有、这台设备未必有，探一遍（约 15 秒/台）
   - **实测标定** —— 多采几次找出真正在变的 token（约 10 分钟，一次性）
   - **一键发现拓扑**
4. **诊断** 提问；**一致性验证** 跑赛题判据
   （会话列表右键可删除会话，独占的采集纪元、回显与冻结答案一并清理；
   被其他会话共享的记录不动）

第 3 步的探测与标定是一次性的，结果存库；换设备或换固件版本再跑一次。

`scripts/lab.sh` 起的 4 台设备对应 telnet `127.0.0.1:2301~2304`。

---

## 目录

```
scripts/lab.sh                CNetNexus 实验环境（起/停/注入故障/恢复）
backend/app/
  core/
    providers.py             多服务商适配（Anthropic SDK / OpenAI 兼容）
    llm.py                   唯一模型出口，按请求内容哈希缓存
    canon.py  db.py  config.py
  modules/
    settings/                服务商、Key、模型、兜底参数（页面可配，带备份恢复）
    kb/importer.py           Word / Markdown 表格 / 正则 / AI 四路提取
    devices/
      transport.py           SSH / Telnet：关分屏、读到提示符、清缓冲、剥回显
      service.py             设备维护 + LLDP 拓扑发现（AI 解析 + 正则兜底 + 自环过滤）
    collect/
      planner.py             AI 挑命令（闭集校验 + 能力过滤 + 规模上限 + 缓存）
      normalize.py           归一化：规则表 + 按表头识别易变列 + **实测标定兜底**
      service.py             采集纪元 + 证据快照 + 漂移比对 + 易变位置标定
    mibs/
      service.py             MIB 源管理 + pysmi 编译 + OID 索引 / 树 / 解码
      router.py              源文件上传、编译、树、搜索、OID 翻译 API
    events/
      service.py             Syslog 服务器 + pysnmp trap 接收 + 事件归一化上下文
      router.py              事件 / 接收器 / 一键上报配置 API
    diagnose/
      prompt.py              字节确定的 prompt + 指纹公式（含会话前缀与模式）
      agent.py               LangGraph ReAct Agent + run_cli 工具（闭集笼子）
                             + 语义键 SQLite 缓存（剥离随机 uuid/元数据，保重放）
      progress.py            进度总线 —— 几十秒的等待要让人看得见
      service.py             六级兜底阶梯 + 冻结答案 + 一致性验证 + 会话历史
  samples/cnetnexus/         CNetNexus CLI 手册（17 份）
  mibs/src/                  标准 MIB 源（17 份，离线携带；厂商 MIB 在页面导入）
frontend/src/modules/
  diagnose/ kb/ devices/ consistency/ settings/
frontend/tests/e2e.mjs       Playwright 端到端（13 步，跑在真机上）
backend/tests/               pytest：单元（离线）+ live 真机集成
.github/workflows/ci.yml     CI：后端单元测试 + 前端构建
scripts/test.sh                 统一测试入口（unit / live / e2e）
```

---

## 测试

与 CI 同一套用例，不写一次性验证脚本：

```bash
./scripts/test.sh          # 单元测试（离线：不碰真机、不调模型，秒级）
./scripts/test.sh live     # + 真机集成（需后端 :8099 + CNetNexus 容器 + API key）
./scripts/test.sh e2e      # + 前端 Playwright 端到端
```

单元测试覆盖一致性机制的每一环：归一化幂等与标定、指纹各分量隔离、
命令闭集校验、回显判定、Agent 循环的终止 / 去重 / 闭集性质（假模型假采集，
测的是循环骨架本身是确定的）。`-m live` 的用例在真机上验证
Agent 问答、F0 冻结命中、prompt 可观测接口。

---

## 设计取舍

**为什么不做结构化解析**　AI 本来就很会读 CLI 原始回显，保留原貌比拆成字段更利于诊断；
不必为每条命令维护解析器 —— **加一条命令的成本是 0**。

**为什么不写规则引擎**　规则引擎每加一类故障就要加一段代码，不可扩展，而且那不是 AI。
判断全部交给模型，我们只保证输入确定。

**为什么擦除而不是删除**　易变量替换成 `<ELIDED:aging>` 而非删行 ——
AI 需要知道「这里有个值，只是被有意擦了」，否则会以为设备没返回该字段。

**列宽也必须归一**　`aging` 从 1180 掉到 999 会让后面少一个空格，
光这一个空格就足以改变快照哈希。所以最后统一把 2+ 空格压成 2 个。

**用测量代替猜测**　逐条写正则去猜哪一列是计数器，永远追不上 ——
换厂商、换命令、换版本，列序就变了。所以规则只是快路径，真正兜底的是**实测标定**：
对每条命令多采几次、token 级比对，**实际在变的位置**才记为易变，然后冻结成 profile。
实测标定在 CNetNexus 上抓出了规则漏掉的三类：`show ospf lsdb` 的 LSA Age、
`show lldp statistics` 的报文计数、`show lldp neighbors detail` 的 TTL。

**空回显绝不当成有效数据**　同一条命令时而有内容时而为空，是最难查的一类不一致。
宁可标记「未采到」交给 F5，也不要把空字符串当结果。

**确定性重试不加 jitter**　常规工程里随机退避是好实践，
这里它会把「采到 / 没采到」变成概率事件，直接破坏一致性。

---

## 当前边界

- 验证环境是 CNetNexus 容器，**没有在物理设备或其它厂商设备上验证过**。
  分页行为、告警插入、格式变体都还没碰过。
- 实测标定按 (行号, token 序号) 记位置。回显行数变了就跳过 profile ——
  那通常意味着内容真的变了，不是抖动；但也意味着行数会变的命令标定不上，
  只能靠规则表覆盖。
- 未配置 Key 时全程走 F4，只输出证据陈述。
- v1.0.0 的发布镜像曾落后于源码（没有 OSPF 模块）。本仓库用的是在当前 HEAD
  上重打 tag 后 GitHub Actions 构建的镜像。

```bash
cd frontend && npm test    # Playwright 端到端（需实验环境与两个服务已启动）
```
