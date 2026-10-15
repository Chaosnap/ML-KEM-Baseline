# Encaps 仿真波形解读指南（`encaps_tc26.vcd`）

本文说明 ML-KEM-768 封装（Encaps）的仿真波形怎么看：选哪些信号、各信号代表什么，以及**怎样沿着数据流在波形上逐步证明 Encaps 工作正确**。

文中的时间和数值都取自实际仿真结果，与 `simulate_result/logs/encaps_tc26_*.txt` 一致。

---

## 1. 这个仿真证明了什么

测试平台用 **NIST ACVP 官方向量**（ML-KEM-768 encapsulation，tcId = 26）驱动未修改的 `auto_encaps_top`：

- 输入：消息 m（32 字节）和公钥 ek（1184 字节）；
- 输出：密文 c（1088 字节）和共享密钥 K（32 字节）；
- 与 NIST 给出的期望值**逐字节比对**。

`simulate_result/logs/encaps_tc26_result.txt` 的结论：

```
RESULT: PASS   (c match, K match)
start sampled at clock edge t = 40 ns
done high from clock edge   t = 464250 ns
latency     : 46421 clock cycles
```

日志中还列出了每个中间结果的期望值（H(ek)、K、r、ρ，以及密文前几个系数），并注明了它们的计算来源（Python hashlib 或 NIST），所以**可以在波形里一站一站地核对**，见第 6 节。

---

## 2. 生成方法和文件

```bash
make sim_evidence                    # 生成全部证据（NTT、Encaps、UART）
cd tb/evidence_encaps && make        # 只生成 Encaps 部分，约 3 秒
```

| 文件 | 内容 |
|------|------|
| `simulate_result/waves/encaps_tc26.vcd` | 波形，约 20 MB，仿真时长约 475 µs |
| `logs/encaps_tc26_result.txt` | 输入、中间结果期望值、NIST 与硬件的 K 和 c，PASS/FAIL |
| `logs/encaps_tc26_phases.txt` | 10 个阶段的起止时间、周期数、占比 |
| `logs/encaps_tc26_expand_a.txt` | 矩阵 Â 的 9 个元素各自的采样时间 |
| `logs/encaps_tc26_poly_ops.txt` | 多项式运算阶段的 86 条微操作及耗时 |

测试平台 `tb/evidence_encaps/evidence_encaps_tb.v` 只是在 `auto_encaps_top` 外面加了标注信号，不改变硬件行为。

---

## 3. 看波形前需要知道的五件事

1. **时间单位**：VCD 用 ps，日志用 ns。日志中的 22,630 ns 对应波形中的 22,630,000 ps。时钟周期 10 ns。
2. **名称信号是 ASCII 字符串**：`phase_name`、`kt_op_name`、`keccak_name`、`ntt_layer_name`、`poly_stage_name` 请设为 ASCII 显示。如果只能显示十六进制，用**附录**对照，例如 `455850414e445f41` = `EXPAND_A`。
3. **32 字节寄存器要看 `*_bytes` 版本**：控制器内部的 `m_reg`、`h_reg`、`k_reg`、`rho`、`r_reg` 把第 0 个字节存在最低位，十六进制显示时字节顺序是**反的**。顶层的 `m_bytes`、`h_bytes`、`k_bytes`、`rho_bytes`、`r_bytes` 已经调整过顺序，显示结果可以直接与日志、NIST 数据对照。
4. **缩放**：整个 Encaps 有 46,421 个周期。先看全景，再放大到第 5 节给出的时间范围。
5. **读出结果时看数值序列**：完成后测试平台逐个地址读 RAM，`host_dout` 比 `host_addr` 晚半个周期变化。按顺序读 `host_dout` 依次出现的值即可。

---

## 4. 信号选择表

推荐程度：★★★ 组会必放 ／ ★★ 讲细节时用 ／ ★ 调试用。除特别注明外，信号都在顶层。

### 4.1 控制与阶段

| 信号 | 格式 | 含义 | 推荐 |
|------|------|------|------|
| `start`、`done` | 二进制 | 启动脉冲、完成脉冲 | ★★★ |
| `busy` | 二进制 | 正在运算 | ★★ |
| `cycle_count` | 十进制 | 从 start 开始经过的周期数，`done` 时为 **46,421** | ★★★ |
| `phase_name` | ASCII | 当前阶段（10 个，见 5.1 节） | ★★★ |
| `phase_id` | 十进制 | 阶段编号 1–10 | ★★ |

### 4.2 数据：输入、中间结果、输出

| 信号 | 格式 | 含义 | 推荐 |
|------|------|------|------|
| `din_valid`、`din_ready`、`din_data` | 二进制 / 十六进制 | 输入字节握手：两者同时为 1 时，`din_data` 被接收 | ★★ |
| `m_bytes` | 十六进制 | 消息 m（32 字节） | ★★★ |
| `rho_bytes` | 十六进制 | ρ，从 ek 最后 32 字节取出，用于生成矩阵 Â | ★★ |
| `h_bytes` | 十六进制 | h = H(ek) = SHA3-256(ek)，**硬件 Keccak 算出** | ★★★ |
| `k_bytes` | 十六进制 | 共享密钥 K = G(m‖h) 前 32 字节 | ★★★ |
| `r_bytes` | 十六进制 | 加密随机数 r = G(m‖h) 后 32 字节 | ★★ |
| `host_slot`、`host_addr`、`host_dout` | 十进制 | 完成后读出密文系数：slot 16–18 为压缩后的 u，19 为压缩后的 v | ★★★ |
| `k_byte_idx`、`k_byte_out` | 十进制 / 十六进制 | 完成后逐字节读出 K | ★★★ |

### 4.3 运算过程

| 信号 | 格式 | 含义 | 推荐 |
|------|------|------|------|
| `keccak_name` | ASCII | Keccak 状态：`ABSORB`（吸收）/ `PAD`（填充）/ `PERMUTE`（置换）/ `SQUEEZE`（输出） | ★★★ |
| `keccak_round` | 十进制 | 置换轮数 0–23 | ★★ |
| `expand_entry` | 十进制 | EXPAND_A 阶段正在生成 Â 的第几个元素（0–8） | ★★★ |
| `poly_stage_name` | ASCII | POLY_OPS 阶段正在算哪个量：`NTT(r)`、`u[0]=At.r+e1` … `v=tt.r+e2+mu`、`COMPRESS` | ★★★ |
| `poly_step` | 十进制 | 微操作编号 0–85 | ★ |
| `kt_op_name` | ASCII | 正在执行的微操作（`RUN_NTT`、`RUN_BASEMUL`、`POLY_ADD` …） | ★★★ |
| `ntt_layer_name` | ASCII | NTT/INTT 当前层，如 `NTT  L1 len=128` | ★★ |
| `kt_host_we`、`kt_host_slot`、`kt_host_addr`、`kt_host_din`（`dut.u_ctrl` 层） | 二进制 / 十进制 | 控制器往 RAM 写系数的**写使能**、slot、下标、数据（INPUT_EK 写 t̂、EXPAND_A 写 Â、LOAD_M 写 μ） | ★★ |

---

## 5. 推荐截图（按展示顺序）

### 5.1 全景：10 个阶段

信号：`phase_name`、`busy`、`done`、`cycle_count`，时间范围 0 – 475,000 ns。

| 阶段 | 名称 | 开始 (ns) | 结束 (ns) | 周期 | 占比 | 做什么 |
|------|------|-----------|-----------|------|------|--------|
| 1 | INPUT_M | 40 | 360 | 32 | 0.1% | 接收 m |
| 2 | INPUT_EK | 360 | 21,730 | 2,137 | 4.6% | 接收 ek，同时 SHA3-256 吸收 ek |
| 3 | H_SQUEEZE | 21,730 | 22,630 | 90 | 0.2% | 输出 h = H(ek) |
| 4 | G_ABSORB | 22,630 | 23,280 | 65 | 0.1% | SHA3-512 吸收 m‖h |
| 5 | G_SQUEEZE | 23,280 | 24,820 | 154 | 0.3% | 输出 K 和 r |
| 6 | EXPAND_A | 24,820 | 149,110 | 12,429 | 26.8% | 生成矩阵 Â（9 个多项式） |
| 7 | LOAD_M | 149,110 | 151,670 | 256 | 0.6% | m 的每一位 → 0 或 1665 |
| 8 | PRF_CBD | 151,670 | 183,170 | 3,150 | 6.8% | 采样噪声 y、e1、e2 |
| 9 | POLY_OPS | 183,170 | 464,240 | 28,107 | 60.5% | NTT、乘法、INTT、加法、压缩 |
| 10 | DONE | 464,240 | 464,250 | 1 | 0.0% | 完成 |

### 5.2 其他推荐截图

| # | 截图 | 信号 | 时间范围 | 看点 |
|---|------|------|----------|------|
| 2 | **哈希 H 的输出** | `phase_name`、`keccak_name`、`h_bytes` | 21,700 – 22,700 ns | `h_bytes` 从左到右逐字节填满 |
| 3 | **一次 Keccak 置换** | `keccak_name`、`keccak_round` | 2,620 – 2,900 ns | 轮数 0 → 23，共 24 个周期 |
| 4 | **生成矩阵** | `expand_entry`、`keccak_name`、`kt_host_we`、`kt_host_din` | 24,800 – 149,200 ns（全段），或 25,400 – 25,700 ns（放大） | 9 段，每段长度不同；Â 的第一个系数 1961 |
| 5 | **多项式运算** | `poly_stage_name`、`kt_op_name`、`ntt_layer_name` | 183,000 – 464,300 ns | 6 段计算，固定的微操作序列 |
| 6 | **一次 NTT 的 7 层** | `kt_op_name`、`ntt_layer_name` | 185,800 – 195,000 ns | `RUN_NTT` 期间 7 段，各 1,300 ns |
| 7 | **结果读出** | `done`、`host_slot`、`host_dout`、`k_byte_idx`、`k_byte_out` | 464,200 – 474,900 ns | 读出值与 NIST 一致 |

---

## 6. 在波形上证明 Encaps 工作正确（七个检查点）

按数据流顺序，每一站都把**波形里的值**和**日志中给出的期望值**对照。

### 检查点 1：输入正确接收

- 信号：`din_valid`、`din_ready`、`din_data`、`m_bytes`、`rho_bytes`
- 40 ns 起，`din_data` 依次为 `4e 77 59 61 …`，每个周期 1 字节（`din_valid` 和 `din_ready` 都为 1）。
- `m_bytes` 从左到右逐字节填入，360 ns 时完整：

  ```
  m_bytes  = 4e77596168711e913965d8175ac3bd76aab08b7f9385a02ae883cf6c6e17dd81
  日志 m   = 4e77596168711e913965d8175ac3bd76aab08b7f9385a02ae883cf6c6e17dd81   ✓
  ```

- 21,730 ns（ek 接收完）时，`rho_bytes` = `497ecff0…19a6e20a`，与日志中 "rho（ek 的最后 32 字节）" 相同 ✓

### 检查点 2：硬件 SHA3-256 正确（h = H(ek)）

- 信号：`phase_name`、`keccak_name`、`h_bytes`
- `phase_name` = `H_SQUEEZE` 期间，`h_bytes` 从 22,000 ns 开始每 20 ns 填入一个字节。22,630 ns 时：

  ```
  h_bytes         = 03ecc855c2acd21b09157de2efb9a09fb3550dfee752404bdec48383110bb08f
  Python SHA3-256 = 03ecc855c2acd21b09157de2efb9a09fb3550dfee752404bdec48383110bb08f   ✓
  ```

- 这说明硬件 Keccak 对 1184 字节 ek 的哈希结果与 Python `hashlib` 完全一致。

### 检查点 3：硬件 SHA3-512 正确（K、r = G(m‖h)）

- 信号：`k_bytes`、`r_bytes`（24,820 ns 以后）

  ```
  k_bytes = 79d74f6c6c2d916bec47bd828fd9b67295a37f54927fab1263c0d122f1c6f1ed   = NIST K ✓
  r_bytes = 80e5459d0982d3ebce4c1207ffe5cdbfe71636c50cfda47e5a4c4ab7844267d0   = 日志中 Python G 的后半段 ✓
  ```

- **共享密钥 K 在阶段 5 就已经算出来了**，后面的阶段都是在计算密文 c。

### 检查点 4：矩阵采样正确（Â）

- 信号：`expand_entry`、`dut.u_ctrl.kt_host_we`、`kt_host_slot`、`kt_host_addr`、`kt_host_din`
- EXPAND_A 阶段，每接受一个候选值，控制器就用写使能 `kt_host_we` 把它写进 RAM：

  | 时间 (ns) | `kt_host_we` | `kt_host_slot` | `kt_host_addr` | `kt_host_din` |
  |-----------|--------------|----------------|----------------|---------------|
  | 25,510 | 1 | 0 | 0 | **1961** |
  | 25,520 | 1 | 0 | 1 | **820** |
  | 25,590 | 1 | 0 | 2 | **972** |

- 用 Python 参考实现 `sample_ntt(ρ, 0, 0)` 计算 Â[0][0] 的前几个系数，结果是 `1961, 820, 972, 1972, 3123, 439` ✓
- `expand_entry` 0 → 8 共 9 段，每段 1,338 – 1,418 个周期，长度不同。这是拒绝采样的特征：只保留小于 q = 3329 的候选值，所以需要的随机字节数随数据变化。

### 检查点 5：运算节奏符合算法结构

- 信号：`poly_stage_name`、`kt_op_name`、`ntt_layer_name`
- 多项式运算分 6 段（见 `encaps_tc26_poly_ops.txt`）：

  | 段 | 公式 | 周期 |
  |----|------|------|
  | `NTT(r)` | ŷ = NTT(y) | 4,290 |
  | `u[0]=At.r+e1` | u[0] = INTT(Σ Â[j][0]·ŷ[j]) + e1[0] | 5,566 |
  | `u[1]=At.r+e1` | 同上，第 1 列 | 5,566 |
  | `u[2]=At.r+e1` | 同上，第 2 列 | 5,566 |
  | `v=tt.r+e2+mu` | v = INTT(Σ t̂[j]·ŷ[j]) + e2 + μ | 5,825 |
  | `COMPRESS` | 压缩成密文 | 1,036 |

- 每一段内部，`kt_op_name` 都是固定的序列：`COPY_TO_BM_A → COPY_TO_BM_B → RUN_BASEMUL → COPY_FROM_BM`（一次乘法）→ `POLY_ADD`（累加）→ … → `COPY_TO_NTT → RUN_INTT → COPY_FROM_NTT` → `POLY_ADD`。与 FIPS 203 的公式一一对应。
- 185,810 ns 起，`ntt_layer_name` 显示 `NTT  L1 len=128` … `NTT  L7 len=  2` 七段，每段 1,300 ns。这就是 NTT 引擎在 Encaps 里的工作节奏，与单独的 NTT 波形完全一致（见 `ntt_waveform_guide_zh.md`）。

### 检查点 6：密文 c 正确

- 信号：`host_slot`、`host_dout`（464,250 ns 以后）
- `host_dout` 依次出现的值（每个 slot 开头的一两个值是切换 slot 时的残留，从下表的时间开始读）：

  | slot | 含义 | 从此时开始 (ns) | `host_dout` 依次为 | 日志中的期望值（由 NIST 密文解码） |
  |------|------|-----------------|--------------------|-----------------------------------|
  | 16 | u[0] | 464,260 | 259 545 78 308 535 632 699 253 | 259 545 78 308 535 632 699 253 ✓ |
  | 17 | u[1] | 466,830 | 508 684 941 296 381 969 669 | 508 684 941 296 381 969 669 420 ✓ |
  | 18 | u[2] | 469,390 | 707 448 643 475 902 318 336 | 707 448 643 475 902 318 336 109 ✓ |
  | 19 | v | 471,950 | 6 15 0 14 13 15 10 | 6 15 0 14 13 15 10 15 ✓ |

- u 的系数是 10 位（0–1023），v 的系数是 4 位（0–15），与 ML-KEM-768 的压缩参数 du = 10、dv = 4 一致。
- 全部 1088 字节的比对结果在 `encaps_tc26_result.txt`：`c match`，NIST 与硬件的 SHA3-256 摘要相同。

### 检查点 7：共享密钥读出正确

- 信号：`k_byte_idx`、`k_byte_out`（474,500 ns 以后）
- `k_byte_idx` = 0、1、2、3、4 … 时，`k_byte_out` = **79、d7、4f、6c、6c、2d** …，与 NIST K `79d74f6c6c2d…` 逐字节相同 ✓
- `k_byte_out` 在很早之前就一直是 `79`：K 在阶段 5 就已算出，而 `k_byte_idx` 默认是 0，所以一直显示第 0 个字节。

最后，`done` 在 **464,250 ns** 拉高，此时 `cycle_count` = **46,421**，与日志中的延迟一致。

---

## 7. 讲解词参考

> 这是用 NIST 官方向量跑的 ML-KEM-768 封装仿真。整个过程 46,421 个周期，分 10 个阶段，其中多项式运算占 60%，生成矩阵占 27%。
>
> 沿着数据流看：输入的 m 被正确接收；硬件 Keccak 算出的 H(ek) 与 Python 的 SHA3-256 一致；紧接着算出的 K 就是 NIST 给出的共享密钥；生成矩阵的第一个系数 1961、820、972 与参考实现一致。
>
> 运算阶段严格按照 FIPS 203 的公式执行：先做 NTT，然后按列做乘法累加、逆变换、加噪声，最后压缩。放大可以看到 NTT 的 7 层。
>
> 最后读出的密文系数和共享密钥，都与 NIST 的期望值逐字节一致，所以 Encaps 功能正确。

---

## 8. 常见问题

| 问题 | 解答 |
|------|------|
| 名称信号显示成十六进制 | 设为 ASCII 显示，或查附录对照表 |
| `h_reg`、`k_reg` 显示的十六进制和日志对不上 | 字节顺序是反的，请看顶层的 `h_bytes`、`k_bytes` |
| `phase_id` = 10 比 `done` 早一个周期 | 正常：控制器进入 DONE 阶段后，下一个时钟沿才把 `done` 置 1 |
| INPUT_EK 为什么用了 2,137 个周期，而不是 1184 个 | 每收 3 字节要花 2 个周期把解析出的 2 个系数写入 RAM；每收满 136 字节还要做一次 24 轮的 Keccak 置换，这期间 `din_ready` 为 0 |
| 9 个 Â 元素的耗时为什么不同 | 拒绝采样的特性，需要的随机字节数取决于数据 |
| `RUN_NTT` 是 914 个周期，不是 911 | kyber_top 调度 NTT 引擎有 3 个周期的开销 |
| 读出时 `host_dout` 开头的值和期望对不上 | 切换 slot 时有一两个残留值，从检查点 6 表中的时间开始读 |
| VCD 为什么有 20 MB | 包含 Keccak 的 1600 位状态寄存器，置换时每个周期都在变。展示时只加本文推荐的信号即可 |

---

## 附录：十六进制 → 文字对照表

### `phase_name`

| 文字 | 十六进制 |
|------|----------|
| `IDLE` | `49444c45` |
| `INPUT_M` | `494e5055545f4d` |
| `INPUT_EK` | `494e5055545f454b` |
| `H_SQUEEZE` | `485f53515545455a45` |
| `G_ABSORB` | `475f4142534f5242` |
| `G_SQUEEZE` | `475f53515545455a45` |
| `EXPAND_A` | `455850414e445f41` |
| `LOAD_M` | `4c4f41445f4d` |
| `PRF_CBD` | `5052465f434244` |
| `POLY_OPS` | `504f4c595f4f5053` |
| `DONE` | `444f4e45` |

### `keccak_name`

| 文字 | 十六进制 |
|------|----------|
| `IDLE` | `49444c45` |
| `ABSORB` | `4142534f5242` |
| `PAD` | `504144` |
| `PERMUTE` | `5045524d555445` |
| `SQUEEZE` | `53515545455a45` |

### `kt_op_name`

| 文字 | 十六进制 |
|------|----------|
| `-`（空闲） | `2d` |
| `COPY_TO_NTT` | `434f50595f544f5f4e5454` |
| `COPY_FROM_NTT` | `434f50595f46524f4d5f4e5454` |
| `RUN_NTT` | `52554e5f4e5454` |
| `RUN_INTT` | `52554e5f494e5454` |
| `COPY_TO_BM_A` | `434f50595f544f5f424d5f41` |
| `COPY_TO_BM_B` | `434f50595f544f5f424d5f42` |
| `COPY_FROM_BM` | `434f50595f46524f4d5f424d` |
| `RUN_BASEMUL` | `52554e5f424153454d554c` |
| `POLY_ADD` | `504f4c595f414444` |
| `COMPRESS` | `434f4d5052455353` |
| `CBD_SAMPLE` | `4342445f53414d504c45` |

### `poly_stage_name`

| 文字 | 十六进制 |
|------|----------|
| `NTT(r)` | `4e5454287229` |
| `u[0]=At.r+e1` | `755b305d3d41742e722b6531` |
| `u[1]=At.r+e1` | `755b315d3d41742e722b6531` |
| `u[2]=At.r+e1` | `755b325d3d41742e722b6531` |
| `v=tt.r+e2+mu` | `763d74742e722b65322b6d75` |
| `COMPRESS` | `434f4d5052455353` |

`ntt_layer_name` 的对照见 `ntt_waveform_guide_zh.md` 附录。
