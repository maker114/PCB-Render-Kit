# 分色与材质

目标：**每个元件有自己的物理材质，板面有蓝油/金盘/丝印/走线**，而不是 STEP 里那 33 种平涂色。

## 数据来源

STEP 导出的每个面都带颜色（`COLOUR_RGB`）。颜色本身是 EDA 的"图例色"，不是真实材质：

| STEP 颜色 | 实际含义（多数板） | 映射到 |
|---|---|---|
| `#977600` `#A58400` `#F2C22E` `#D4AB21` | 焊盘/引脚镀金、排针 | `gold` |
| `#C0C0C0` `#C4C4C4` `#AAAAAA` `#D9D9D9` | 引脚/端子镀锡 | `tin` |
| `#DADADA` `#FFFFFF`（电容） | MLCC 陶瓷本体 | `ceramic` / `ceramic_light` |
| `#000000` `#1A1A1A` `#393939` | 电阻/IC 黑色环氧 | `epoxy_black` |
| `#404040` `#4C4C4C` | SOT/QFN 本体 | `epoxy_darkgray` |
| `#A49D99` `#A59E96` `#C7C2BD` `#D7D0C0` | 塑料/陶瓷外壳 | `plastic_beige` |
| `#FFFFFF` `#FFFBF0` `#F7F7F7` | 连接器/开关白塑料 | `plastic_white` |
| `#FFFF00` `#00FF00` `#FF0000` `#1776FF` | LED 芯片/透明罩 | `led_yellow` / `led_green` / `led_red` / `plastic_blue` |
| `#008DBF` | STEP 里的铜层视觉色 | （铜层不参与，直接用贴图） |
| `#EFEFA6` | FR4 板边 | `fr4_edge` |
| `#356A9E` | 板面顶/底（阻焊） | （用 `board_top` 节点树，不映射） |

## 映射表结构（scripts/materials.py）

```python
FOOTPRINT_MAP = {
    "C0603": {"#977600": "gold", "#DADADA": "ceramic", "#FFFFFF": "ceramic_light"},
    "R0603": {"#000000": "epoxy_black", "#C0C0C0": "tin", "#FFFFFF": "mark_white"},
    ...
}
HEX_FALLBACK = {"#977600": "gold", ...}   # 全局兜底，未知封装的已知颜色
```

查找顺序：`FOOTPRINT_MAP[封装][颜色]` → `HEX_FALLBACK[颜色]` → `"plastic_beige"`。

封装名 = GLB 里 mesh 名字 `~` 之前的部分，例如 `SOT-23-5_L3.0-W1.7-P0.95-LS2.8-BL`。**精确匹配**，所以每个封装变体（`SOT-23-3` / `SOT-23-5` / `SOT-23-6`）都要单独列。

## 加新封装的流程（每块新板 1 分钟）

1. `python scripts/recon_footprints.py --glb <render>/data/board.glb`
2. 输出里每个 `== 封装名` 下面列出颜色 + 三角面数
3. 按面数最多的几个颜色判断本体/引脚，照上面颜色表映射
4. 写进 `FOOTPRINT_MAP`（新条目加注释 `# ---- board #N: 板子描述 ----`）
5. 新颜色（表里没有的）顺手加进 `HEX_FALLBACK`
6. 重跑 `build_board.py` 并渲一张 800 px 验证

**判断技巧**：三角面数最多的颜色通常是本体或引脚阵列；面数很少（个位数）的多是 pin1 标记、丝印字。拿不准就先渲一张看看，再调。

## 调色板（PALETTE）

| 名称 | 用途 | 关键参数 |
|---|---|---|
| `gold` | 镀金焊盘/引脚、排针 | metallic 1.0, rough 0.22 |
| `cap_term` | **MLCC 端头（哑光）** | metallic 0.25, rough 0.72 |
| `tin` / `tin_dull` | 镀锡/不锈钢 | metallic 1.0, rough 0.28 / 0.40 |
| `ceramic` / `ceramic_light` | MLCC 本体 | rough 0.32, coat 0.55 |
| `epoxy_black` / `epoxy_darkgray` | IC/电阻本体 | rough 0.36 / 0.40, coat 0.2 |
| `plastic_white` / `plastic_beige` / `plastic_dark` / `plastic_blue` | 塑料件 | rough 0.38~0.46 |
| `shield_metal` | ESP32 屏蔽罩 | metallic 0.9, rough 0.42 |
| `led_red` / `led_yellow` / `led_green` | LED | 带 emission，strength 1.6~2.2 |
| `fr4_edge` | 板边 FR4 | rough 0.60 |
| `silk_white` | 丝印 | rough 0.50 |

**MLCC 端头不要用 `gold`**：mlcc 的端电极是镀锡/镍的哑光面，用抛光金（metallic 1.0 / rough 0.22）会在侧面拉出一条镜面高光，看着像黄铜块。`C0402/C0603/C0805` 的 `#977600` 现已映射到 `cap_term`（metallic 0.25 / rough 0.72）。焊盘本身仍然是 `gold`（真实 ENIG 是亮的），两者要区分开。

调整原则：**先保色相，再调质感**。色相来自 STEP（保证"分色"正确），metallic/roughness 才是"像不像实物"的部分。

## 板面材质（`build_board_materials`）

节点树按顺序叠：`阻焊(FR4) → 阻焊(铜上) → 裸 FR4 光环 → 金焊盘 → 丝印 → 钻孔`。

| 输入贴图 | 作用 |
|---|---|
| `copper_top.png` | 铜在不在 → 决定阻焊色（`#093C66` vs `#1C74C8`）+ 25 µm 微起伏（bump 0.35） |
| `mask_top.png` | 开窗 → 与 copper 相乘得到金焊盘，其余是裸 FR4 环 |
| `silk_top.png` | 白色丝印 |
| `drill.png` | 钻孔 → 覆盖成暗色（通孔/安装孔） |

粗糙度按同样顺序插值：阻焊 0.45/0.38 → 裸 FR4 0.62 → 金 0.20 → 丝印 0.55 → 钻孔 0.85。

> 阻焊**故意做成偏哑光**（不是镜面）：镜面会在斜视角制造大片高光，把蓝色冲成惨白，颜色分不清。
