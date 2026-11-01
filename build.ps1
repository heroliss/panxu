[CmdletBinding()]
param(
    [string]$Python = 'python'
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$projectRoot = [IO.Path]::GetFullPath($PSScriptRoot)
$entryPoint = Join-Path $projectRoot 'run.py'
$readmePath = Join-Path $projectRoot 'README.txt'
$distRoot = Join-Path $projectRoot 'dist'
$packageRoot = Join-Path $distRoot 'PanXu'
$verificationRoot = Join-Path $projectRoot 'verification'
$reportPath = Join-Path $verificationRoot 'package.json'
$buildId = 'package-' + [DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss-fff')
$buildRoot = Join-Path (Join-Path $projectRoot 'build') $buildId

function Assert-ProjectPath([string]$Path) {
    $absolute = [IO.Path]::GetFullPath($Path)
    if (-not $absolute.StartsWith($projectRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Generated output must stay inside this project: $absolute"
    }
    return $absolute
}

foreach ($path in @($entryPoint, $readmePath)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Missing required source: $path" }
}
foreach ($path in @($buildRoot, $packageRoot, $reportPath)) { $null = Assert-ProjectPath $path }
if (Test-Path -LiteralPath $packageRoot) {
    throw 'dist\PanXu already exists. Move the existing package aside before rebuilding; this script never deletes an existing package or its userdata.'
}
if (Test-Path -LiteralPath $reportPath) {
    throw 'verification\package.json already exists. Preserve the existing report before rebuilding.'
}

$preflight = @'
import importlib.metadata as m, json, platform, sys
if sys.platform != 'win32' or sys.version_info[:2] != (3, 14) or platform.machine().upper() not in ('AMD64', 'X86_64'):
    raise SystemExit('Build requires the installed Windows x64 Python 3.14 runtime.')
versions = {name: m.version(name) for name in ('pyinstaller', 'pywebview', 'psutil', 'pythonnet')}
import clr
import webview.platforms.winforms
print(json.dumps({'python_executable': sys.executable, 'python_version': platform.python_version(), 'architecture': platform.machine(), 'dependencies': versions}))
'@
$environmentJson = & $Python -c $preflight
if ($LASTEXITCODE -ne 0) { throw 'Installed dependency preflight failed; no dependencies were installed.' }
$environmentInfo = $environmentJson | ConvertFrom-Json
$pythonPath = $environmentInfo.python_executable
Write-Host ('Using Python ' + $environmentInfo.python_version + ' at ' + $pythonPath)

$versionText = [IO.File]::ReadAllText((Join-Path $projectRoot 'diskpilot\__init__.py'))
$versionMatch = [regex]::Match($versionText, '__version__\s*=\s*["''](\d+)\.(\d+)\.(\d+)["'']')
if (-not $versionMatch.Success) { throw 'Cannot determine the application version.' }
$appVersion = $versionMatch.Groups[1].Value + '.' + $versionMatch.Groups[2].Value + '.' + $versionMatch.Groups[3].Value
$versionTuple = $versionMatch.Groups[1].Value + ', ' + $versionMatch.Groups[2].Value + ', ' + $versionMatch.Groups[3].Value + ', 0'
$null = New-Item -ItemType Directory -Path $buildRoot -Force
$null = New-Item -ItemType Directory -Path $verificationRoot -Force
$versionFile = Join-Path $buildRoot 'version.txt'
$versionResource = @"
VSVersionInfo(
  ffi=FixedFileInfo(filevers=($versionTuple), prodvers=($versionTuple), mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[StringFileInfo([StringTable('040904B0', [
    StringStruct('FileDescription', 'PanXu disk and background assistant'),
    StringStruct('FileVersion', '$appVersion'),
    StringStruct('InternalName', 'PanXu'),
    StringStruct('OriginalFilename', 'PanXu.exe'),
    StringStruct('ProductName', 'PanXu'),
    StringStruct('ProductVersion', '$appVersion')
  ])]), VarFileInfo([VarStruct('Translation', [1033, 1200])])]
)
"@
[IO.File]::WriteAllText($versionFile, $versionResource, [Text.UTF8Encoding]::new($false))

$arguments = @(
    '-m', 'PyInstaller',
    '--onedir', '--windowed', '--noupx', '--clean',
    '--name', 'PanXu',
    '--contents-directory', '_internal',
    '--distpath', $distRoot,
    '--workpath', (Join-Path $buildRoot 'work'),
    '--specpath', $buildRoot,
    '--version-file', $versionFile,
    '--hidden-import', 'psutil',
    '--hidden-import', 'webview.platforms.winforms',
    '--hidden-import', 'webview.platforms.edgechromium',
    '--copy-metadata', 'pywebview',
    '--copy-metadata', 'psutil'
)
# Explicit application asset allowlist: never collect the project directory,
# verification fixtures, model settings, API keys, databases, or user histories.
foreach ($asset in @('index.html', 'style.css', 'app.js', 'tasks.js', 'files.js')) {
    $assetPath = Join-Path (Join-Path $projectRoot 'web') $asset
    if (-not (Test-Path -LiteralPath $assetPath -PathType Leaf)) { throw "Missing web asset: $assetPath" }
    if ((Get-Item -LiteralPath $assetPath -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw "Refusing to package a linked web asset: $assetPath"
    }
    $arguments += @('--add-data', ($assetPath + ';web'))
}
$skillFilesJson = & $pythonPath -c "import sys,json; sys.path.insert(0,sys.argv[1]); from diskpilot.knowledge import BUNDLED_SKILL_FILES; print(json.dumps(BUNDLED_SKILL_FILES))" $projectRoot
if ($LASTEXITCODE -ne 0) { throw 'Cannot load bundled guidance allowlist.' }
$skillDirectory = Join-Path $projectRoot 'skills'
if ((Get-Item -LiteralPath $skillDirectory -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Refusing linked guidance directory.' }
foreach ($skillFile in ($skillFilesJson | ConvertFrom-Json)) {
    if ($skillFile -notmatch '^[a-z0-9-]+\.md$') { throw 'Invalid bundled guidance filename.' }
    $skillPath = Assert-ProjectPath (Join-Path $skillDirectory $skillFile)
    if (-not (Test-Path -LiteralPath $skillPath -PathType Leaf) -or ((Get-Item -LiteralPath $skillPath -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw "Missing or linked guidance resource: $skillFile" }
    $arguments += @('--add-data', ($skillPath + ';skills'))
}
foreach ($module in @('tests', 'pytest', 'webview.platforms.qt', 'webview.platforms.gtk', 'webview.platforms.cocoa', 'webview.platforms.android', 'webview.platforms.cef', 'PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'gi', 'cefpython3')) {
    $arguments += @('--exclude-module', $module)
}
$arguments += $entryPoint

$previousConfigDir = $env:PYINSTALLER_CONFIG_DIR
$env:PYINSTALLER_CONFIG_DIR = Join-Path $buildRoot 'pyinstaller-cache'
Push-Location $projectRoot
try {
    & $pythonPath @arguments
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }
} finally {
    Pop-Location
    $env:PYINSTALLER_CONFIG_DIR = $previousConfigDir
}
Copy-Item -LiteralPath $readmePath -Destination (Join-Path $packageRoot 'README.txt')

$verify = @'
import hashlib, importlib.metadata as m, json, pathlib, platform, re, struct, sys
from datetime import datetime, timezone
from PyInstaller.archive.readers import CArchiveReader
from PyInstaller.utils.win32.winmanifest import read_manifest_from_executable

root, package, report = map(pathlib.Path, sys.argv[1:4])
sys.path.insert(0,str(root))
from diskpilot.knowledge import BUNDLED_SKILL_FILES
exe = package / 'PanXu.exe'
sha256 = lambda p: hashlib.file_digest(p.open('rb'), 'sha256').hexdigest()
files = sorted(p for p in package.rglob('*') if p.is_file())
linked = [str(p.relative_to(package)) for p in package.rglob('*') if p.is_symlink() or (hasattr(p, 'is_junction') and p.is_junction())]
forbidden = [str(p.relative_to(package)) for p in files if any(part.casefold() in {'verification', 'tests', 'userdata', '.git', '.venv'} for part in p.relative_to(package).parts) or p.name.casefold() in {'.env', 'model_settings.json'} or p.name.casefold().startswith('.env.') or p.name.casefold().endswith(('.sqlite', '.sqlite3', '.db', '.sqlite3-wal', '.sqlite3-shm'))]
assets = []
for name in ('index.html', 'style.css', 'app.js', 'tasks.js', 'files.js'):
    source, target = root / 'web' / name, package / '_internal' / 'web' / name
    assets.append({'path': str(target.relative_to(package)), 'bytes': target.stat().st_size if target.exists() else None, 'sha256': sha256(target) if target.exists() else None, 'matches_source': target.exists() and sha256(source) == sha256(target)})
skill_assets=[]
for name in BUNDLED_SKILL_FILES:
    source,target=root/'skills'/name,package/'_internal'/'skills'/name
    skill_assets.append({'path':str(target.relative_to(package)),'bytes':target.stat().st_size if target.exists() else None,'sha256':sha256(target) if target.exists() else None,'matches_source':target.exists() and sha256(source)==sha256(target)})
required_names = ('python314.dll', 'Python.Runtime.dll', 'Microsoft.Web.WebView2.Core.dll', 'Microsoft.Web.WebView2.WinForms.dll', 'WebView2Loader.dll')
runtime_resources = {name: [str(p.relative_to(package)) for p in files if p.name.casefold() == name.casefold()] for name in required_names}
archive = CArchiveReader(str(exe))
pyz_name = next(name for name in archive.toc if name.endswith('.pyz'))
modules = sorted(archive.open_embedded_archive(pyz_name).toc)
required_modules = {name: name in modules for name in ('diskpilot.server', 'diskpilot.llm', 'diskpilot.knowledge', 'diskpilot.tasks', 'diskpilot.scan_index', 'psutil', 'webview', 'webview.platforms.winforms', 'webview.platforms.edgechromium')}
forbidden_modules = [name for name in modules if name == 'tests' or name.startswith(('tests.', 'verification.'))]
with exe.open('rb') as f:
    f.seek(0x3c)
    pe_offset = struct.unpack('<I', f.read(4))[0]
    f.seek(pe_offset + 4)
    machine = struct.unpack('<H', f.read(2))[0]
    f.seek(pe_offset + 24 + 68)
    subsystem = struct.unpack('<H', f.read(2))[0]
manifest = read_manifest_from_executable(str(exe)).decode('utf-8')
as_invoker = 'level="asInvoker"' in manifest or "level='asInvoker'" in manifest
version = re.search(r'__version__\s*=\s*[\"\']([^\"\']+)', (root / 'diskpilot' / '__init__.py').read_text(encoding='utf-8')).group(1)
passed = all(a['matches_source'] for a in assets+skill_assets) and all(runtime_resources.values()) and all(required_modules.values()) and not forbidden and not linked and not forbidden_modules and machine == 0x8664 and subsystem == 2 and as_invoker
result = {
    'status': 'passed' if passed else 'failed',
    'verification_scope': 'Static Windows portable package verification; GUI launch and end-to-end acceptance are separate.',
    'built_at_utc': datetime.now(timezone.utc).isoformat(),
    'application': {'name': 'PanXu', 'version': version, 'mode': 'simulation'},
    'build': {'python_version': platform.python_version(), 'python_executable': sys.executable, 'dependencies': {name: m.version(name) for name in ('pyinstaller', 'pywebview', 'psutil', 'pythonnet')}, 'layout': 'onedir', 'windowed': subsystem == 2, 'architecture': 'x64' if machine == 0x8664 else hex(machine), 'requested_execution_level': 'asInvoker' if as_invoker else 'unverified', 'installs_dependencies': False},
    'package': {'directory': str(package), 'file_count': len(files), 'total_bytes': sum(p.stat().st_size for p in files), 'exe_path': str(exe), 'exe_bytes': exe.stat().st_size, 'exe_sha256': sha256(exe), 'readme_included': (package / 'README.txt').exists()},
    'web_resources': assets,
    'skill_resources': skill_assets,
    'runtime_resources': runtime_resources,
    'required_modules': required_modules,
    'forbidden_paths_found': forbidden,
    'forbidden_modules_found': forbidden_modules,
    'reparse_points_found': linked,
    'userdata_included': False,
    'provider_key_included': False,
    'runtime_acceptance': {'status': 'pending', 'reason': 'Run the packaged application and verify the native window, health API, and primary flows.'}
}
report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps(result, ensure_ascii=False, indent=2))
if not passed:
    raise SystemExit('Package verification failed; inspect verification/package.json.')
'@
$verify | & $pythonPath - $projectRoot $packageRoot $reportPath
if ($LASTEXITCODE -ne 0) { throw 'Static package verification failed.' }
Write-Host ('Portable package ready: ' + (Join-Path $packageRoot 'PanXu.exe'))
Write-Host ('Verification report: ' + $reportPath)
