#!/bin/bash
# CNetNexus 实验环境：起 4 台真机（1×SPINE + 3×LEAF），配好 IP / LLDP / Telnet
#   ./scripts/lab.sh up      拉起并配置
#   ./scripts/lab.sh down    销毁
#   ./scripts/lab.sh fault      注入故障（LEAF2 上行口 shutdown）
#   ./scripts/lab.sh heal       恢复
#   ./scripts/lab.sh fault-mtu  注入协议层故障（LEAF3 hello 间隔不一致）
#   ./scripts/lab.sh heal-mtu   恢复
#   ./scripts/lab.sh fault-multi   单设备多异常表项（80% 判据环境，LEAF2 三处异常）
#   ./scripts/lab.sh heal-multi    恢复
#   ./scripts/lab.sh fault-fabric  跨设备多异常表项（100% 判据环境，三台设备各一处）
#   ./scripts/lab.sh heal-fabric   恢复
set -e
IMG="${NN_IMAGE:-netnexus:1.0.0-arm64}"
DEVS="nn-spine1 nn-leaf1 nn-leaf2 nn-leaf3"

console() { local c="$1"; shift
  { printf '%s\n' "$@"; printf 'exit\n'; } | docker exec -i \
    -e NN_CONSOLE_SOCK=/opt/netnexus/run/console.sock "$c" \
    /opt/netnexus/bin/netnexus-console >/dev/null 2>&1 || true; }

case "${1:-up}" in
up)
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
*) echo "用法: $0 {up|down|fault|heal|fault-mtu|heal-mtu|fault-multi|heal-multi|fault-fabric|heal-fabric}"; exit 1 ;;
esac
