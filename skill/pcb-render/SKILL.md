---
name: pcb-render
description: "把立创EDA/嘉立创EDA（EasyEDA）专业版导出的 PCB 素材包渲染成照片级 3D 效果图（Blender Cycles）：STEP 3D + DXF 图形层 → 每个元件独立分色的 PBR 材质、蓝色阻焊 + 金色焊盘 + 白色丝印 + 铜箔走线，输出 2K/4K 主图、俯视图、微距。Use when the user asks to render a PCB / circuit board / product shot / 电路板效果图 / 板子 3D 图 from an EDA export package (.zip containing *.step + *.dxf), or asks to re-render an existing board at another angle/resolution, or asks to add a macro/bottom view. 中文触发：渲染 PCB、电路板效果图、板子 3D、出图、换角度重渲、加微距、导出的素材包渲染。"
compatibility: "Windows. Blender 5.2 LTS (Steam install supported). Python 3.12 with numpy / Pillow / ezdxf / cascadio. GPU optional (OptiX used when available, CPU otherwise). All scripts are self-contained in scripts/."
allowed-tools: "Bash, Read, Write, Edit"
---

# pcb-render

把「立创EDA 专业版一键导出的素材包」变成成品效果图。整条链路的脚本都在 `scripts/`，本文件只做路由与纪律；细节按需读 `references/`。

## 触发条件

- 用户给了 EDA 导出的 zip（内含 `*_3D.step`、`*_DXF.dxf`、BOM、PickAndPlace），要出图
- 用户说「再渲一版」「换个角度」「来张微距」「放大到 4K」「上次那块板」
- 用户在聊 PCB 渲染、板子 3D、产品图

## 铁律（每一条都是踩坑换来的，违反必翻车）

1. **STEP 优先，禁止用 DXF 重绘已存在的层。**
   STEP 里有 `TopCopper` / `TopSilkscreen` 等产品时，铜层和丝印**必须**从 STEP 几何投影（`layers_from_step.py`）。
   DXF 只补 STEP 没有的层：**阻焊开窗、钻孔、板框**。
   - 反例 1：用 DXF 重绘铜层 → 铺铜的隔离带被填成实心（覆盖率 97%），走线全消失
   - 反例 2：用 DXF TEXT + 替代字体重绘丝印 → 字宽不一致，4~6 个字符偏移 0.3~0.5 mm
2. **审计先行。** `audit_assets.py` 非 0 退出就**停止**，不许渲染。它查覆盖率/连通块，能抓住"实心填充""空层""单连通域"。
3. **单 GPU 队列。** 禁止同时跑两个 Blender OptiX 渲染（6 GB 显存互抢，实测 85 s → 696 s）。
4. **不许覆盖。** 验证图进 `render/out/diag/`，`render/out/final/` 只写一次；重渲前确认目标分辨率。
5. **交付前 1:1 抽查。** 至少看 3 处 100% 裁切：焊盘、走线、丝印位号。缩略图不算验收。
6. **每换一块板就补齐材质映射。** `recon_footprints.py` 打印封装 + 颜色，补进 `materials.py` 的 `FOOTPRINT_MAP`。

## 环境探测（每次开工先做）

| 项 | 位置 |
|---|---|
| Blender | `E:\SteamLibrary\steamapps\common\Blender\blender.exe`（Steam 版），否则在 `C:\Program Files\Blender Foundation` 找 |
| Python | `C:\Users\lenovo\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe` |
| 依赖 | `pip install cascadio ezdxf`；numpy/Pillow 自带 |

**所有 Blender 调用都要带 `--factory-startup`。** 否则会加载用户偏好（中文界面），新建节点被本地化成「背景」，`studio.py` 之类按名字取节点的地方会炸。本仓库的 `studio.py` 已改成按类型取节点，但仍建议加该参数（还能避开不兼容插件的报错）。

## 主流程

一条命令跑完（推荐，约 40 s 出 preview / 3–5 min 出 2K）：

```powershell
& $py "<skill>\scripts\pipeline.py" `
    --package "E:\...\PCB1_渲染素材包2.zip" `
    --workdir "E:\...\PCB4" `
    --resolution preview        # preview | 2k | 4k
# 可选：--views hero,top,macro  --samples 160  --skip-render
#       --macro-target 0.010,-0.010,0.0016  --macro-distance 0.062
```

它依次做：解包 → STEP→GLB → DXF 只出 mask/drill（并写出 align.json）→ STEP 出 copper/silk →
**审计门（不通过直接退出，不渲染）** → 打印封装清单 → build_board → 渲染 3 张 → 转 8bit/jpg。
分步命令与每步产物见 `references/pipeline.md`。

跑完必做：

1. **看图**（`render/out/final/`）→ 构图/材质/走线是否正常
2. **1:1 裁切抽查**（Python 裁 3 处，逐张 Read 确认）
3. **转 8-bit**：16 位母版留 `*_16bit.png`，主文件名给 8-bit，另存 q95 jpg
4. **写 README.md** 到交付目录：输入包、板子型号、尺寸、每张图的内容/尺寸、审计数字、复现命令
5. 用 `present` 交付 3 张 + README

## 构图与参数速查

| 板型 | 主图 | 俯视 | 微距 |
|---|---|---|---|
| 宽板（100×65） | 16:9 `3840x2160` | 竖/横按板 | `2560x1600` |
| 方板（36×36） | **1:1** `2048x2048`（2K）/ `3840x3840`（4K） | 1:1 | `2400x1680` |
| 长条 | 4:5 或 16:9 | 竖构图 | — |

已验证的相机/灯光（不要每次重扫）：

- 3/4 主图：`--framing three_quarter --azimuth -32 --elevation 38 --margin 0.0~0.02`
- 俯视：`--framing top --azimuth 0 --elevation 84 --margin 0.05 --exposure -0.2`
- 微距：`--framing three_quarter --target X,Y,0.0016 --lens 100 --distance 0.05~0.07`
  （**必须用 `--distance`**：studio 的 detail 取景距离偏大 2~3 倍）
- 灯光一律 `--light pcb_hero --floor black --exposure -0.8`（俯视 -0.2）
- 采样：2K 128~160、4K 200；降噪 OIDN

## 故障对照表

| 症状 | 原因 | 处理 |
|---|---|---|
| 走线看不见 / 板子一片纯蓝 | 铜层用 DXF 重绘被填实 | 跑 `layers_from_step.py` 重新用 STEP 投影 |
| 丝印文字偏移、越长的标签偏得越多 | DXF 用替代字体重绘 | 同上，丝印也改用 STEP |
| `KeyError: 'Background'/'背景'` | 没加 `--factory-startup`，中文界面节点本地化 | 加 `--factory-startup` |
| 微距出来还是整板 | studio detail 自动距离偏大 | 加 `--distance 0.05~0.07` |
| 渲染突然变慢 5~10 倍 | 两个 OptiX 渲染并发 | 串行，一次只跑一个 |
| 模组丝印上下颠倒 | 器件旋转 180°（设计如此） | 核对 PickAndPlace 的 `Rotation`，不是 bug |
| 通孔/安装孔变成金圆盘 | 金盘用「开窗」而不是「开窗∧铜」 | 已实现 `mask ∧ copper`，检查铜层贴图是否为空 |
| 焊盘比器件引脚大一圈 | 蒙版扩张（mask expansion） | 正常现象，实物也是开窗略大于焊盘 |
| 板子贴图整体错位 | align.json 的板框与 UV 不一致 | 确认 `build_board.py` 读的是同一份 align.json |

## 参考文件

| 什么时候读 | 文件 | 内容 |
|---|---|---|
| 要分步执行 / 搞不清每步产物 | `references/pipeline.md` | 命令、目录结构、每步输入输出、审计含义 |
| 出问题要对照前因后果 | `references/pitfalls.md` | 5 次事故的数据、根因、检测手段 |
| 新板子分色不对 / 要加封装 | `references/materials.md` | 调色板、FOOTPRINT_MAP 结构、补映射流程 |
| 调构图、灯光、微距目标点 | `references/lighting-camera.md` | studio 预设、aspect 决策、微距坐标换算 |



## 安装（DSH 到底从哪扫）

DSH 由 `skill-filesystem` 插件按以下根目录发现 skill（rank 越小优先级越高）：

| 范围 | 路径 | rank |
|---|---|---|
| 项目 | `<项目根>/.dsh/skills` | 100 |
| 项目 | `<项目根>/.agents/skills` | 200 |
| 自定义 | `customSkillDirs` 配置（cordis.patch.yml 里 `skill-filesystem` 的 config） | 300 |
| 用户 | `C:\Users\lenovo\.dsh\skills`（= dshHome/skills） | 400 |
| 用户 | `~/.agents/skills` | 500 |

一个 skill = 一个目录 + `SKILL.md`。frontmatter **必须有** `name`（kebab-case，正则 `^[a-z0-9]+(?:-[a-z0-9]+)*$`）和 `description`；
其余字段（`compatibility` / `allowed-tools`）会被忽略或收进 metadata，不会报错。
想禁止模型自动触发可加 `disable-model-invocation: true`。

```powershell
# 本机：仓库通过「目录联接」挂到 DSH 技能目录 —— 改仓库立即生效，只有一份内容
cmd /c mklink /J "%USERPROFILE%\.dsh\skills\pcb-render" "E:\PCB-Render-Kit\skill\pcb-render"

# 其他机器：普通复制即可
Copy-Item -Recurse -Force "E:\PCB-Render-Kit\skill\pcb-render" "$env:USERPROFILE\.dsh\skills\pcb-render"

# 或直接用仓库脚本（默认 junction，-Copy 走复制）
powershell -File "E:\PCB-Render-Kit\scripts\install-skill.ps1"
```

**坑**：`E:\DeepseekHarnessBeta\resources\runtime\office-skills\` **不是**通用扫描目录 ——
它是 office 插件自己的 `assetRoot`，塞进去不会被发现（踩过，白折腾一轮）。
改完 skill 会自动热加载（provider 带 chokidar watcher），新会话必然可见。
