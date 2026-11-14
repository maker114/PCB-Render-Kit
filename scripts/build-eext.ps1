# 重新构建立创EDA 扩展，并把 .eext 复制到仓库 dist/。
#
# 用法：
#   powershell -File scripts\build-eext.ps1
#   powershell -File scripts\build-eext.ps1 -Registry "https://registry.npmmirror.com"
#   powershell -File scripts\build-eext.ps1 -SkipInstall     # node_modules 已就绪
#
# 产物：eda-extension\pcb-render-kit\build\dist\*.eext  →  复制到  dist\

param(
    [string]$Registry = "https://registry.npmmirror.com",
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path $PSScriptRoot -Parent
$proj = Join-Path $repoRoot "eda-extension\pcb-render-kit"
$distOut = Join-Path $repoRoot "dist"

if (-not (Test-Path (Join-Path $proj "package.json"))) {
    throw "找不到扩展工程：$proj"
}

# 定位 npm：优先 PATH，其次常见安装位置
$npmCmd = Get-Command npm.cmd -ErrorAction SilentlyContinue
$npm = if ($null -ne $npmCmd) { $npmCmd.Source } else { $null }   # 5.1 不支持 ?. 运算符
if (-not $npm) {
    foreach ($cand in @("E:\nodejs\npm.cmd", "$env:ProgramFiles\nodejs\npm.cmd",
                        "$env:APPDATA\npm\npm.cmd")) {
        if (Test-Path $cand) { $npm = $cand; break }
    }
}
if (-not $npm) { throw "找不到 npm.cmd，请安装 Node.js 或把它加到 PATH" }
Write-Host "npm : $npm"
Write-Host "proj: $proj"

Push-Location $proj
try {
    $env:npm_config_registry = $Registry
    if (-not $SkipInstall -and -not (Test-Path (Join-Path $proj "node_modules"))) {
        Write-Host "`n== npm install =="
        & $npm install --no-audit --no-fund
        if ($LASTEXITCODE -ne 0) { throw "npm install 失败（exit $LASTEXITCODE）" }
    }

    Write-Host "`n== npm run build =="
    & $npm run build
    if ($LASTEXITCODE -ne 0) { throw "构建失败（exit $LASTEXITCODE）" }

    $built = Get-ChildItem (Join-Path $proj "build\dist") -Filter "*.eext" -ErrorAction SilentlyContinue |
             Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $built) { throw "构建结束但没找到 .eext 产物" }

    New-Item -ItemType Directory -Force -Path $distOut | Out-Null
    Copy-Item $built.FullName (Join-Path $distOut $built.Name) -Force

    $ver = (Get-Content (Join-Path $proj "extension.json") -Raw -Encoding UTF8 |
            Select-String '"version"\s*:\s*"([^"]+)"').Matches.Groups[1].Value
    Write-Host "`nOK：$($built.Name)  (version $ver, $([math]::Round($built.Length/1KB)) KB)"
    Write-Host "    已复制到 $distOut"
    Write-Host "    在 EDA 里：高级 → 扩展管理器 → 导入 这个 .eext"
} finally {
    Pop-Location
}
