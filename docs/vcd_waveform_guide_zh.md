# 仿真证据总览

`make sim_evidence` 会运行三组带标注的仿真，把波形（VCD）和比对日志放在 `simulate_result/` 中。本文是总览；每组波形怎么看、怎么在波形上证明功能正确，见各自的专门文档：

| 仿真 | 波形 | 证明什么 | 解读文档 |
|------|------|----------|----------|
| NTT 往返 | `ntt_roundtrip.vcd` | NTT 引擎 7 层节奏正确，正变换结果与参考实现一致，逆变换还原输入 | [ntt_waveform_guide_zh.md](ntt_waveform_guide_zh.md) |
| Encaps | `encaps_tc26.vcd` | ML-KEM-768 封装的 10 个阶段；中间结果和最终输出与 NIST 官方向量一致 | [encaps_waveform_guide_zh.md](encaps_waveform_guide_zh.md) |
| UART | `uart_transactions.vcd` | 串口收发、请求—应答、数据原样回传、通过串口驱动密码核心 | [uart_waveform_guide_zh.md](uart_waveform_guide_zh.md) |

---

## 1. 证据由两部分组成

| 证据 | 证明什么 | 文件 |
|------|----------|------|
| **比对日志** | 硬件输出与标准答案**逐字节一致**，这是"功能正确"的证明 | `logs/*_result.txt`，第一行为 `RESULT: PASS` |
| **VCD 波形** | 硬件**怎样**得到这个结果：各阶段的顺序、耗时、中间值 | `waves/*.vcd` |

标准答案的来源：

- **Encaps**：NIST ACVP 官方向量（ML-KEM-768 encapsulation tcId=26）；
- **NTT**：Python 参考实现 `ref/kyber_math.py`，它本身已通过 NIST 向量验证；
- **UART**：测试平台自己发出的已知数据（要求原样回传）和 Python 的 SHA3-512。

> **可信度说明**：三个测试平台（`tb/evidence_*/evidence_*_tb.v`）都只是在**未修改的** RTL 外面包了一层。标注信号通过层次引用读取内部状态，把数字翻译成文字，不改变任何硬件行为。被测的仍然是烧进 FPGA 的同一份设计。

---

## 2. 生成方法

在仓库根目录执行：

```bash
make sim_evidence
```

- 大约 15 秒完成，依次运行 `tb/evidence_ntt`、`tb/evidence_encaps`、`tb/evidence_uart`，最后打印三行 `RESULT` 和波形文件列表。
- 前提：已安装 `requirements-test.txt` 中的依赖；Encaps 测试需要本地已有 NIST 向量（运行过 `python ref/test_acvp_oracle.py`，见 `fpga_uart_testing_zh.md` 第 7 节）。
- 每次运行都会覆盖旧结果。改过 RTL 后重新运行一次即可。
- 也可以单独运行其中一个，例如 `cd tb/evidence_encaps && make`。

`.gitignore` 忽略了 `*.vcd`，所以波形不会提交到 git，需要时重新生成即可；日志文件可以提交。

---

## 3. 文件清单

```
simulate_result/
├── waves/
│   ├── ntt_roundtrip.vcd            约 1.2 MB
│   ├── encaps_tc26.vcd              约 20 MB
│   └── uart_transactions.vcd        约 11 MB
└── logs/
    ├── ntt_roundtrip_result.txt     输入多项式、NTT 结果、参考结果、INTT 结果，PASS/FAIL
    ├── ntt_roundtrip_layers.txt     每一层的时间、周期数、蝶形运算次数
    ├── encaps_tc26_result.txt       输入、中间结果期望值、NIST 与硬件的 K 和 c，PASS/FAIL
    ├── encaps_tc26_phases.txt       10 个阶段的时间、周期数、占比
    ├── encaps_tc26_expand_a.txt     矩阵 Â 的 9 个元素的采样时间
    ├── encaps_tc26_poly_ops.txt     86 条微操作及耗时
    ├── uart_result.txt              6 次事务的检查结果
    ├── uart_transactions.txt        每次事务的请求/应答字节和时间段
    └── uart_bytes.txt               串口线上每个字节的方向、数值和时间
```

---

## 4. 三份文档共同的看图要点

1. **时间单位**：VCD 用 ps，日志用 ns，日志时间乘以 1000 就是波形中的位置。时钟周期 10 ns。
2. **名称信号是 ASCII 字符串**：名称以 `_name`、`_char` 结尾的信号和 `pingpong` 请设为 ASCII 显示；只能显示十六进制时，查各文档的附录对照表。
3. **最顶层就是标注层**：展示时优先从最顶层取信号，需要细节再展开 `dut` 下的层次。
4. **读 RAM 结果看数值序列**：测试平台在下降沿改地址，RAM 在下一个上升沿出数据，按顺序读输出信号依次出现的值即可。
5. **组会建议的顺序**：先给出日志中的 `RESULT: PASS`，再用波形解释过程，最后在波形里对照一两个具体数值。
