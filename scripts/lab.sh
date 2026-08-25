#!/bin/bash
# CNetNexus 实验环境：起 4 台真机（1×SPINE + 3×LEAF），配好 IP / LLDP / Telnet
#   ./scripts/lab.sh up      拉起并配置（镜像按架构自动从 GitHub Release 下载）
#   ./scripts/lab.sh down    销毁
#   ./scripts/lab.sh fault      注入故障（LEAF2 上行口 shutdown）
#   ./scripts/lab.sh heal       恢复
#   ./scripts/lab.sh fault-mtu  注入协议层故障（LEAF3 hello 间隔不一致）
#   ./scripts/lab.sh heal-mtu   恢复
#   ./scripts/lab.sh fault-multi   单设备多异常表项（80% 判据环境，LEAF2 三处异常）
#   ./scripts/lab.sh heal-multi    恢复
#   ./scripts/lab.sh fault-fabric  跨设备多异常表项（100% 判据环境，三台设备各一处）
#   ./scripts/lab.sh heal-fabric   恢复
#   ./scripts/lab.sh register [后台地址]   把这 4 台设备注册到后台（含上报端口并下发），默认 http://127.0.0.1:8099
#   ./scripts/lab.sh check-report [后台地址]  自检 syslog/trap 上报链路：设备配置 → 容器到宿主 UDP → 后台入库
set -e
# 镜像按本机架构自动选择；本地没有就从 CNetNexus 的 GitHub Release 下载并 docker load。
NN_VERSION="${NN_VERSION:-1.0.0}"
case "$(uname -m)" in
  x86_64|amd64) NN_ARCH=amd64 ;;
  aarch64|arm64) NN_ARCH=arm64 ;;
  *) echo "不支持的架构: $(uname -m)"; exit 1 ;;
esac
IMG="${NN_IMAGE:-netnexus:${NN_VERSION}-${NN_ARCH}}"
DEVS="nn-spine1 nn-leaf1 nn-leaf2 nn-leaf3"

ensure_image() {
  # 1) 指定/默认标签本地已有 → 直接用
  if docker image inspect "$IMG" >/dev/null 2>&1; then
    echo "使用本地镜像 $IMG"; return
  fi
  # 2) 本地已有别的 netnexus 镜像（自己 load/tag 过的）→ 优先用同架构的那一个，不下载
  local found
  found="$(docker images --format '{{.Repository}}:{{.Tag}}' 2>/dev/null | grep -i 'netnexus' | grep -v '<none>' | grep -i -- "$NN_ARCH" | head -1)"
  [ -n "$found" ] || found="$(docker images --format '{{.Repository}}:{{.Tag}}' 2>/dev/null | grep -i 'netnexus' | grep -v '<none>' | head -1)"
  if [ -n "$found" ]; then
    echo "本地已有镜像 $found，直接使用（想强制用别的：NN_IMAGE=<标签>）"; IMG="$found"; return
  fi
  # 3) 真没有 → 从 GitHub Release 下载并 load（缓存到 ~/.detops，只下一次）
  local tar="netnexus-${NN_VERSION}-docker-${NN_ARCH}.tar.gz"
  local url="https://github.com/jihuaib/CNetNexus/releases/download/v${NN_VERSION}/${tar}"
  local cache="${NN_CACHE:-$HOME/.detops}"; mkdir -p "$cache"
  if [ ! -f "$cache/$tar" ]; then
    echo "本地没有镜像 $IMG，从 GitHub Release 下载（约 150MB）…"
    curl -fL --progress-bar -o "$cache/$tar.part" "$url" && mv "$cache/$tar.part" "$cache/$tar" \
      || { echo "下载失败: $url"; exit 1; }
  fi
  echo "docker load $cache/$tar …"
  docker load -i "$cache/$tar" >/dev/null
  docker image inspect "$IMG" >/dev/null 2>&1 || {
    echo "镜像包里的标签不是 $IMG，实际为："; docker images --format '{{.Repository}}:{{.Tag}}' | grep -i netnexus
    echo "可用 NN_IMAGE=<标签> ./scripts/lab.sh up 指定"; exit 1; }
}

if [ "$(id -u)" = "0" ] && [ -n "$SUDO_USER" ]; then
  echo "提示：用 sudo 运行会切到 root 的 docker 环境与 HOME，可能看不到你已有的镜像/缓存；建议把用户加入 docker 组后直接运行。"
fi

console() { local c="$1"; shift
  { printf '%s\n' "$@"; printf 'exit\n'; } | docker exec -i \
    -e NN_CONSOLE_SOCK=/opt/netnexus/run/console.sock "$c" \
    /opt/netnexus/bin/netnexus-console >/dev/null 2>&1 || true; }

case "${1:-up}" in
up)
  ensure_image
  docker rm -f $DEVS >/dev/null 2>&1 || true
  docker network create nn-mgmt >/dev/null 2>&1 || true
  for i in 1 2 3; do docker network create nn-s1l$i >/dev/null 2>&1 || true; done

  docker run -d -i --name nn-spine1 --network nn-mgmt --hostname SPINE1 \
    --cap-add NET_ADMIN --cap-add NET_RAW --security-opt seccomp=unconfined \
    -p 2301:23 "$IMG" >/dev/null
  for i in 1 2 3; do
    docker run -d -i --name nn-leaf$i --network nn-mgmt --hostname LEAF$i \
      --cap-add NET_ADMIN --cap-add NET_RAW --security-opt seccomp=unconfined \
      -p 230$((i+1)):23 "$IMG" >/dev/null
    docker network connect nn-s1l$i nn-spine1
    docker network connect nn-s1l$i nn-leaf$i
  done

  # Linux 网桥默认过滤 LLDP 的 01:80:c2:00:00:0e，必须放开否则邻居发现不到
  docker run --rm --privileged --net=host alpine sh -c \
    'for b in $(ls /sys/class/net | grep "^br-"); do
       echo 16384 > /sys/class/net/$b/bridge/group_fwd_mask 2>/dev/null; done' >/dev/null 2>&1

  echo "等待设备就绪…"; sleep 15
  # SPINE1 GE-1/2/3 → LEAF1/2/3；LEAFn GE-1 → SPINE1
  console nn-spine1 config \
    "if GE-1" "ip address 10.0.1.1 24" "no shutdown" "lldp enable" "lldp admin-status txrx" exit \
    "if GE-2" "ip address 10.0.2.1 24" "no shutdown" "lldp enable" "lldp admin-status txrx" exit \
    "if GE-3" "ip address 10.0.3.1 24" "no shutdown" "lldp enable" "lldp admin-status txrx" exit \
    "if loop 1" "ip address 1.1.1.1 32" exit \
    lldp "lldp timer 5" "telnet server enable" "line vty 0 4" "transport input telnet" exit end
  for i in 1 2 3; do
    console nn-leaf$i config \
      "if GE-1" "ip address 10.0.$i.2 24" "no shutdown" "lldp enable" "lldp admin-status txrx" exit \
      "if loop 1" "ip address 2.2.2.$i 32" exit \
      lldp "lldp timer 5" "telnet server enable" "line vty 0 4" "transport input telnet" exit end
  done
  # OSPF：p2p 网络类型（两两互联的链路上 broadcast 会卡在 2-Way）
  console nn-spine1 config "ospf 1" "router-id 1.1.1.1" "area 0" exit \
    "if GE-1" "ospf enable 1 area 0" "ospf network-type 1 point-to-point" exit \
    "if GE-2" "ospf enable 1 area 0" "ospf network-type 1 point-to-point" exit \
    "if GE-3" "ospf enable 1 area 0" "ospf network-type 1 point-to-point" exit end
  for i in 1 2 3; do
    console nn-leaf$i config "ospf 1" "router-id 2.2.2.$i" "area 0" exit \
      "if GE-1" "ospf enable 1 area 0" "ospf network-type 1 point-to-point" exit end
  done

  # LLDP 模块是按需拉起的，首次配置会失败，起来后再配一遍
  sleep 3
  console nn-spine1 config lldp "lldp timer 5" \
    "if GE-1" "lldp enable" "lldp admin-status txrx" exit \
    "if GE-2" "lldp enable" "lldp admin-status txrx" exit \
    "if GE-3" "lldp enable" "lldp admin-status txrx" exit end
  for i in 1 2 3; do
    console nn-leaf$i config lldp "lldp timer 5" \
      "if GE-1" "lldp enable" "lldp admin-status txrx" exit end
  done
  echo "等待 LLDP / OSPF 收敛…"; sleep 30
  docker ps --format '  {{.Names}}\t{{.Status}}\t{{.Ports}}' | grep nn-
  echo "telnet: SPINE1=2301 LEAF1=2302 LEAF2=2303 LEAF3=2304"
  echo
  echo "下一步（在界面或 API 上做一次，之后不用重复）："
  echo "  1) 设备与拓扑 → 探测          手册说有、这台设备未必有，探一遍"
  echo "  2) 设备与拓扑 → 实测标定       多采几次找出真正在变的 token（约 10 分钟）"
  echo "  3) 设备与拓扑 → 一键发现拓扑"
  ;;
down)
  docker rm -f $DEVS >/dev/null 2>&1 || true
  for n in nn-mgmt nn-s1l1 nn-s1l2 nn-s1l3; do docker network rm $n >/dev/null 2>&1 || true; done
  echo "已销毁"
  ;;
fault)
  console nn-leaf2 config "if GE-1" shutdown end
  echo "已注入：LEAF2 GE-1 shutdown（连锁 LLDP 邻居丢失 + OSPF 邻居断）"
  ;;
fault-mtu)
  # 物理层完好、OSPF 卡在 ExStart —— 与链路 down 形成对照
  console nn-leaf3 config "if GE-1" "ospf hello-interval 1 3" end
  echo "已注入：LEAF3 GE-1 hello 间隔改为 3s（与对端不一致，邻居会断）"
  ;;
heal)
  console nn-leaf2 config "if GE-1" "no shutdown" end
  echo "已恢复：LEAF2 GE-1 up"
  ;;
heal-mtu)
  console nn-leaf3 config "if GE-1" "no ospf hello-interval 1" end
  echo "已恢复：LEAF3 GE-1 hello 间隔"
  ;;
fault-multi)
  # 80% 判据：单设备（LEAF2）多异常表项 —— 接口保持 UP，三处异常相互独立：
  #   ① OSPF hello 间隔与对端不一致 → 邻居断 → 路由表缺 OSPF 表项（异常性缺失）
  #   ② 指向不可达下一跳的静态路由（错误表项，且因静态优先级高会盖掉正确路径）
  #   ③ 到 SPINE1 loopback 的错误静态路由（第二条错误表项）
  console nn-leaf2 config "if GE-1" "ospf hello-interval 1 3" exit     "route static ipv4 10.0.3.0 24 10.0.2.99"     "route static ipv4 1.1.1.1 32 10.0.2.99" end
  echo "已注入（LEAF2 多异常表项）："
  echo "  ① GE-1 hello 间隔 3s（与对端不一致 → OSPF 邻居断）"
  echo "  ② 静态路由 10.0.3.0/24 → 10.0.2.99（下一跳不可达）"
  echo "  ③ 静态路由 1.1.1.1/32 → 10.0.2.99（下一跳不可达）"
  ;;
heal-multi)
  console nn-leaf2 config "if GE-1" "no ospf hello-interval 1" exit     "no route static ipv4 10.0.3.0 24 10.0.2.99"     "no route static ipv4 1.1.1.1 32 10.0.2.99" end
  echo "已恢复：LEAF2 多异常表项"
  ;;
fault-fabric)
  # 100% 判据：SPINE-LEAF 组网内 3 台设备同时各有异常表项：
  #   LEAF2  接口 shutdown（L1 down + LLDP 邻居丢失）
  #   SPINE1 到 LEAF3 loopback 的错误静态路由（转发黑洞表项）
  #   LEAF3  hello 间隔不一致（OSPF 邻居断）
  console nn-leaf2 config "if GE-1" shutdown end
  console nn-spine1 config "route static ipv4 2.2.2.3 32 10.0.9.9" end
  console nn-leaf3 config "if GE-1" "ospf hello-interval 1 3" end
  echo "已注入（跨设备多异常表项）："
  echo "  LEAF2  GE-1 shutdown"
  echo "  SPINE1 静态路由 2.2.2.3/32 → 10.0.9.9（黑洞）"
  echo "  LEAF3  GE-1 hello 间隔 3s"
  ;;
heal-fabric)
  console nn-leaf2 config "if GE-1" "no shutdown" end
  console nn-spine1 config "no route static ipv4 2.2.2.3 32 10.0.9.9" end
  console nn-leaf3 config "if GE-1" "no ospf hello-interval 1" end
  echo "已恢复：跨设备多异常表项"
  ;;
register)
  API="${2:-http://127.0.0.1:8099}"
  command -v python3 >/dev/null || { echo "需要 python3"; exit 1; }
  API="$API" python3 - << 'PYEOF'
import json, os, sys, urllib.request
api = os.environ["API"].rstrip("/")
def call(path, payload=None, method=None):
    req = urllib.request.Request(api + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": "application/json"},
        method=method or ("POST" if payload is not None else "GET"))
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())
try:
    opts = call("/api/devices/options")
except Exception as exc:
    sys.exit("后台不可达 %s：%s（先 ./scripts/start.sh）" % (api, exc))
prof = next((p for p in opts["vendor_profiles"] if p["id"] == "cnetnexus"), None) \
    or sys.exit("vendors.json 里没有 cnetnexus 预设")
host = call("/api/events/suggest-host?force=1")["host"]
existing = {d["name"]: d for d in call("/api/devices")}
plan = [("SPINE1", "SPINE", 2301), ("LEAF1", "LEAF", 2302),
        ("LEAF2", "LEAF", 2303), ("LEAF3", "LEAF", 2304)]
for i, (name, role, port) in enumerate(plan, 1):
    body = {"name": name, "role": role, "protocol": prof["protocol"], "host": "127.0.0.1",
            "port": port, "username": "", "password": "", "enable_password": "",
            "vendor": prof["label"], "model": "", "pager_cmd": prof["pager_cmd"],
            "lldp_cmd": prof["lldp_cmd"], "enabled": True, "note": "scripts/lab.sh",
            "report_host": host, "syslog_port": 5514 + i, "trap_port": 1162 + i,
            "syslog_cmd": prof.get("syslog_cmd", ""), "trap_cmd": prof.get("trap_cmd", "")}
    if name in existing:
        d = call("/api/devices/%d" % existing[name]["id"], body, method="PUT"); act = "更新"
    else:
        d = call("/api/devices", body); act = "新增"
    try:
        r = call("/api/devices/%d/push-reporting" % d["id"], {})
        rep = "上报已下发(%d 条)" % len(r["results"])
    except Exception as exc:
        rep = "上报下发失败: %s" % exc
    print("  %s %-7s telnet 127.0.0.1:%d  syslog %d / trap %d  %s" % (act, name, port, 5514 + i, 1162 + i, rep))
try:
    st = call("/api/events/receivers/start", {})
    print("接收器已按设备端口重启：syslog %s / trap %s。上报目标地址: %s" % (
        st["syslog"]["ports"], st["trap"]["ports"], host))
except urllib.error.HTTPError as exc:
    body = exc.read().decode("utf-8", "replace")[:300]
    print("接收器重启失败（HTTP %d）: %s" % (exc.code, body))
    print("  → 多半是后台还在跑旧代码/旧依赖：./scripts/backend.sh restart 后再 ./scripts/lab.sh register；日志看 ./scripts/backend.sh logs")
print("下一步：设备页 → 探测 → 实测标定 → 一键发现拓扑（或直接在诊断页提问）")
PYEOF
  ;;
check-report)
  API="${2:-http://127.0.0.1:8099}"
  API="$API" python3 - << 'PYEOF'
import json, os, subprocess, sys, time, urllib.request
api = os.environ["API"].rstrip("/")
def call(path, payload=None, method=None):
    req = urllib.request.Request(api + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": "application/json"},
        method=method or ("POST" if payload is not None else "GET"))
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.loads(r.read().decode())
def sh(cmd):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True); return (r.stdout + r.stderr).strip()
def port_default(rs):
    return rs["syslog"]["ports"][0]

print("== 1. 后台接收器 ==")
rs = call("/api/events/receivers")
print("  syslog 监听:", rs["syslog"]["ports"], "| trap 监听:", rs["trap"]["ports"], "| trap 错误:", rs["trap"].get("error") or "无")
host = call("/api/events/suggest-host?force=1")["host"]
print("  后台实测的上报目标（探测包能到宿主机的地址）:", host)
gw = sh("docker network inspect nn-mgmt --format '{{(index .IPAM.Config 0).Gateway}}'")
print("  nn-mgmt 网桥网关:", gw)
print("  容器内解析 host.docker.internal:", sh("docker exec nn-leaf1 getent hosts host.docker.internal") or "（不解析 —— Linux 原生 Docker 正常现象，应使用网关地址）")

print("== 2. 设备上配置了什么 ==")
devs = call("/api/devices")
for d in devs:
    try:
        out = call("/api/devices/%d/test" % d["id"], {"command": "show current-configuration"})
        text = out.get("output") or out.get("text") or json.dumps(out)
        lines = [l.strip() for l in text.split("\n") if "syslog server" in l or "snmp trap server" in l]
        print("  %-7s 配置: %s | 后台记录: report_host=%s syslog=%s trap=%s" % (
            d["name"], "; ".join(lines) or "（无上报配置！）", d.get("report_host"), d.get("syslog_port"), d.get("trap_port")))
    except Exception as exc:
        print("  %-7s 读取失败: %s" % (d["name"], exc))

print("== 3. 谁在监听 5514 / 1162（宿主机）==")
print("  " + (sh("ss -lunp 2>/dev/null | grep -E ':(5514|1162|%s)\\b' || lsof -nP -iUDP:5514 -iUDP:1162 2>/dev/null" % port_default(rs)) or "（看不到监听 —— ss/lsof 不可用或端口无人监听）").replace("\n", "\n  "))
print("  ufw:", sh("sudo -n ufw status 2>/dev/null | head -1") or "（未装或需 sudo）")

def send_and_wait(via, target, port, label):
    marker = "detops-check-%s-%d" % (label, int(time.time() * 1000) % 100000)
    msg = "<190>Aug 25 10:00:00 checker lab/check-report: %s" % marker
    if via == "local":
        sh("echo '%s' | nc -u -w1 %s %d" % (msg, target, port))
    else:
        sh("docker run --rm --network nn-mgmt alpine sh -c \"echo '%s' | nc -u -w1 %s %d\"" % (msg, target, port))
    time.sleep(1.5)
    hit = [e for e in call("/api/events?kind=syslog&limit=100") if marker in e.get("message", "")]
    return bool(hit)

port = rs["syslog"]["ports"][0]
print("== 4. UDP 通路矩阵（目标端口 %d）==" % port)
ok_local = send_and_wait("local", "127.0.0.1", port, "local")
print("  本机回环 127.0.0.1:%d → %s" % (port, "✓ 后台能收" if ok_local else "✗ 后台收不到（后台监听/解析有问题，与容器无关）"))
lan = sh("hostname -I 2>/dev/null | awk '{print $1}'") or ""
results = {}
for cand in [c for c in [gw, lan, "host.docker.internal"] if c]:
    results[cand] = send_and_wait("container", cand, port, cand.replace(".", "-"))
    print("  容器 → %-22s:%d → %s" % (cand, port, "✓" if results[cand] else "✗"))
good = [c for c, ok in results.items() if ok]
if good:
    print("  → 能通的地址: %s。设备当前配置的是 %s；若不一致，运行 ./scripts/lab.sh register 重新下发" % (good, host))
    if host not in good:
        print("    （实测选址与本次矩阵不一致，说明该地址对高端口通、对 %d 不通：端口级过滤）" % port)
elif ok_local:
    print("  → 本机能收、容器全不通：宿主机防火墙拦了来自容器/虚拟机网段的 UDP %d。放行后重试：" % port)
    print("     sudo ufw allow proto udp to any port 5514:5530,1162:1180")
else:
    print("  → 连本机回环都收不到：检查后台日志 ./scripts/backend.sh logs，以及是否有别的进程占了 %d" % port)
PYEOF
  ;;
*) echo "用法: $0 {up|down|fault|heal|fault-mtu|heal-mtu|fault-multi|heal-multi|fault-fabric|heal-fabric|register|check-report}"; exit 1 ;;
esac
