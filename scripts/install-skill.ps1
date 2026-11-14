# 把本仓库的 skill/pcb-render 安装到 DSH 技能目录。
#
#   默认：建 junction（目录联接）—— 改仓库立即生效，只有一份内容
#   -Copy：普通复制 —— 适合其他机器 / 不支持 junction 的场景
#
# 用法：
#   powershell -File scripts\install-skill.ps1
#   powershell -File scripts\install-skill.ps1 -Copy
#   powershell -File scripts\install-skill.ps1 -Target "D:\somewhere\skills\pcb-render"

param(
    [string]$Target = (Join-Path $env:USERPROFILE ".dsh\skills\pcb-render"),
    [switch]$Copy
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path $PSScriptRoot -Parent
$src = Join-Path $repoRoot "skill\pcb-render"

if (-not (Test-Path (Join-Path $src "SKILL.md"))) {
    throw "找不到技能源目录：$src"
}

Write-Host "源  : $src"
Write-Host "目标: $Target"

# 清理旧安装（目录、文件、junction 三种情况都要处理）
if (Test-Path $Target) {
    $item = Get-Item $Target -Force
    if ($item.LinkType -in @("Junction", "SymbolicLink")) {
        # 删 junction 本身，不删目标内容
        cmd /c rmdir "`"$Target`"" | Out-Null
        Write-Host "  已移除旧的目录联接"
    } else {
        Remove-Item -Recurse -Force $Target
        Write-Host "  已移除旧目录"
    }
}

New-Item -ItemType Directory -Force -Path (Split-Path $Target -Parent) | Out-Null

if ($Copy) {
    Copy-Item -Recurse -Force $src $Target
    Write-Host "`n已复制安装。注意：以后改仓库需要重新跑一次本脚本同步。"
} else {
    cmd /c mklink /J "`"$Target`"" "`"$src`"" | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "junction 创建失败，回退为复制安装"
        Copy-Item -Recurse -Force $src $Target
    } else {
        Write-Host "`n已建立目录联接：改仓库 = 立即生效。"
    }
}

$skillMd = Join-Path $Target "SKILL.md"
if (Test-Path $skillMd) {
    $name = (Select-String -Path $skillMd -Pattern '^name:\s*(.+)$' | Select-Object -First 1).Matches.Groups[1].Value.Trim()
    $files = (Get-ChildItem $Target -Recurse -File).Count
    Write-Host "`nOK：技能 '$name' 已就位（$files 个文件）。新开会话即可生效。"
} else {
    throw "安装后仍找不到 SKILL.md，请检查目标路径"
}
