# 分步执行

## 目录约定

```
<WorkDir>/                      # 一块板一个目录（任意位置，例如 ~/pcb/myboard）
  PCB1_3D.step                  # 素材包解出来的原件（只读，不要改）
  PCB1_DXF.dxf
  PCB1_BOM.xlsx
  PCB1_PickAndPlace.xlsx
  render/
    data/board.glb              # STEP 转出来的 GLB（中间产物）
    tex/                        # 贴图 + align.json
      silk_top.png  silk_bottom.png  silk_bottom_viewed_from_bottom.png
      copper_top.png  copper_bottom.png
      mask_top.png  mask_bottom.png  drill.png  outline.png
      align.json                # 板框、UV 公式、每张贴图的 px/mm —— 唯一的对位真源
      scripts/                  # build_textures.py / audit_assets.py / compare_silk.py（可选副本）
    blend/board.blend           # 建好的场景（几何 + 材质 + UV）
    out/diag/                   # 验证图、审计联络图、裁切抽查 —— 随便写
    out/final/                  # 交付图 —— 只写一次
```

## 逐步命令

设好变量：

```powershell
$py     = "python"                                   # 任意装了 requirements.txt 的 Python 3.10+
$sc     = "<Skill>\scripts"
$bl     = & $py "$sc\env.py" --print-blender          # 自动探测；也可 --blender 手动指定
$work   = "<WorkDir>"
$render = "$work\render"
$tex    = "$render\tex"
$glb    = "$render\data\board.glb"
$blend  = "$render\blend\board.blend"
```

### 1. 解包

```powershell
& $py -c "import zipfile;zipfile.ZipFile(r'<zip>').extractall(r'$work')"
```

预期 6~7 个文件：`*_3D.step`、`*_DXF.dxf`、`*_Gerber.zip`、`*_BOM.xlsx`、`*_PickAndPlace.xlsx`、`*_Netlist.tel`、`README.txt`。

> 没有 zip？让用户用 `pcb-render-kit` 扩展导出（本仓库同名扩展，一键出这 6 个文件）。

### 2. STEP → GLB（cascadio，~10–20 s）

```powershell
& $py -c @"
import cascadio
cascadio.step_to_glb(r'$work\PCB1_3D.step', r'$glb', tol_linear=0.05, tol_angular=0.3,
                     merge_primitives=True, use_parallel=True, include_materials=True)
"@
```

输出：每个元件一个 mesh、每面保留 STEP 原色（33 色左右）、节点矩阵带位置。
`RWGltf_CafWriter skipped node 'TopFpcStiffener...'` 这类警告可以忽略。

### 3. DXF → 贴图（只出 STEP 没有的层）

```powershell
& $py "$sc\build_textures.py" --dxf "$work\PCB1_DXF.dxf" --outdir $tex
```

会生成 **全部** 层（silk/copper/mask/drill/outline…）。其中 silk/copper 马上会被第 4 步覆盖——这是故意的：
`build_textures.py` 先跑是为了产出 `align.json`（板框 + px/mm），第 4 步需要它。

### 4. STEP → 贴图（铜层 + 丝印，权威源）

```powershell
& $py "$sc\layers_from_step.py" --tex $tex --glb $glb
```

对 `TopCopper / BottomCopper / TopSilkscreen / BottomSilkscreen` 做三角面投影（默认 100 px/mm，2× 超采样）。
被覆盖的 DXF 版本自动存为 `*.png.dxf-backup`。
如果某层在 STEP 里不存在，会打印 `NOT in this STEP export, skipped` —— 这时才保留 DXF 版本，**并在交付说明里注明"该层为二手重建"**。

### 5. 审计（硬门）

```powershell
& $py "$sc\audit_assets.py" --tex $tex
if ($LASTEXITCODE -ne 0) { throw "不要渲染" }
```

判定规则：

| 条件 | 判定 |
|---|---|
| 覆盖率 > 90% | `SOLID-FILL?` —— 铺铜隔离带没挖（就是那次走线消失的 bug） |
| 覆盖率 < 0.4% | `near-empty / wrong layer?` |
| 铜层连通块 ≤ 2 且覆盖 > 50% | `single blob -> clearances probably not punched` |

正常范围参考：铜 60~90%、丝印 5~12%、开窗 10~20%、钻孔 1~3%。

### 6. 补材质映射（每块新板都要看）

```powershell
& $py "$sc\recon_footprints.py" --glb $glb
```

输出 `footprint -> {颜色: 三角面数}`。对照 `materials.py` 的 `FOOTPRINT_MAP`：
- 全部命中 → 直接下一步
- 有缺 → 按 `references/materials.md` 的规则补（颜色→语义材质），重跑本步确认

### 7. 建场景

```powershell
& $bl -b --factory-startup -P "$sc\build_board.py" -- `
    --glb $glb --tex $tex --blend $blend `
    --rough "0.45,0.38" --spec 0.3 --coat 0.06
```

- 板框从 `tex/align.json` 的 `board_bbox_mm` 读，UV 自动对位（不要硬编码板尺寸）
- `TopCopper / BottomCopper / TopSilkscreen / BottomSilkscreen` 四个"视觉层"对象会被跳过（`VISUAL_LAYER_PRODUCTS`），因为已经用贴图表达
- `--rough/--spec/--coat` 是阻焊表面的粗糙度/高光/清漆，默认值已验证，不要随意动

### 8. 渲染

```powershell
# 主图
& $bl -b --factory-startup $blend -P "$sc\render_view.py" -- `
    --framing three_quarter --azimuth -32 --elevation 38 --margin 0.0 `
    --res 2048x2048 --samples 160 --exposure -0.8 --light pcb_hero --floor black `
    --out "$render\out\final\01_hero_16bit.png"

# 俯视
& $bl -b --factory-startup $blend -P "$sc\render_view.py" -- `
    --framing top --azimuth 0 --elevation 84 --margin 0.05 `
    --res 2048x2048 --samples 128 --exposure -0.2 --light pcb_hero --floor black `
    --out "$render\out\final\02_top_16bit.png"

# 微距
& $bl -b --factory-startup $blend -P "$sc\render_view.py" -- `
    --framing three_quarter --target "0.010,-0.010,0.0016" --azimuth -32 --elevation 44 `
    --lens 100 --distance 0.062 --margin 0.05 `
    --res 2400x1680 --samples 128 --exposure -0.8 --light pcb_hero --floor black `
    --out "$render\out\final\03_macro_16bit.png"
```

`render_view.py` 会自动从场景里算 bbox，`--target` 是**场景坐标（米）**，换算见 `references/lighting-camera.md`。

### 9. 收尾

```powershell
# 16bit 母版 → 8bit 主文件 + jpg
& $py -c @"
from PIL import Image
import os
d = r'$render\out\final'
for n in ('01_hero','02_top','03_macro'):
    im = Image.open(os.path.join(d, n+'_16bit.png'))
    im.convert('RGB').save(os.path.join(d, n+'.png'), optimize=True)
    im.convert('RGB').save(os.path.join(d, n+'.jpg'), quality=95, subsampling=0)
"@
```

然后 1:1 裁切抽查 3 处，再写 `out/final/README.md`（模板见交付过的任一板）。

## 审计联络图

`audit_assets.py` 会顺手写 `<render>/out/diag/audit_contact_sheet.png`：7 张层的缩略图叠在蓝色底上。
新板第一次跑，**先看这张图**：能一眼看出铺铜是否成片、丝印是否只有文字没有轮廓、开窗是否只覆盖焊盘。
