# 12 条草稿题 · 核对清单

> **这些是草稿，不是成品。** 核对完才算你的题。
> 每条只标出**最可能出错的那一点**，核对速度大约每条 3 分钟，全部 40 分钟。
> 核对方式：改对了就改，改不动就删——**删掉一条错题，比留着一条你讲不清的题好。**

---

## 华为 6 条

### `hw_iface_004` · 接口描述 + 开启接口 · L1

```
system-view
interface GigabitEthernet0/0/3
description To-Core-SW
undo shutdown
```

**要确认的点**：`description` 后面的内容会被归一化转成小写，所以 checkpoint 写的是 `^description to-core-sw$`。

**风险**：如果模型输出 `description To-Core-SW`（大写），归一化后变小写，能命中；但如果模型写 `description "To-Core-SW"`（带引号），就不命中了。**你觉得带引号该算对吗？** 华为设备上引号不是必须的，我倾向算错，但这是你的判断。

---

### `hw_vlan_005` · Trunk 口放行 VLAN · L2

```
port link-type trunk
port trunk allow-pass vlan 10 20
```

**要确认的点**：c3/c4 我拆成了两条检查点，各自只查一个 VLAN 号：

```
^port trunk allow-pass vlan .*\b10\b
^port trunk allow-pass vlan .*\b20\b
```

这样写是为了容忍模型分两行放行（`allow-pass vlan 10` 一行、`allow-pass vlan 20` 一行）——这在设备上完全合法。

**风险**：`.*\b10\b` 也会被 `allow-pass vlan 10 to 20` 命中。这个写法放行的是 10 到 20 共 11 个 VLAN，**和题目要求的"只放行 10 和 20"不一样**，但会被判对。

**你要决定**：要不要收紧成 `vlan 10 20$` 这种严格形式？收紧了会误杀分两行的合法写法。我倾向保持宽松并在 notes 里写明，但你得知道这个洞在。

---

### `hw_trunk_006` · Eth-Trunk 链路聚合 · L2 ⭐

```
interface Eth-Trunk 1
mode lacp-static
...
eth-trunk 1
```

**这是串味重灾区，也是你项目的招牌题。** forbidden 里挡了 `port-channel` 和 `channel-group`。

**要确认的两点**：

1. `mode lacp-static` 这个语法你确认吗？华为 Eth-Trunk 有手工负载分担模式和 LACP 模式，我写的是 LACP 静态。**如果你日常用的是别的写法，以你的为准。**
2. c5 的 `^eth-trunk 1$` 在参考答案里出现了两次（两个成员口各一次），但只算一个检查点。**模型只把一个口加进聚合组也会命中。** 这算对吗？我觉得不算，但要修的话需要更复杂的正则。你的判断是什么？

---

### `hw_route_007` · 默认静态路由 · L1

```
ip route-static 0.0.0.0 0.0.0.0 192.168.1.254
```

**要确认的点**：checkpoint 写成 `0\.0\.0\.0 (0\.0\.0\.0|0)`，容忍掩码写 `0.0.0.0` 或 `0`。华为两种都接受。**确认一下你的设备上是不是这样。**

---

### `hw_acl_008` · 基本 ACL · L2

```
acl number 2000
rule 5 deny source 192.168.10.0 0.0.0.255
rule 10 permit source any
```

**要确认的三点**：

1. `acl (number )?2000` —— 新版 VRP 可以省略 `number`，老版不行。**你的场景是哪个？**
2. `rule( \d+)?` —— rule 号可省略（设备自动编号）。合理吗？
3. `rule 10 permit source any` —— 华为基本 ACL 的"放行所有"是这么写吗？还是 `rule permit`？**这条我不确定，重点核对。**

---

### `hw_diag_009` · Trunk 不通排障 · L3

题干说了"给出最关键的三条查看命令"——**这是 L3 题的必要写法**，不写数量模型会列二十条把 checkpoint 全蒙对。

**要确认的点**：三条检查点分别是 `display port vlan` / `display vlan` / `display interface`。

**风险**：`display interface` 这条太宽，模型随便写个 `display interface brief` 就命中。**但排障题本来就该宽松**——你要的是"他想到了去查接口"，不是"他写对了具体哪条"。

**你的判断**：这个宽松度合适吗？

---

## 思科 6 条

### `cs_iface_003` · 接口描述 · L1
和 `hw_iface_004` 对称，用来做厂商对比。**注意 forbidden 里挡了 `undo` 和 `display`。**

---

### `cs_vlan_004` · Trunk 放行 · L2
和 `hw_vlan_005` 对称。同样有 `.*\b10\b` 过宽的问题。

**额外风险**：思科写 `switchport trunk allowed vlan 10,20`，逗号分隔。归一化不处理逗号，所以 `.*\b10\b` 能命中 `10,20`。**确认这是你想要的。**

---

### `cs_route_005` · 默认路由 · L1
和 `hw_route_007` 对称。forbidden 挡了华为的 `ip route-static`。

---

### `cs_acl_006` · 扩展 ACL · L2

```
access-list 100 deny tcp host 192.168.1.10 host 10.0.0.5 eq 80
```

**要确认的点**：checkpoint 写了 `eq (80|www)`，因为思科上 `eq 80` 和 `eq www` 等价。**确认这个等价关系。**

**风险**：真实场景更常用命名 ACL（`ip access-list extended`），我用的是编号 ACL。**你觉得该考哪种？** 或者两种都出一题？

---

### `cs_ospf_007` · OSPF · L2

**这条的价值在于和华为的对比**：华为是 `ospf 1 router-id 1.1.1.1` 一行写完，思科是 `router ospf 1` 和 `router-id 1.1.1.1` 分两行。**这正是最容易串味的地方**，forbidden 里挡了 `^ospf 1`。

---

### `cs_pc_008` · Port-Channel · L2 ⭐

和 `hw_trunk_006` 配对，构成**串味检测的核心对照组**。

**要确认的点**：我用了 `interface range GigabitEthernet0/1 - 2` 这种批量写法。

**风险**：c2 的正则是 `^interface (range )?gigabitethernet0/1`，模型如果分两次进接口（不用 range），也能命中第一个。合理。

但 `interface range Gi0/1 - 2` 里的空格和减号，归一化会压成单空格 `interface range gigabitethernet0/1 - 2`。**确认这个形态是你要的。**

---

## 核对完之后

```powershell
PS> python scripts\validate_dataset.py
PS> pytest -v
PS> git add .; git commit -m "feat: 12 more tasks covering link-aggregation and ACL"; git push
```

## 当前进度与缺口

```
20 / 120 条
厂商  华为 12 / 思科 8
难度  L1 35% / L2 55% / L3 10%     ← L3 偏少，目标 20%
领域  接口7 VLAN5 路由4 ACL2 排障2  ← MPLS 0 条
```

**两个缺口要你自己补，我补不了：**

1. **L3 排障题** —— 需要"给现状+目标，先诊断再配置"。这类题的质量完全取决于你见过多少真实故障。
2. **MPLS 5 条** —— 手册说这是你的独家内容，绝大多数评测集没有，你日报里做过。**我写不了这部分，写了你也讲不清。**

明天（8/31）的任务：核对这 12 条 + 自己写 5 条，其中至少 2 条 L3。
