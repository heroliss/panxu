<#
  Build the portable Windows package: dist\PanXu\PanXu.exe (+ _internal) and a zip.

  Usage (PowerShell, in the project folder):
      python -m pip install -r requirements.txt pyinstaller
      .\build.ps1                 # or: .\build.ps1 -Python "C:\Python314\python.exe"

  An existing dist\PanXu is moved aside (never deleted), because older builds
  kept user data next to the exe.
#>
[CmdletBinding()]
param(
    [string]$Python = 'python',
    [switch]$SkipTests
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$root = [IO.Path]::GetFullPath($PSScriptRoot)
Set-Location $root

$version = (& $Python -c "import panxu; print(panxu.__version__)").Trim()
if ($LASTEXITCODE -ne 0) { throw 'Cannot import panxu with the selected Python.' }
& $Python -c "import webview, psutil, PyInstaller" 2>$null
if ($LASTEXITCODE -ne 0) { throw 'Missing build dependencies. Run: python -m pip install -r requirements.txt pyinstaller' }
Write-Host "Building PanXu $version"

if (-not $SkipTests) {
    & $Python -W error::ResourceWarning -m unittest discover -s tests -q
    if ($LASTEXITCODE -ne 0) { throw 'Unit tests failed; package not built.' }
}

$stamp = [DateTime]::Now.ToString('yyyyMMdd-HHmmss')
$build = Join-Path $root "build\$stamp"
$dist = Join-Path $root 'dist'
$package = Join-Path $dist 'PanXu'
if (Test-Path -LiteralPath $package) {
    $aside = Join-Path $dist "PanXu-previous-$stamp"
    Move-Item -LiteralPath $package -Destination $aside
    Write-Host "Moved the previous package to $aside"
}

# Stage the UI without development-only files (web\dev holds the browser mock).
$webStage = Join-Path $build 'web'
New-Item -ItemType Directory -Path $webStage -Force | Out-Null
Get-ChildItem -LiteralPath (Join-Path $root 'web') -File | Where-Object { $_.Extension -in '.html', '.css', '.js' } |
    ForEach-Object { Copy-Item -LiteralPath $_.FullName -Destination $webStage }

$parts = $version.Split('.') + @('0')
$tuple = ($parts[0..3] -join ', ')
$versionFile = Join-Path $build 'version.txt'
$versionText = @"
VSVersionInfo(
  ffi=FixedFileInfo(filevers=($tuple), prodvers=($tuple), mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[StringFileInfo([StringTable('080404B0', [
    StringStruct('CompanyName', 'Liss'),
    StringStruct('FileDescription', 'PanXu disk cleanup assistant'),
    StringStruct('FileVersion', '$version'),
    StringStruct('InternalName', 'PanXu'),
    StringStruct('OriginalFilename', 'PanXu.exe'),
    StringStruct('ProductName', 'PanXu'),
    StringStruct('LegalCopyright', 'Copyright (c) 2026 Liss'),
    StringStruct('ProductVersion', '$version')])]),
  VarFileInfo([VarStruct('Translation', [2052, 1200])])]
)
"@
[IO.File]::WriteAllText($versionFile, $versionText, [Text.UTF8Encoding]::new($false))  # no BOM: PyInstaller evals this file

$arguments = @(
    '-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir', '--windowed', '--noupx',
    '--name', 'PanXu', '--contents-directory', '_internal',
    '--distpath', $dist, '--workpath', (Join-Path $build 'work'), '--specpath', $build,
    '--version-file', $versionFile,
    '--add-data', ($webStage + ';web'),
    '--hidden-import', 'webview.platforms.winforms',
    '--hidden-import', 'webview.platforms.edgechromium',
    '--copy-metadata', 'pywebview',
    '--copy-metadata', 'psutil'
)
foreach ($module in @('tests', 'tools', 'pytest', 'tkinter', 'webview.platforms.qt', 'webview.platforms.gtk', 'webview.platforms.cocoa',
                      'webview.platforms.android', 'webview.platforms.cef', 'PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'gi', 'cefpython3')) {
    $arguments += @('--exclude-module', $module)
}
$arguments += (Join-Path $root 'run.py')
& $Python @arguments
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }

foreach ($name in @('LICENSE', 'NOTICE', 'THIRD_PARTY_NOTICES.md')) {
    Copy-Item -LiteralPath (Join-Path $root $name) -Destination $package
}
if (Test-Path -LiteralPath (Join-Path $root 'licenses')) {
    Copy-Item -LiteralPath (Join-Path $root 'licenses') -Destination $package -Recurse
}

$exe = Join-Path $package 'PanXu.exe'
if (-not (Test-Path -LiteralPath $exe)) { throw 'PanXu.exe was not produced.' }
$zip = Join-Path $dist "PanXu-$version-win-x64.zip"
if (Test-Path -LiteralPath $zip) { $zip = Join-Path $dist "PanXu-$version-win-x64-$stamp.zip" }
Compress-Archive -Path $package -DestinationPath $zip
Write-Host ''
Write-Host "Done: $exe"
Write-Host "Zip:  $zip"
Write-Host 'Build success is not acceptance: launch PanXu.exe and walk through the checklist in README.md.'
