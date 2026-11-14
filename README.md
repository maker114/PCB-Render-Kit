# PCB-Render-Kit

把「立创EDA / 嘉立创EDA 专业版」的板子，变成照片级 3D 效果图。两件配套工具：

| 目录 | 是什么 |
|---|---|
| `skill/pcb-render/` | **渲染技能**（Agent Skills 格式）。一条命令把 EDA 导出包渲染成 2K/4K 主图、俯视图、微距 |
| `eda-extension/` | **立创EDA 扩展** `pcb-render-kit`。在 PCB 编辑器里一键把渲染所需文件导出成一个 ZIP |
| `dist/` | 已构建的扩展包 `pcb-render-kit_v1.0.0.eext`，可直接导入 EDA |
| `scripts/` | 安装技能 / 重新构建扩展的辅助脚本 |

## 工作流

```
立创EDA PCB 编辑器
   └─[扩展：渲染素材包]→ xxx_渲染素材包.zip   (STEP 3D + DXF + Gerber + BOM + 贴片坐标 + 网表)
            │
            └─[skill/pcb-render/pipeline.py]→ render/out/final/01_hero.png 02_top.png 03_macro.png
```

---

## 一、安装渲染技能

技能目录本就是 DSH / Claude Code 通用的 **Agent Skills** 结构（`SKILL.md` + `scripts/` + `references/`）。

**本机（已装好，用 junction 直连本仓库）**

```powershell
# 把仓库里的 skill 挂到 DSH 技能目录；改仓库 = 立即生效，不会两份漂移
cmd /c mklink /J "%USERPROFILE%\.dsh\skills\pcb-render" "E:\PCB-Render-Kit\skill\pcb-render"
```

**其他机器（普通复制即可）**

```powershell
powershell -File scripts\install-skill.ps1 -Copy
# 或手动：
Copy-Item -Recurse -Force .\skill\pcb-render "$env:USERPROFILE\.dsh\skills\pcb-render"
```

一键脚本两种模式（默认 junction，`-Copy` 强制复制）：

```powershell
powershell -File scripts\install-skill.ps1
```

**验证**：新开会话里应能看到 `pcb-render` 技能；或直接看 DSH 技能目录是否可读 `SKILL.md`。
（DSH 由 `skill-filesystem` 插件扫描 `~/.dsh/skills`、`<项目>/.dsh/skills` 等根目录，带文件监听，改完自动热加载。）

**用法**

```
对话里：「用 pcb-render 把这个包渲成 2K：<路径>.zip」
命令行：
  python skill\pcb-render\scripts\pipeline.py --package <zip> --workdir <目录> --resolution 2k
```

详细参数、构图速查、故障对照见 `skill/pcb-render/SKILL.md` 与 `references/`。

---

## 二、安装 EDA 扩展

1. 打开 **嘉立创EDA（专业版）**，打开任意 PCB 工程
2. 顶部菜单：`高级` → `扩展管理器...` → `导入`
3. 选择 `dist\pcb-render-kit_v1.0.0.eext`
4. 在扩展管理器里确认状态为**启用**（必要时重启 EDA）
5. 打开 PCB 编辑器，顶部菜单出现 **「渲染素材包」** 即安装成功

> 首次导入第三方扩展时 EDA 会提示「未知来源」，属正常现象（本地扩展未签名、未上架）。

菜单项：

| 菜单项 | 作用 |
|---|---|
| 一键导出全部（打包为 ZIP） | STEP / DXF / Gerber / BOM / 贴片坐标 / 网表 全部导出并打成一个 ZIP，只弹一次保存框 |
| 仅导出 STEP 3D 模型 | `PCB1_3D.step`（装配体，含元件 3D 模型） |
| 仅导出 DXF 图形层 | `PCB1_DXF.dxf`（丝印 / 阻焊 / 铜层 / 板框） |
| 仅导出 Gerber 制版文件 | `PCB1_Gerber.zip` |
| 仅导出 BOM 物料表 | `PCB1_BOM.xlsx` |
| 仅导出贴片坐标 | `PCB1_PickAndPlace.xlsx` |
| 仅导出网表 | `PCB1_Netlist.tel` |

导出文件名前缀由 `eda-extension/pcb-render-kit/src/index.ts` 顶部的 `BASE_NAME` 控制。

---

## 三、重新构建扩展

```powershell
powershell -File scripts\build-eext.ps1
# 内部等价于：
#   cd eda-extension\pcb-render-kit
#   npm install && npm run build        →  build\dist\pcb-render-kit_v<版本>.eext
#   再把产物复制到仓库 dist\
```

改代码后记得递增 `eda-extension/pcb-render-kit/extension.json` 里的 `version`，否则 EDA 可能视为同版本不更新。

**参考仓库**（本地克隆，已 gitignore，构建不需要它们）：

| 目录 | 来源 |
|---|---|
| `eda-extension/sdk/` | <https://github.com/easyeda/pro-api-sdk.git> |
| `eda-extension/demo/` | <https://github.com/easyeda/eext-extension-demo.git> |

需要时重新拉取：

```powershell
cd eda-extension
git clone --depth 1 https://github.com/easyeda/pro-api-sdk.git sdk
git clone --depth 1 https://github.com/easyeda/eext-extension-demo.git demo
```

扩展用到的官方 API 清单（STEP / DXF / Gerber / BOM / PnP / 网表 / 保存 / 菜单注册）见
`eda-extension/README.md` 的「用到的官方 API」一节。

---

## 四、环境要求

| 用途 | 要求 |
|---|---|
| 渲染 | Blender **5.2 LTS**（Steam 版亦可）；GPU 可选（有 OptiX 用 GPU，否则 CPU 慢但能出） |
| 渲染脚本 | Python 3.12+，依赖 `cascadio`、`ezdxf`、`numpy`、`Pillow` |
| 构建扩展 | Node.js 20+、npm（国内可设 registry 为 npmmirror） |
| EDA | 立创EDA 专业版 3.2+（`engines.eda: ^3.2.0`） |

Blender 与 Python 的具体路径在 `skill/pcb-render/SKILL.md` 的「环境探测」一节，换机器时改那里。

---

## 五、仓库约定

**被跟踪**：技能全部内容、扩展源码（`src/`、`extension.json`、`package.json`、`build/`、`config/`、`locales/`、`iframe/`、图标、文档）、发布产物 `dist/*.eext`。

**被忽略**：`node_modules/`、扩展的构建中间产物、`sdk/`、`demo/`、渲染运行时的 `.blend` 与出图。

**许可**：扩展工程沿用模板的 Apache-2.0（见 `eda-extension/pcb-render-kit/LICENSE`）。
