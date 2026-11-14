# 立创EDA 扩展：`pcb-render-kit`

在 **立创EDA（专业版）** 的 PCB 编辑器顶部菜单加一个 **「渲染素材包」**，
一键把渲染/存档需要的文件全部导出，并打包成一个 ZIP。

> 这个扩展是 [PCB-Render-Kit](../../README.md) 的一部分。装好它，导出素材包只要点一下。
> 不装也能用：手动导出 STEP 和 DXF 一样可以渲染。

## 安装（一次性，GUI 操作）

1. 打开 **立创EDA（专业版）**，打开任意 PCB 工程
2. 顶部菜单：`高级` → `扩展管理器...` → `导入`
3. 选择构建好的 `pcb-render-kit_v1.0.0.eext`（仓库根目录 `dist/` 下有现成的）
4. 在扩展管理器里确认它是**启用**状态（必要时重启一次 EDA）
5. 打开 PCB 编辑器，顶部菜单出现 **「渲染素材包」** 即安装成功

> 首次导入第三方扩展时，EDA 可能提示「未知来源」，属于正常现象（扩展未上架商店、未签名）。

## 用法

PCB 编辑器 → 顶部菜单 **渲染素材包**：

| 菜单项 | 作用 |
|---|---|
| **一键导出全部（打包为 ZIP）** | 依次导出 STEP / DXF / Gerber / BOM / 贴片坐标 / 网表，打成一个 ZIP，**只弹一次保存框** |
| 仅导出 STEP 3D 模型 | `PCB1_3D.step`（装配体模式，含元件 3D 模型） |
| 仅导出 DXF 图形层 | `PCB1_DXF.dxf`（丝印 / 阻焊 / 铜层 / 板框） |
| 仅导出 Gerber 制版文件 | `PCB1_Gerber.zip` |
| 仅导出 BOM 物料表 | `PCB1_BOM.xlsx` |
| 仅导出贴片坐标 | `PCB1_PickAndPlace.xlsx` |
| 仅导出网表 | `PCB1_Netlist.tel` |
| 关于… | 版本信息 |

ZIP 里还会附带一个 `README.txt`，写明每个文件的用途。

## 修改导出文件名前缀

打开 `pcb-render-kit/src/index.ts`，改第一行的常量：

```ts
const BASE_NAME = 'PCB1';
```

## 重新构建

```powershell
# 仓库根目录执行（推荐）
powershell -File scripts\build-eext.ps1

# 或者手动
cd eda-extension\pcb-render-kit
npm install
npm run build
# 产物：build\dist\pcb-render-kit_v1.0.0.eext

# 类型检查（可选）
npx tsc --noEmit
```

构建后记得把 `extension.json` 里的 `version` 递增，否则导入时可能被视为同版本而不更新。
`scripts/build-eext.ps1` 会自动把产物复制到仓库根的 `dist/`。

## 用到的官方 API

类型定义都在本机 `node_modules/@jlceda/pro-api-types` 里。

| 功能 | API |
|---|---|
| 3D / STEP | `eda.pcb_ManufactureData.get3DFile(name, 'step', ['Component Model','Via','Silkscreen','Wire In Signal Layer'], 'Outfit', true)` |
| DXF | `eda.pcb_ManufactureData.getDxfFile(name)` |
| Gerber | `eda.pcb_ManufactureData.getGerberFile(name)` |
| BOM | `eda.pcb_ManufactureData.getBomFile(name, 'xlsx')` |
| 贴片坐标 | `eda.pcb_ManufactureData.getPickAndPlaceFile(name, 'xlsx')` |
| 网表 | `eda.pcb_ManufactureData.getNetlistFile(name)` |
| 保存文件 | `eda.sys_FileSystem.saveFile(file, fileName)` |
| 菜单注册 | `extension.json` → `headerMenus.pcb` + `registerFn` |

## 已知限制

- 扩展只读制造数据，不修改工程。
- `getDxfFile` 不传 `layers` 时的行为取决于 EDA 实现；若它要求必须传层，会走 catch 分支报错，其余文件仍会正常导出（单项失败不中断整包）。
- STEP 里能否出现某个元件，取决于该器件是否**绑定了 STEP 格式**的 3D 模型（导入 OBJ 的模型不会出现在 STEP 里）——这是 EDA 的限制。
- 没有在 EDA 图形界面里自动化实测点击流程（只用官方类型定义做了静态校验，`tsc --noEmit` 无报错）。若某个菜单项点了没反应，看 `高级 → 扩展管理器 → 该扩展 → 日志/控制台`。
