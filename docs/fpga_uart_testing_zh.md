# FPGA UART 测试指南

本文说明怎样在 Digilent **Arty A7-100T** 上编译、烧录 ML-KEM-768 加速器，并用 Python 脚本通过 UART 验证硬件结果。文中还包括上板前的仿真验证方法。

---

## 1. 测试原理

```
 ┌──────────────┐   USB-UART (115200 8N1)   ┌─────────────────────────────────────┐
 │  PC (Python) │ ◀───────────────────────▶ │ FPGA: uart_rx/tx → uart_host (协议) │
 │ test_fpga_   │                           │   ├─ core 0: auto_keygen_top        │
 │   uart.py    │                           │   ├─ core 1: auto_encaps_top        │
 └──────┬───────┘                           │   └─ core 2: decaps_top (Decrypt)   │
        │ 对比                              └─────────────────────────────────────┘
 ┌──────▼───────────────┐
 │ ref/kyber_acvp.py    │  Python 参考实现（已通过 NIST ACVP 验证）
 └──────────────────────┘
```

测试流程：

1. 脚本生成随机输入，例如种子 d、z 和消息 m。
2. 通过 UART 把输入发给 FPGA，FPGA 完成运算后，脚本读回多项式和密钥字节。
3. 脚本按 FIPS 203 把读回的数据编码成标准的 ek / dk / c / K。
4. 与 Python 参考实现的结果逐字节比较，只要有一个字节不同就判为失败。

完整的 ML-KEM.Decaps 分两步：先在 FPGA 的 decaps 核心上解密，再用 encaps 核心重新加密做 FO 校验。PC 端只计算隐式拒绝时用到的哈希 J(z‖c)。

---

## 2. 环境准备

| 项目 | 要求 |
|------|------|
| 开发板 | Digilent Arty A7-100T（XC7A100T-1CSG324C） |
| 连接 | Micro-USB 线同时负责 JTAG 烧录和 UART 通信 |
| Vivado | 用于综合、实现和烧录（需要在 Windows 或 Linux 上运行） |
| Python | 3.9 及以上 |
| 仿真（可选） | Icarus Verilog：`brew install icarus-verilog` |

安装 Python 依赖（在仓库根目录执行）：

```bash
# 使用 uv
uv pip install -r requirements-test.txt -r scripts/requirements.txt

# 或使用 pip
pip install -r requirements-test.txt -r scripts/requirements.txt
```

`requirements-test.txt` 包含仿真用的 cocotb 和 kyber-py，`scripts/requirements.txt` 包含串口库 pyserial。

---

## 3. 第一步：仿真验证（上板前，可选但推荐）

仿真用的测试程序与上板测试是同一个 `test_fpga_uart.py`，区别只是 UART 两端接的是仿真出来的 FPGA。仿真通过后再上板，出了问题更容易判断是 RTL 的问题还是板级问题。

```bash
make test_fpga_top           # 约 3 分钟
make waves_fpga_top          # 同时导出波形 tb/fpga_top/sim_build/fpga_top.fst
```

通过时的输出：

```
** test_fpga_top.test_host_suite            PASS **
** test_fpga_top.test_rx_timeout_recovery   PASS **
** test_fpga_top.test_error_led             PASS **
** TESTS=3 PASS=3 FAIL=0 SKIP=0             **
```

改过 RTL 之后，也应先运行全部模块测试：

```bash
make test
```

---

## 4. 第二步：编译比特流

打开 Vivado，在 **Tcl Console** 中执行（路径换成你的仓库位置）：

```tcl
source /Users/yuki/Downloads/Vivado/ML-KEM-Baseline/fpga/build.tcl
```

这个脚本会依次完成：

1. 创建并保存工程 `fpga/vivado_proj/ml_kem_uart.xpr`。如果 GUI 里已经打开了其他工程，会先把它关掉。
2. 运行综合、实现并生成比特流。
3. 输出报告到 `fpga/vivado_proj/reports/`（`timing.rpt`、`utilization.rpt`）。
4. 在最后打印时序结果。

**⚠️ 必须确认时序满足：**

```
Bitstream: .../ml_kem_uart.runs/impl_1/fpga_top.bit
Timing:    WNS = x.xxx ns, WHS = x.xxx ns
Timing met. Program with: source .../fpga/program.tcl
```

- WNS 和 WHS 都 ≥ 0，才能保证硬件运算正确。
- 如果出现 `WARNING: timing NOT met`，把 `build.tcl` 顶部的 `mmcm_div` 调大（例如 50 改成 60），再重新 `source`。

常用配置都在 `build.tcl` 顶部的 Settings 区域：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `mmcm_div` | 50 | 核心时钟 = 1000 MHz / mmcm_div，默认 20 MHz |
| `baud` | 115200 | UART 波特率，必须与测试脚本的 `--baud` 一致 |
| `ENABLE_KEYGEN/ENCAPS/DECAPS` | 全部为 1 | 需要启用的核心 |
| `jobs` | 4 | 并行编译的线程数 |

> **为什么是 20 MHz 而不是 100 MHz？** 运算核心没有做流水线，其中 `basemul_unit` 有 5 个 DSP48 串联，组合路径约 41 ns。在 100 MHz 下时序违例约 -31 ns，结果就是 keygen/encaps 算错，而 UART 回环测试仍然正常。降到 20 MHz 后测试全部通过。
>
> 之后想重新打开这个工程，可以用 *File → Open Project* 选 `fpga/vivado_proj/ml_kem_uart.xpr`。

---

## 5. 第三步：烧录

用 USB 线连接开发板，然后在 Tcl Console 中执行：

```tcl
source /Users/yuki/Downloads/Vivado/ML-KEM-Baseline/fpga/program.tcl
```

默认烧录的是 `build.tcl` 生成的比特流。要烧录其他文件，先设置 `bit_file`：

```tcl
set bit_file /path/to/other.bit
source /Users/yuki/Downloads/Vivado/ML-KEM-Baseline/fpga/program.tcl
```

烧录成功后，开发板上的 LED 含义如下：

| LED | 含义 |
|-----|------|
| LD4（led[0]） | 心跳闪烁，表示时钟和 MMCM 正常 |
| LD5（led[1]） | 有核心正在运算 |
| LD6（led[2]） | 正在处理一条 UART 请求 |
| LD7（led[3]） | 上一次响应是错误 |

按板上的 **RESET** 按钮会复位整个设计。

---

## 6. 第四步：运行上板测试

### 6.1 查找串口

```bash
uv run python scripts/test_fpga_uart.py --list
```

Arty 板上的 FT2232 会出现两个串口，UART 一般是**编号较大的那个**：

- macOS：`/dev/cu.usbserial-XXXXXXXXXX1`（以 `1` 结尾）
- Linux：`/dev/ttyUSB1`
- Windows：两个 COM 口中编号较大的那个

### 6.2 基本测试

```bash
uv run python scripts/test_fpga_uart.py --port /dev/cu.usbserial-41032807F7C11
```

### 6.3 常用参数

| 参数 | 说明 | 示例 |
|------|------|------|
| `--port` | 串口（必填） | `--port /dev/cu.usbserial-XXXX1` |
| `--baud` | 波特率，默认 115200 | `--baud 921600` |
| `-n` | 每项测试的随机向量个数，默认 3 | `-n 100` |
| `--seed` | 随机种子，用于复现失败用例 | `--seed 1684936210` |
| `--acvp` | 同时运行 NIST ACVP 官方向量 | `--acvp` |
| `--acvp-set` | 只运行指定类别的 ACVP 向量（keygen / encaps / decaps），默认全部 | `--acvp-set encaps` |
| `-v` | 逐条打印每个 ACVP 向量的结果和运算周期数 | `-v` |
| `--only` | 只运行指定的测试项 | `--only link loopback` |
| `--timeout` | 串口读超时，单位秒，默认 2 | `--timeout 5` |

每次运行都会打印本次用的 `seed=...`。某次测试失败时，用同一个 `--seed` 重跑就能得到完全相同的输入。

### 6.4 常用命令组合

```bash
# 快速检查链路是否正常（约 1 秒）
uv run python scripts/test_fpga_uart.py --port <串口> --only link errors loopback

# 稳定性压力测试：每项 100 个随机向量
uv run python scripts/test_fpga_uart.py --port <串口> -n 100

# NIST ACVP 官方向量（详见第 7 节）
uv run python ref/test_acvp_oracle.py
uv run python scripts/test_fpga_uart.py --port <串口> --acvp --only acvp

# 复现某次失败
uv run python scripts/test_fpga_uart.py --port <串口> --seed 1684936210 --only keygen
```

### 6.5 测试项说明

| 测试项 | 内容 | 需要的核心 |
|--------|------|-----------|
| `link` | PING 连续多次，检查协议版本和核心能力位（caps） | 任意 |
| `errors` | 发送未知命令、非法 slot、不存在的核心，确认返回正确错误码，且之后通信仍然同步 | 任意 |
| `loopback` | 在每个核心的 slot 0 和 slot 19 写入随机多项式再读回比较，用来检查 UART 和 RAM 通路 | 任意 |
| `keygen` | ML-KEM.KeyGen：随机 (d, z)，比较 ek（1184 B）和 dk（2400 B） | keygen |
| `encaps` | ML-KEM.Encaps：随机 ek 和 m，比较 c（1088 B）和 K（32 B） | encaps |
| `decaps` | ML-KEM.Decaps：分别用正确密文和随机翻转 1 位的密文测试，后者用来检查隐式拒绝 | encaps + decaps |
| `roundtrip` | 全部在 FPGA 上完成 KeyGen → Encaps → Decaps，两端的共享密钥必须一致 | 全部 |
| `acvp` | NIST ML-KEM-768 官方向量，共 60 个（需要 `--acvp`，详见第 7 节） | 按已有核心运行 |

bitstream 中没有的核心，对应测试会显示 `SKIP`，这不算失败。

### 6.6 正常结果示例

```
Port /dev/cu.usbserial-41032807F7C11 @ 115200 baud, seed=1684936210
[ RUN  ] link
    version=0x01 caps=0b111 (keygen, encaps, decaps)
[ PASS ] link (0.09s)
[ RUN  ] errors
[ PASS ] errors (0.06s)
[ RUN  ] loopback
[ PASS ] loopback (0.58s)
[ RUN  ] keygen
    [1/3] ok (35613 cycles)
    ...
[ PASS ] keygen (1.03s)
[ RUN  ] encaps
    [1/3] ok (44110 cycles)
    ...
[ PASS ] roundtrip (4.13s)
[ RUN  ] acvp
[ SKIP ] acvp: enable with --acvp

7 passed, 0 failed, 1 skipped
```

`cycles` 是核心的运算周期数，从最后一个输入字节算到运算完成，不包括 UART 传输时间。在 20 MHz 下，KeyGen 约 35.5k 周期（约 1.8 ms），Encaps 约 44k 周期（约 2.2 ms）。

所有测试都通过时，进程退出码为 0；只要有一项失败，退出码为 1，可以直接用在脚本或 CI 里。

---

## 7. NIST ACVP 向量检测

随机向量测试只能说明 FPGA 和本项目的 Python 参考实现结果一致。**NIST ACVP 向量检测**则直接拿 NIST 官方给出的输入和期望输出来比对，用来证明硬件符合 FIPS 203 标准。

### 7.1 向量来源与数量

向量来自 NIST 的 [usnistgov/ACVP-Server](https://github.com/usnistgov/ACVP-Server) 仓库（`gen-val/json-files/`），每组都有两个文件：`prompt.json` 是输入，`expectedResults.json` 是期望输出。本项目只使用 **ML-KEM-768** 参数集的向量：

| 类别 | ACVP 目录 | 向量数 | 输入 | 比对内容 |
|------|-----------|--------|------|----------|
| keyGen | `ML-KEM-keyGen-FIPS203` | 25 | d、z（各 32 B） | ek（1184 B）、dk（2400 B）逐字节比对 |
| encapsulation | `ML-KEM-encapDecap-FIPS203` | 25 | ek、m | c（1088 B）、K（32 B）逐字节比对 |
| decapsulation | `ML-KEM-encapDecap-FIPS203` | 10 | dk、c | K（32 B）比对，包含 NIST 构造的非法密文，用来检查隐式拒绝 |

下载后保存在 `ref/acvp_vectors/`（这个目录已在 `.gitignore` 中，不会提交）：

```
ref/acvp_vectors/
├── keygen_prompt.json          keygen_results.json
└── encapdecap_prompt.json      encapdecap_results.json
```

### 7.2 第 1 步：下载向量并验证参考实现

```bash
uv run python ref/test_acvp_oracle.py
```

这条命令做两件事：

1. 第一次运行时联网下载 4 个 JSON 文件，之后直接使用本地缓存。
2. 用 Python 参考实现 `ref/kyber_acvp.py` 跑一遍全部 60 个向量。

**这一步是前提。** 如果参考实现本身都没通过 NIST 向量，后面拿它和硬件比对就失去了意义。正常输出：

```
=== ML-KEM-768 KeyGen ===
  KeyGen: 25/25 passed

=== ML-KEM-768 Encapsulation ===
  Encapsulation: 25/25 passed

=== ML-KEM-768 Decapsulation ===
  Decapsulation: 10/10 passed

==================================================
ALL TESTS PASSED -- Oracle matches ACVP vectors
```

### 7.3 第 2 步（可选）：仿真中跑 ACVP 向量

在上板之前，可以先确认 RTL 在仿真中能通过 NIST 向量：

```bash
make test_acvp                                # 参考实现 + keygen/encaps/decaps 三个控制器的 ACVP 测试
make test_auto_keygen test_auto_encaps        # 上板所用的自主核心（含硬件 Keccak）跑 ACVP 向量
```

`test_auto_keygen` 和 `test_auto_encaps` 测的就是烧进 FPGA 的 `auto_keygen_top` 和 `auto_encaps_top`，与上板测试最接近。

### 7.4 第 3 步：在 FPGA 上跑 ACVP 向量

烧录完成后执行：

```bash
uv run python scripts/test_fpga_uart.py --port /dev/cu.usbserial-41032807F7C11 --acvp --only acvp
```

- `--acvp` 打开 ACVP 测试。不加这个参数时，这一项会显示 SKIP。
- `--only acvp` 只跑 ACVP。去掉它就会把随机向量测试也一起跑一遍。
- 在 115200 波特率下，全部 60 个向量大约需要 30 秒，时间主要花在 UART 传输上。

正常输出：

```
[ RUN  ] acvp
    ML-KEM-768 keyGen         25/25 ok
    ML-KEM-768 encapsulation  25/25 ok
    ML-KEM-768 decapsulation  10/10 ok
    total 60 NIST ACVP vectors ok
[ PASS ] acvp (xx.xxs)

1 passed, 0 failed, 0 skipped
```

每类向量在 FPGA 上是这样执行的：

| 类别 | FPGA 上的操作 | PC 端的操作 |
|------|---------------|-------------|
| keyGen | 把 d 发给 keygen 核心，读回 t̂、ŝ 和 ρ | 按 FIPS 203 编码出 ek、dk（dk 中的 H(ek) 和 z 由 PC 拼接），再与 NIST 结果比对 |
| encapsulation | 把 m‖ek 发给 encaps 核心，读回压缩后的 u、v 和 K | 编码出 c，再与 NIST 结果比对 |
| decapsulation | decaps 核心解密得到 m'，encaps 核心用 m' 重新加密得到 c' 和 K' | 比较 c' 与 c：相等则取 K'，否则取 J(z‖c)，再与 NIST 结果比对 |

如果 bitstream 里缺少某个核心，对应的那一类向量会被跳过。比如没有 decaps 核心时，只跑 keyGen 和 encapsulation。

### 7.5 失败时怎么排查

某个向量失败后，测试会继续跑完其余向量，最后列出所有失败向量的类别和 `tcId`，例如：

```
    FAIL encapsulation  tcId=26  command 0x20 failed: 0x05 (core timeout)
    ML-KEM-768 encapsulation  24/25 ok
[ FAIL ] acvp: 1 ACVP vector(s) failed: encapsulation tcId=26
```

常见的失败类型：

| 报错 | 含义 |
|------|------|
| `c mismatch` / `ek mismatch` / `K mismatch` | 核心跑完了，但结果与 NIST 不一致 |
| `0x05 (core timeout)` | 核心卡住了：可能是 100 ms 内没接收完输入，也可能是没跑完。FPGA 已经自动复位了该核心 |
| `no response` | FPGA 没有响应。脚本会自动重新同步，然后继续跑后面的向量 |

排查顺序：

1. **先看第 7.2 步是否通过。** 参考实现没通过时，问题出在向量文件或 Python 环境，与硬件无关。
2. **只跑出问题的那一类向量**，并打印每个向量的结果，看是全部失败还是个别失败：
   ```bash
   uv run python scripts/test_fpga_uart.py --port <串口> --acvp --only acvp --acvp-set encaps -v
   ```
3. **跑随机向量对照**：运行 `--only encaps -n 10`。如果随机测试也失败，多半是时序问题（见第 4 节）。
4. **在仿真里复现**：运行 `make test_auto_encaps`，它不经过 UART，直接驱动 encaps 核心跑全部 25 个 NIST 向量。
   - 仿真通过、上板失败：属于板级问题，重点检查时序报告，以及上板时 UART 的传输情况。
   - 仿真也失败：属于 RTL 逻辑问题。
5. 想查看某个 tcId 的原始数据，可以在 `ref/acvp_vectors/*_prompt.json` 中搜索 `"tcId": 26`。

### 7.6 无法联网时的手动下载

如果运行 FPGA 测试的电脑不能访问 GitHub，可以在另一台电脑上下载下面 4 个文件，**按下表重命名后**放到 `ref/acvp_vectors/`：

| 下载地址（前缀 `https://raw.githubusercontent.com/usnistgov/ACVP-Server/master/gen-val/json-files/`） | 保存为 |
|------|--------|
| `ML-KEM-keyGen-FIPS203/prompt.json` | `keygen_prompt.json` |
| `ML-KEM-keyGen-FIPS203/expectedResults.json` | `keygen_results.json` |
| `ML-KEM-encapDecap-FIPS203/prompt.json` | `encapdecap_prompt.json` |
| `ML-KEM-encapDecap-FIPS203/expectedResults.json` | `encapdecap_results.json` |

放好之后，再运行一次 `uv run python ref/test_acvp_oracle.py` 确认文件可用。缓存已经存在时，它不会再联网。

---

## 8. 常见问题排查

| 现象 | 可能原因 | 解决方法 |
|------|----------|----------|
| `link` 报 `expected 1 bytes, got 0` | 串口选错、没有烧录，或波特率不一致 | 换另一个串口（以 `1` 结尾的那个）；重新烧录；检查 `--baud` 与 `build.tcl` 里的 `baud` 是否相同 |
| LD4 不闪烁 | 没有烧录成功，或 MMCM 没有锁定 | 重新运行 `program.tcl`，并检查约束文件 |
| link/loopback 通过，keygen/encaps 失败 | **时序不满足** | 查看 `reports/timing.rpt` 中的 WNS，调大 `mmcm_div` 后重新编译 |
| 串口被占用（`Resource busy`） | 其他程序（串口终端、另一个脚本）正在使用这个串口 | 关闭占用该串口的程序 |
| 测试中途超时，之后全部失败 | FPGA 处在一个没处理完的请求中 | 脚本会自动发送 RESET；如果仍然不行，按板上的 RESET 按钮 |
| `acvp` 显示 SKIP：vectors not cached | 还没有下载 ACVP 向量 | 先运行 `uv run python ref/test_acvp_oracle.py`，无法联网时见 7.6 节 |
| LD7 常亮 | 上一次请求返回了错误 | 属于正常提示，下一次成功的请求会让它熄灭 |

---

## 9. 附录：UART 协议速查

串口格式为 8N1，默认 115200 波特率。

- 请求：`[cmd][参数][数据]`
- 响应：`[status][数据]`（只有 status 为 OK 时才带数据）
- 多字节数值都是小端序；多项式是 256 个 u16。

| cmd | 名称 | 参数 | 请求数据 | 响应数据 |
|-----|------|------|----------|----------|
| 0x01 | PING | – | – | `'K' 'Y' version caps` |
| 0x02 | RESET | – | – | – |
| 0x10 | KEYGEN | – | d（32 B） | cycles（u32） |
| 0x20 | ENCAPS | – | m ‖ ek（1216 B） | cycles（u32） |
| 0x30 | DECRYPT | – | – | cycles（u32） |
| 0x40 | READ_POLY | core, slot | – | 512 B |
| 0x41 | WRITE_POLY | core, slot | 512 B | – |
| 0x50 | READ_SECRET | core | – | 32 B（core 0 返回 rho，core 1 返回 K） |

core 编号：0 = keygen，1 = encaps，2 = decaps。

状态码：

| 状态码 | 含义 |
|--------|------|
| `00` | 成功 |
| `01` | 未知命令 |
| `02` | 核心不存在 |
| `03` | 参数非法 |
| `04` | 核心忙 |
| `05` | 核心超时（核心已被复位） |
| `06` | 接收溢出（核心已被复位） |

一条请求如果中途停顿超过 100 ms，FPGA 会丢弃它、不做响应，并复位各个核心。

协议的完整定义见 `rtl/uart_host.v` 文件头注释，Python 驱动见 `scripts/kyber_uart.py`。在自己的代码里直接调用驱动的写法：

```python
import sys; sys.path.insert(0, "scripts")
from kyber_uart import KyberUart

dev = KyberUart.open("/dev/cu.usbserial-41032807F7C11")
print(dev.ping())                                    # (version, caps)
ek, dk, cycles = dev.ml_kem_keygen(d=bytes(32), z=bytes(32))
K, c, cycles   = dev.ml_kem_encaps(ek, m=bytes(32))
K2, cycles     = dev.ml_kem_decaps(dk, c)            # 需要 encaps 和 decaps 两个核心
assert K == K2
dev.close()
```
