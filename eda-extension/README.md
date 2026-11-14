# PCB 渲染素材一键导出（立创EDA专业版扩展）

给 `E:\DSH WorkSpace\PCB` 这块板子做的辅助扩展：在 **PCB 编辑器**顶部菜单加一个「渲染素材包」，
一次把渲染/存档需要的文件全部导出并打包成一个 ZIP。

## 安装（一次性，GUI 操作）

1. 打开 **嘉立创EDA(专业版)**，打开任意工程（建议打开「毕业设计」的 PCB）
2. 顶部菜单：`高级` → `扩展管理器...` → `导入`
3. 选择：`E:\DSH WorkSpace\PCB\pcb-render-kit_v1.0.0.eext`
4. 导入后在扩展管理器里确认它是 **启用** 状态（可能需要重启一次 EDA）
5. 打开 PCB 编辑器，顶部菜单出现 **「渲染素材包」** 即安装成功

> 首次导入第三方扩展时 EDA 可能提示「未知来源」，属于正常现象（扩展未上架商店、未签名）。

## 用法

PCB 编辑器 → 顶部菜单 **渲染素材包**：

| 菜单项 | 作用 |
|---|---|
| **一键导出全部（打包为 ZIP）** | 依次导出 STEP / DXF / Gerber / BOM / 贴片坐标 / 网表，打进一个 ZIP，**只弹一次保存框** |
| 仅导出 STEP 3D 模型 | `PCB1_3D.step`（装配体模式，含元件 3D 模型，等同我渲染用的那个 STEP） |
| 仅导出 DXF 图形层 | `PCB1_DXF.dxf`（丝印/阻焊/铜层/板框） |
| 仅导出 Gerber 制版文件 | `PCB1_Gerber.zip` |
| 仅导出 BOM 物料表 | `PCB1_BOM.xlsx` |
| 仅导出贴片坐标 | `PCB1_PickAndPlace.xlsx` |
| 仅导出网表 | `PCB1_Netlist.tel` |
| 关于… | 版本信息 |

ZIP 里还会附带一个 `README.txt`，写明每个文件的用途，方便直接丢给渲染/打样/文档流程。

## 改文件名前缀

打开 `pcb-render-kit\src\index.ts`，改第一行的常量：

```ts
const BASE_NAME = 'PCB1';
```

## 重新构建

```powershell
cd "E:\DSH WorkSpace\PCB\eda-ext\pcb-render-kit"
$env:npm_config_registry = "https://registry.npmmirror.com"
& "E:\nodejs\npm.cmd" install --no-audit --no-fund
& "E:\nodejs\npm.cmd" run build
# 产物：build\dist\pcb-render-kit_v1.0.0.eext
# 类型检查（可选）：
& "E:\nodejs\node.exe" .\node_modules\typescript\bin\tsc --noEmit
```

修改后记得把 `extension.json` 的 `version` 递增，否则导入时可能被当成同版本不更新。

## 用到的官方 API（都在本机 `@jlceda/pro-api-types` 里有类型定义）

| 功能 | API |
|---|---|
| 3D / STEP | `eda.pcb_ManufactureData.get3DFile(name, 'step', ['Component Model','Via','Silkscreen','Wire In Signal Layer'], 'Outfit', true)` |
| DXF | `eda.pcb_ManufactureData.getDxfFile(name)` |
| Gerber | `eda.pcb_ManufactureData.getGerberFile(name)` |
| BOM | `eda.pcb_ManufactureData.getBomFile(name, 'xlsx')` |
| 贴片坐标 | `eda.pcb_ManufactureData.getPickAndPlaceFile(name, 'xlsx')` |
| 网表 | `eda.pcb_ManufactureData.getNetlistFile(name)` |
| 保存 | `eda.sys_FileSystem.saveFile(file, fileName)` |
| 菜单 | `extension.json` → `headerMenus.pcb` + `registerFn` |
| 提示框 | `eda.sys_Dialog.showInformationMessage(...)` |

## 已知限制

- 我无法在 EDA 的图形界面里实测点击流程（只能用官方类型定义做静态校验，`tsc --noEmit` 零错误）。
  如果某个菜单项点了没反应，请看 `高级 → 扩展管理器 → 该扩展 → 日志/控制台`。
- `getDxfFile` 在不传 `layers` 时的行为取决于 EDA 实现：如果它要求必须传层，会走 catch 分支报错，其余文件仍会正常导出（单项失败不会中断整包）。
- STEP 里能出现的元件模型，取决于器件是否**以 STEP 格式**绑定了 3D 模型（导入 OBJ 的模型不会出现在 STEP 里）——这是 EDA 的限制，不是扩展的。
- 扩展只读制造数据，不修改工程。
