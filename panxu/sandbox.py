"""演练环境 (simulation): a fake Windows installation for safe end-to-end testing.

``python run.py --sandbox`` (or ``PanXu.exe --sandbox``) builds a folder that
looks like a lived-in Windows PC — a C:\\ and a D:\\ with Windows, Program
Files, a user profile full of caches, downloads, duplicates and old document
versions — plus a fake registry (startup items, installed programs), fake
services and a Recycle Bin. Every base folder (%SystemRoot%, %USERPROFILE%,
...) points inside it, the protection policy refuses anything outside it, and
system changes (registry, services, DISM, cleanmgr, uninstallers) are applied
to the fake state only.

Large files are *sparse*: they report their full size (so the numbers look
real) but use almost no disk space.
"""
from __future__ import annotations

import io
import json
import os
import random
import shutil
import threading
import time
import uuid
import zipfile

from . import paths, winapi

MARKER = '.panxu-sandbox'
USER = 'demo'
KB, MB, GB = 1024, 1024 ** 2, 1024 ** 3
DAY = 86400.0

HKCU, HKLM = 'HKCU', 'HKLM'
RUN = r'Software\Microsoft\Windows\CurrentVersion\Run'
RUN32 = r'Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run'
APPROVED = r'Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved'
UNINSTALL = r'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall'
UNINSTALL32 = r'SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall'

DRIVES = {'C': ('演练系统盘', 256 * GB, 168 * GB), 'D': ('演练数据盘', 1024 * GB, 410 * GB)}  # label, total, base used


# ---------------------------------------------------------------------------
# small document builders (real content, so content previews can be tested)
# ---------------------------------------------------------------------------

def _xml_escape(text: str) -> str:
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def _zip_bytes(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            # fixed timestamps: identical inputs must give identical files (duplicates)
            info = zipfile.ZipInfo(name, date_time=(2024, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_STORED if name.endswith(('.png', '.jpg', '.bin')) else zipfile.ZIP_DEFLATED
            zf.writestr(info, data)
    return buf.getvalue()


def docx(paragraphs: list[str], pad: int = 0, seed: int = 0) -> bytes:
    body = ''.join(f'<w:p><w:r><w:t>{_xml_escape(p)}</w:t></w:r></w:p>' for p in paragraphs)
    members = {
        '[Content_Types].xml': b'<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                               b'<Default Extension="xml" ContentType="application/xml"/></Types>',
        'word/document.xml': ('<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/'
                              f'wordprocessingml/2006/main"><w:body>{body}</w:body></w:document>').encode('utf-8'),
    }
    if pad:
        members['word/media/image1.png'] = random.Random(seed).randbytes(pad)
    return _zip_bytes(members)


def pptx(slides: list[str], pad: int = 0, seed: int = 0) -> bytes:
    members = {'[Content_Types].xml': b'<?xml version="1.0"?><Types/>'}
    for i, text in enumerate(slides, 1):
        members[f'ppt/slides/slide{i}.xml'] = (
            '<?xml version="1.0" encoding="UTF-8"?><p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
            'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:cSld><p:spTree><p:sp><p:txBody>'
            f'<a:p><a:r><a:t>{_xml_escape(text)}</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>').encode('utf-8')
    if pad:
        members['ppt/media/image1.png'] = random.Random(seed).randbytes(pad)
    return _zip_bytes(members)


def xlsx(cells: list[str]) -> bytes:
    strings = ''.join(f'<si><t>{_xml_escape(c)}</t></si>' for c in cells)
    return _zip_bytes({
        '[Content_Types].xml': b'<?xml version="1.0"?><Types/>',
        'xl/sharedStrings.xml': ('<?xml version="1.0" encoding="UTF-8"?><sst xmlns="http://schemas.openxmlformats.org/'
                                 f'spreadsheetml/2006/main">{strings}</sst>').encode('utf-8'),
    })


def _blob(seed: int, size: int, header: bytes = b'') -> bytes:
    rng = random.Random(seed)
    return header + rng.randbytes(max(0, size - len(header)))


JPEG = b'\xff\xd8\xff\xe0\x00\x10JFIF\x00'
PNG = b'\x89PNG\r\n\x1a\n'


# ---------------------------------------------------------------------------
# the layout
# ---------------------------------------------------------------------------

class _Spec:
    __slots__ = ('rel', 'size', 'data', 'age', 'atime_age', 'sparse')

    def __init__(self, rel, size=0, data=None, age=0.0, atime_age=None):
        self.rel = rel
        self.data = data
        self.size = len(data) if data is not None else size
        self.sparse = data is None and size >= 256 * KB
        self.age = age
        self.atime_age = age if atime_age is None else atime_age


def _layout() -> tuple[list[_Spec], list[tuple[str, float]]]:
    """(files, empty folders). Paths use '/' and start with the drive letter."""
    F = _Spec
    u = f'C/Users/{USER}'
    L, R = f'{u}/AppData/Local', f'{u}/AppData/Roaming'
    PF, PF86 = 'C/Program Files', 'C/Program Files (x86)'
    files: list[_Spec] = []
    add = files.append

    # -- Windows -----------------------------------------------------------------
    for name in ('ntoskrnl.exe', 'svchost.exe', 'spoolsv.exe', 'SearchIndexer.exe', 'SecurityHealthSystray.exe',
                 'RtkAudUService64.exe', 'sc.exe', 'cleanmgr.exe', 'Dism.exe'):
        add(F(f'C/Windows/System32/{name}', 2 * MB, age=400))
    add(F('C/Windows/System32/DriverStore/FileRepository/nv_dispi.inf_amd64/nvlddmkm.sys', 1200 * MB, age=60))
    add(F('C/Windows/WinSxS/amd64_microsoft-windows-shell32_31bf3856ad364e35/shell32.dll', 2300 * MB, age=300))
    add(F('C/Windows/WinSxS/Temp/PendingDeletes/superseded_01.bin', 640 * MB, age=40))
    add(F('C/Windows/Installer/1a2b3c.msi', 720 * MB, age=500))
    add(F('C/Windows/Temp/MSI3f2a.tmp', 46 * MB, age=12))
    add(F('C/Windows/Temp/dd_vcredist_amd64.log', 1 * MB, age=40))
    add(F('C/Windows/Temp/fresh_setup.tmp', 6 * MB, age=0.1))
    add(F('C/Windows/SoftwareDistribution/Download/8f1a2c/windows11.0-kb5044284-x64.msu', 830 * MB, age=15))
    add(F('C/Windows/SoftwareDistribution/Download/8f1a2c/express.cab', 160 * MB, age=15))
    add(F('C/Windows/Logs/CBS/CbsPersist_20260901093011.log', 62 * MB, age=60))
    add(F('C/Windows/Logs/CBS/CBS.log', 8 * MB, age=0.2))
    add(F('C/Windows/Minidump/100126-15625-01.dmp', 2 * MB, age=33))
    add(F('C/Windows/MEMORY.DMP', 950 * MB, age=33))
    add(F('C/Windows/LiveKernelReports/WATCHDOG-20260801-1200.dmp', 310 * MB, age=90))
    add(F('C/Windows/ServiceProfiles/NetworkService/AppData/Local/Microsoft/Windows/DeliveryOptimization/Cache/a1b2/c3d4',
          390 * MB, age=20))
    for exe, age in (('CHROME.EXE', 0.2), ('WECHAT.EXE', 1), ('CODE.EXE', 2), ('FOOSOFT.EXE', 5), ('ACRORD32.EXE', 30),
                     ('STEAM.EXE', 210), ('HOLLOW_KNIGHT.EXE', 420), ('QUICKVIEW.EXE', 400)):
        add(F(f'C/Windows/Prefetch/{exe}-{abs(hash(exe)) % 0xFFFFFFFF:08X}.pf', 40 * KB, age=age))
    add(F('C/Windows.old/Windows/System32/ntoskrnl.exe', 3600 * MB, age=200))
    add(F('C/Windows.old/Users/demo/Documents/旧电脑上的笔记.txt', data='升级前的备忘'.encode('utf-8'), age=200))
    add(F('C/$WINDOWS.~BT/Sources/install.esd', 1150 * MB, age=150))
    add(F('C/pagefile.sys', 4096 * MB, age=0.1))
    add(F('C/hiberfil.sys', 3270 * MB, age=0.1))
    add(F('C/NVIDIA/DisplayDriver/566.36/Win11_64/International/setup.exe', 910 * MB, age=50))

    # -- ProgramData ---------------------------------------------------------------
    add(F('C/ProgramData/Microsoft/Windows/WER/ReportArchive/AppCrash_FooSoft.exe_7a1b/Report.wer', 1 * MB, age=10))
    add(F('C/ProgramData/Package Cache/{9C1A2B3C-0000-4D2E-8F11-1234567890AB}/VC_redist.x64.exe', 25 * MB, age=300))
    add(F('C/ProgramData/NVIDIA Corporation/Downloader/latest/566.36-desktop-win10-win11-64bit.exe', 690 * MB, age=50))
    add(F('C/ProgramData/FooSoft/UpdateCache/FooSoft_2.4.0_full.pkg', 1320 * MB, age=25))
    add(F('C/ProgramData/FooSoft/UpdateCache/FooSoft_2.3.5_delta.pkg', 410 * MB, age=80))
    add(F('C/ProgramData/FooSoft/license.dat', data=b'FOOSOFT-LICENSE-DEMO', age=300))
    add(F('C/ProgramData/QuickView/AdCache/banner_pack_2026.dat', 640 * MB, age=8))
    add(F('C/ProgramData/QuickView/AdCache/news_feed.db', 22 * MB, age=1))

    # -- Program Files -------------------------------------------------------------
    add(F(f'{PF}/Google/Chrome/Application/chrome.exe', 3 * MB, age=20))
    add(F(f'{PF}/Google/Chrome/Application/130.0.6723.92/chrome.dll', 420 * MB, age=20))
    add(F(f'{PF}/Tencent/WeChat/WeChat.exe', 2 * MB, age=60))
    add(F(f'{PF}/Tencent/WeChat/[3.9.12.17]/WeChatWin.dll', 230 * MB, age=60))
    add(F(f'{PF}/FooSoft/FooSoft.exe', 5 * MB, age=200))
    add(F(f'{PF}/FooSoft/FooUpdate.exe', 1 * MB, age=200))
    add(F(f'{PF}/FooSoft/FooSoftSvc.exe', 1 * MB, age=200))
    add(F(f'{PF}/FooSoft/resources/core.pak', 610 * MB, age=200))
    add(F(f'{PF}/QuickView/QuickView.exe', 4 * MB, age=410))
    add(F(f'{PF}/QuickView/QVHelper.exe', 2 * MB, age=410))
    add(F(f'{PF}/QuickView/QVUpdateSvc.exe', 1 * MB, age=410))
    add(F(f'{PF}/QuickView/plugins/ads.dll', 118 * MB, age=410))
    add(F(f'{PF}/Adobe/Acrobat DC/Acrobat/AcroRd32.exe', 410 * MB, age=120))
    add(F(f'{PF}/NVIDIA Corporation/Display.NvContainer/NVDisplay.Container.exe', 3 * MB, age=50))
    add(F(f'{PF86}/Common Files/Adobe/ARM/1.0/AdobeARM.exe', 3 * MB, age=120))
    add(F(f'{PF86}/Common Files/Adobe/ARM/1.0/armsvc.exe', 1 * MB, age=120))
    add(F(f'{PF86}/Common Files/Steam/steamservice.exe', 2 * MB, age=210))
    add(F(f'{PF86}/Google/GoogleUpdater/updater.exe', 4 * MB, age=20))
    add(F(f'{PF86}/Microsoft/EdgeUpdate/MicrosoftEdgeUpdate.exe', 1 * MB, age=30))
    add(F(f'{PF86}/Steam/steam.exe', 4 * MB, age=210))
    add(F(f'{PF86}/Steam/steamapps/common/Hollow Knight/hollow_knight_Data/data.unity3d', 9100 * MB, age=420))
    add(F(f'{PF86}/Steam/steamapps/common/Hollow Knight/hollow_knight.exe', 1 * MB, age=420))

    # -- user profile: caches (junk rules) ---------------------------------------------
    add(F(f'{L}/Temp/wct8A2B.tmp', 120 * MB, age=3))
    add(F(f'{L}/Temp/{{5D1E2C3B-AAAA-4BBB-8CCC-1234567890AB}}/setup.exe', 62 * MB, age=9))
    add(F(f'{L}/Temp/chrome_installer.log', 1 * MB, age=20))
    add(F(f'{L}/Temp/VSCodeSetup-x64-1.95.3/payload.bin', 84 * MB, age=5))
    add(F(f'{L}/Temp/still_in_use.tmp', 31 * MB, age=0.05))
    for i in range(1, 41):
        add(F(f'{L}/Google/Chrome/User Data/Default/Cache/Cache_Data/f_{i:06x}', 6 * MB + i * 997, age=1 + i % 9))
    add(F(f'{L}/Google/Chrome/User Data/Default/Code Cache/js/index-dir/the-real-index', 52 * MB, age=2))
    add(F(f'{L}/Google/Chrome/User Data/Default/GPUCache/data_1', 21 * MB, age=2))
    add(F(f'{L}/Google/Chrome/User Data/Profile 1/Cache/Cache_Data/f_000001', 64 * MB, age=6))
    add(F(f'{L}/Google/Chrome/User Data/ShaderCache/data_0', 31 * MB, age=4))
    add(F(f'{L}/Google/Chrome/User Data/Default/Bookmarks', data=b'{"roots": {}}', age=1))
    add(F(f'{L}/Google/Chrome/User Data/Default/History', 6 * MB, age=0.1))
    for i in range(1, 16):
        add(F(f'{L}/Microsoft/Edge/User Data/Default/Cache/Cache_Data/f_{i:06x}', 12 * MB + i * 991, age=2))
    add(F(f'{L}/Microsoft/Windows/INetCache/IE/AB12CD34/banner.dat', 41 * MB, age=30))
    add(F(f'{L}/Microsoft/Windows/WER/ReportArchive/AppCrash_QuickView.exe_c1/Report.wer', 3 * MB, age=15))
    add(F(f'{L}/Microsoft/Windows/Explorer/thumbcache_256.db', 142 * MB, age=1))
    add(F(f'{L}/Microsoft/Windows/Explorer/thumbcache_1280.db', 61 * MB, age=1))
    add(F(f'{L}/Microsoft/OneDrive/OneDrive.exe', 3 * MB, age=40))
    add(F(f'{L}/CrashDumps/FooSoft.exe.13456.dmp', 212 * MB, age=40))
    add(F(f'{L}/D3DSCache/a1b2c3/shader.idx', 93 * MB, age=7))
    add(F(f'{L}/NVIDIA/DXCache/dx_0001.bin', 151 * MB, age=7))
    add(F(f'{L}/npm-cache/_cacache/content-v2/sha512/ab/cd/0123456789', 655 * MB, age=30))
    add(F(f'{L}/pip/cache/http-v2/a/b/c/wheel.body', 233 * MB, age=60))
    add(F(f'{L}/uv/cache/archive-v0/torch-2.5.1/torch_cpu.dll', 305 * MB, age=45))
    add(F(f'{R}/Code/Cache/Cache_Data/data_1', 41 * MB, age=3))
    add(F(f'{R}/Code/CachedData/a1b2c3/chrome/js/index.js', 122 * MB, age=3))
    add(F(f'{R}/Code/logs/20260801T101010/main.log', 11 * MB, age=90))
    add(F(f'{R}/Code/User/settings.json', data=b'{"editor.fontSize": 14}', age=30))
    add(F(f'{R}/Adobe/Common/Media Cache Files/clip_0001.cfa', 705 * MB, age=60))
    add(F(f'{R}/Slack/Cache/Cache_Data/data_3', 124 * MB, age=12))
    add(F(f'{u}/AppData/LocalLow/Intel/ShaderCache/cache.bin', 21 * MB, age=12))
    add(F(f'{u}/.gradle/caches/modules-2/files-2.1/com.android.tools/gradle.jar', 1210 * MB, age=150))

    # -- user profile: unknown application data (for AI analysis) ----------------------------
    add(F(f'{L}/JetBrains/IntelliJIdea2021.1/caches/content.dat', 1640 * MB, age=1100))
    add(F(f'{L}/JetBrains/IntelliJIdea2021.1/log/idea.log', 33 * MB, age=1100))
    add(F(f'{L}/JetBrains/IntelliJIdea2025.2/caches/content.dat', 905 * MB, age=1))
    add(F(f'{L}/JetBrains/IntelliJIdea2025.2/log/idea.log', 12 * MB, age=0.5))
    add(F(f'{L}/FooSoft/cache/thumbs/thumbs_0001.db', 1930 * MB, age=2))
    add(F(f'{L}/FooSoft/User/config.json', data=b'{"recent": ["poster.foo"]}', age=2))
    add(F(f'{L}/FooSoft/User/templates/poster.foo', 18 * MB, age=40))
    add(F(f'{L}/OldGameStudio/StarVoyage/Saved/SaveGames/slot1.sav', 2 * MB, age=700))
    add(F(f'{L}/OldGameStudio/StarVoyage/Saved/Logs/StarVoyage.log', 21 * MB, age=700))
    add(F(f'{L}/OldGameStudio/StarVoyage/Cache/shaders.bin', 1120 * MB, age=700))
    add(F(f'{L}/Docker/wsl/disk/docker_data.vhdx', 8200 * MB, age=20))
    add(F(f'{L}/Unity/cache/packages/packages.unity.com/com.unity.render-pipelines/pkg.tgz', 760 * MB, age=120))
    add(F(f'{L}/ms-playwright/chromium-1140/chrome-win/chrome.exe', 520 * MB, age=200))
    add(F(f'{L}/Programs/Microsoft VS Code/Code.exe', 155 * MB, age=25))
    add(F(f'{L}/Packages/Microsoft.WindowsStore_8wekyb3d8bbwe/LocalCache/store.db', 205 * MB, age=3))
    add(F(f'{L}/QuickView/Cache/ad_0001.jpg', 183 * MB, age=2))
    add(F(f'{R}/Tencent/WeChat/All Users/config/global_config', data=b'demo', age=10))
    add(F(f'{u}/.cache/huggingface/hub/models--bert-base-chinese/blobs/9b1a2c', 412 * MB, age=300))

    # -- personal files ---------------------------------------------------------------------
    thesis_v1 = docx(['基于深度学习的图像分割研究', '第一章 绪论（初稿）', '本文研究……'], pad=1400 * KB, seed=11)
    thesis_v2 = docx(['基于深度学习的图像分割研究', '第一章 绪论（根据导师意见修改）', '本文研究……',
                      '第二章 相关工作'], pad=1500 * KB, seed=12)
    thesis_v3 = docx(['基于深度学习的图像分割研究', '摘要', '第一章 绪论', '第二章 相关工作', '第三章 方法',
                      '第四章 实验', '第五章 总结（定稿，已提交学院）'], pad=1700 * KB, seed=13)
    add(F(f'{u}/Documents/毕业论文.docx', data=thesis_v1, age=900))
    add(F(f'{u}/Documents/毕业论文(1).docx', data=thesis_v1, age=899))
    add(F(f'{u}/Documents/毕业论文_修改版.docx', data=thesis_v2, age=870))
    add(F(f'{u}/Documents/毕业论文_最终版.docx', data=thesis_v3, age=860))
    add(F(f'{u}/Documents/毕业论文_最终版 - 副本.docx', data=thesis_v3, age=860))
    add(F(f'{u}/Documents/简历_2024.docx', data=docx(['张三 简历', '求职意向：算法工程师', '2024 年版']), age=700))
    add(F(f'{u}/Documents/简历_2025.docx', data=docx(['张三 简历', '求职意向：高级算法工程师', '2025 年版']), age=330))
    add(F(f'{u}/Documents/项目A/需求文档_v1.docx', data=docx(['项目 A 需求文档 v1', '功能列表（初版）']), age=400))
    add(F(f'{u}/Documents/项目A/需求文档_v2.docx', data=docx(['项目 A 需求文档 v2', '功能列表（评审后）']), age=390))
    add(F(f'{u}/Documents/项目A/需求文档_v3.docx', data=docx(['项目 A 需求文档 v3', '功能列表（最终确认）']), age=380))
    add(F(f'{u}/Documents/财务/2023年报销汇总.xlsx', data=xlsx(['日期', '项目', '金额', '2023-03-02', '差旅', '1280']), age=600))
    add(F(f'{u}/Documents/Notes/读书笔记.md', data='# 读书笔记\n\n《人类简史》读后感：……\n'.encode('utf-8'), age=200))
    add(F(f'{u}/Documents/WeChat Files/wxid_demo/FileStorage/File/2025-05/合同扫描件.pdf', 15 * MB, age=180))
    add(F(f'{u}/Desktop/项目汇报.pptx', data=pptx(['2026 年第三季度项目汇报', '进度：已完成 80%', '下一步计划'],
                                                   pad=900 * KB, seed=21), age=40))
    shot = _blob(31, 3 * MB + 123, PNG)
    add(F(f'{u}/Desktop/截图 2026-03-01 101530.png', data=shot, age=245))
    add(F(f'{u}/Pictures/截图 2026-03-01 101530.png', data=shot, age=245))
    add(F(f'{u}/Desktop/待办.txt', data='1. 交报销单\n2. 备份照片\n'.encode('utf-8'), age=10))
    add(F(f'{u}/Desktop/WeChatSetup.exe', 182 * MB, age=400))
    # Downloads: installers for installed programs, incomplete downloads, extracted archives, big old images
    add(F(f'{u}/Downloads/ChromeSetup.exe', 1 * MB + 4321, age=300))
    add(F(f'{u}/Downloads/VSCodeUserSetup-x64-1.95.3.exe', 98 * MB, age=250))
    add(F(f'{u}/Downloads/WeChatSetup_3.9.12.17.exe', 186 * MB, age=200))
    add(F(f'{u}/Downloads/FooSoft_Setup_2.3.0.msi', 141 * MB, age=180))
    add(F(f'{u}/Downloads/SteamSetup.exe', 2 * MB + 77, age=500))
    add(F(f'{u}/Downloads/QuickView_Setup_v5.2.exe', 61 * MB, age=410))
    add(F(f'{u}/Downloads/python-3.12.4-amd64.exe', 26 * MB, age=420))
    photos = {f'IMG_{i:04d}.jpg': _blob(100 + i, 600 * KB + i * 101, JPEG) for i in range(1, 11)}
    add(F(f'{u}/Downloads/旅行照片.zip', data=_zip_bytes(photos), age=150))
    for name, data in photos.items():
        add(F(f'{u}/Downloads/旅行照片/{name}', data=data, age=150))
    add(F(f'{u}/Downloads/数据集.rar', 402 * MB, age=260))
    add(F(f'{u}/Downloads/数据集/train.csv', 310 * MB, age=260))
    add(F(f'{u}/Downloads/电影.mkv.crdownload', 702 * MB, age=30))
    add(F(f'{u}/Downloads/年度报告.pdf.part', 12 * MB, age=45))
    add(F(f'{u}/Downloads/Ubuntu-24.04.1-desktop-amd64.iso', 5900 * MB, age=700))
    # a portable program unpacked in Downloads and used from there: its .exe is no installer, its
    # DLLs are no old downloads (我的文件 must leave the whole folder alone)
    add(F(f'{u}/Downloads/Snipaste-2.11.3-x64/Snipaste.exe', 7 * MB, age=200))
    add(F(f'{u}/Downloads/Snipaste-2.11.3-x64/Qt6Core.dll', 5 * MB, age=200))
    add(F(f'{u}/Downloads/Snipaste-2.11.3-x64/platforms/qwindows.dll', 1 * MB, age=200))
    add(F(f'{u}/Downloads/Snipaste-2.11.3-x64/config.ini', data=b'[General]\nlanguage=zh\n', age=5))
    invoice = _blob(41, 2 * MB + 555, b'%PDF-1.7\n')
    add(F(f'{u}/Downloads/发票.pdf', data=invoice, age=100))
    add(F(f'{u}/Downloads/发票(1).pdf', data=invoice, age=99))
    for i in range(1, 6):
        add(F(f'{u}/Pictures/Camera Roll/IMG_2023010{i}.jpg', data=_blob(200 + i, 2 * MB + i * 333, JPEG), age=1000))
    add(F(f'{u}/Pictures/2024 旅行/IMG_0001.jpg', data=photos['IMG_0001.jpg'], age=150))
    add(F(f'{u}/Videos/2019 家庭聚会.mp4', 2460 * MB, age=2400, atime_age=2400))
    add(F(f'{u}/Videos/录屏 2025-11-02.mp4', 1310 * MB, age=366))
    add(F(f'{u}/Videos/剪辑工程/Render/temp_render_001.mp4', 905 * MB, age=300))
    add(F(f'{u}/Music/歌单/夜曲.mp3', data=_blob(51, 4 * MB + 9, b'ID3'), age=900))

    # -- D: drive --------------------------------------------------------------------------
    add(F('D/资料/旧电脑备份/backup_2020.zip', 3500 * MB, age=2000))
    add(F('D/资料/照片备份/IMG_20230101.jpg', data=_blob(201, 2 * MB + 333, JPEG), age=1000))
    add(F('D/Games/Genshin Impact/GenshinImpact_Data/blocks.pak', 64000 * MB, age=60))
    add(F('D/下载/navicat_x64.exe', 120 * MB, age=500))

    empty_dirs = [(f'{u}/Desktop/新建文件夹', 300), (f'{u}/Desktop/新建文件夹 (2)', 120),
                  (f'{u}/Documents/项目A/旧草稿', 500), (f'{R}/Microsoft/Windows/Start Menu/Programs/Startup', 300),
                  ('C/$Recycle.Bin', 400)]
    return files, empty_dirs


# ---------------------------------------------------------------------------
# fake system state (registry, services, version info)
# ---------------------------------------------------------------------------

def _state(native) -> dict:
    """``native(rel)`` turns 'C/Program Files/x' into an absolute path inside the sandbox."""
    u = f'C/Users/{USER}'
    L = f'{u}/AppData/Local'
    PF, PF86 = 'C/Program Files', 'C/Program Files (x86)'
    q = lambda rel: '"' + native(rel) + '"'  # noqa: E731

    reg = {HKCU: {}, HKLM: {}}

    def put(hive, key, name, value):
        reg[hive].setdefault(key, {})[name] = value

    put(HKCU, RUN, 'OneDrive', q(f'{L}/Microsoft/OneDrive/OneDrive.exe') + ' /background')
    put(HKCU, RUN, 'WeChat', q(f'{PF}/Tencent/WeChat/WeChat.exe') + ' -autorun')
    put(HKCU, RUN, 'Steam', q(f'{PF86}/Steam/steam.exe') + ' -silent')
    put(HKCU, RUN, 'FooSoftUpdater', q(f'{PF}/FooSoft/FooUpdate.exe') + ' --tray')
    put(HKCU, RUN, 'QuickViewHelper', q(f'{PF}/QuickView/QVHelper.exe') + ' /startup')
    put(HKCU, RUN, 'com.squirrel.slack.slack', q(f'{L}/slack/slack.exe') + ' --process-start-args --startup')
    put(HKLM, RUN, 'SecurityHealth', '%windir%\\system32\\SecurityHealthSystray.exe')
    put(HKLM, RUN, 'RtkAudUService', q('C/Windows/System32/RtkAudUService64.exe') + ' -background')
    put(HKLM, RUN32, 'Adobe ARM', q(f'{PF86}/Common Files/Adobe/ARM/1.0/AdobeARM.exe'))
    put(HKCU, APPROVED + '\\Run', 'Steam', {'__bin__': '03000000' + '00' * 8})

    apps = [
        (HKLM, UNINSTALL, 'Google Chrome', 'Google Chrome', '130.0.6723.92', 'Google LLC', '20240105',
         f'{PF}/Google/Chrome/Application', q(f'{PF}/Google/Chrome/Application/130.0.6723.92/Installer/setup.exe') +
         ' --uninstall --channel=stable --system-level'),
        (HKLM, UNINSTALL32, 'WeChat', '微信', '3.9.12.17', '腾讯科技(深圳)有限公司', '20240310', f'{PF}/Tencent/WeChat',
         q(f'{PF}/Tencent/WeChat/Uninstall.exe')),
        (HKLM, UNINSTALL, '{A1B2C3D4-1111-4222-8333-0123456789AB}', 'FooSoft 设计工具 2.3', '2.3.0', 'FooSoft Inc.',
         '20250508', f'{PF}/FooSoft', 'MsiExec.exe /I{A1B2C3D4-1111-4222-8333-0123456789AB}'),
        (HKLM, UNINSTALL32, 'QuickView', '快看图大师', '5.2.0.1180', 'Fastview Network Technology Co., Ltd.', '20250918',
         f'{PF}/QuickView', q(f'{PF}/QuickView/uninst.exe')),
        (HKLM, UNINSTALL32, 'Steam', 'Steam', '2.10.91.91', 'Valve Corporation', '20230601', f'{PF86}/Steam',
         q(f'{PF86}/Steam/uninstall.exe')),
        (HKLM, UNINSTALL, 'Steam App 367520', 'Hollow Knight', '', 'Team Cherry', '20230915',
         f'{PF86}/Steam/steamapps/common/Hollow Knight', '"steam://uninstall/367520"'),
        (HKLM, UNINSTALL, '{AC76BA86-1033-1033-7760-BC15014EA700}', 'Adobe Acrobat (64-bit)', '24.004.20219',
         'Adobe', '20250701', f'{PF}/Adobe/Acrobat DC', 'MsiExec.exe /I{AC76BA86-1033-1033-7760-BC15014EA700}'),
        (HKCU, UNINSTALL, '{771FD6B0-FA20-440A-A002-3B3BAC16DC50}_is1', 'Microsoft Visual Studio Code (User)', '1.95.3',
         'Microsoft Corporation', '20251010', f'{L}/Programs/Microsoft VS Code',
         q(f'{L}/Programs/Microsoft VS Code/unins000.exe')),
        (HKLM, UNINSTALL, '{5af95fd8-a22e-458f-acee-c61bd787178e}',
         'Microsoft Visual C++ 2015-2022 Redistributable (x64) - 14.40.33810', '14.40.33810.0', 'Microsoft Corporation',
         '20240105', '', q('C/ProgramData/Package Cache/{9C1A2B3C-0000-4D2E-8F11-1234567890AB}/VC_redist.x64.exe') +
         ' /uninstall'),
        (HKLM, UNINSTALL, '{B2FE1952-0186-46C3-BAEC-A80AA35AC5B8}_Display.Driver', 'NVIDIA 图形驱动程序 566.36', '566.36',
         'NVIDIA Corporation', '20250920', f'{PF}/NVIDIA Corporation', q('C/Windows/System32/RunDll32.EXE') +
         ' "C:\\Program Files\\NVIDIA Corporation\\Installer2\\InstallerCore\\NVI2.DLL",UninstallPackage Display.Driver'),
    ]
    for hive, base, key, name, version, publisher, date, location, uninstall in apps:
        k = base + '\\' + key
        put(hive, k, 'DisplayName', name)
        if version:
            put(hive, k, 'DisplayVersion', version)
        put(hive, k, 'Publisher', publisher)
        put(hive, k, 'InstallDate', date)
        if location:
            put(hive, k, 'InstallLocation', native(location))
        put(hive, k, 'UninstallString', uninstall)
        if key.startswith('{') and 'MsiExec' in uninstall:
            put(hive, k, 'WindowsInstaller', 1)
    # entries real tools hide: system components and update packages
    put(HKLM, UNINSTALL + '\\{90160000-008C-0000-1000-0000000FF1CE}', 'DisplayName', 'Office 16 Click-to-Run Licensing Component')
    put(HKLM, UNINSTALL + '\\{90160000-008C-0000-1000-0000000FF1CE}', 'SystemComponent', 1)
    put(HKLM, UNINSTALL + '\\KB5044284', 'DisplayName', 'Update for Windows (KB5044284)')
    put(HKLM, UNINSTALL + '\\KB5044284', 'ParentKeyName', 'OperatingSystem')

    svc = lambda name, display, rel, args, start, status, desc, delayed=False: {  # noqa: E731
        'name': name, 'display_name': display, 'binpath': q(rel) + (' ' + args if args else ''),
        'start_type': start, 'status': status, 'description': desc, 'delayed': delayed}
    services = [
        svc('wuauserv', 'Windows Update', 'C/Windows/System32/svchost.exe', '-k netsvcs -p', 'manual', 'running',
            '启用检测、下载和安装 Windows 和其他程序的更新。'),
        svc('Spooler', 'Print Spooler', 'C/Windows/System32/spoolsv.exe', '', 'automatic', 'running', '后台处理打印作业。'),
        svc('WSearch', 'Windows Search', 'C/Windows/System32/SearchIndexer.exe', '/Embedding', 'automatic', 'running',
            '为文件、电子邮件和其他内容提供内容索引和搜索。', True),
        svc('DiagTrack', 'Connected User Experiences and Telemetry', 'C/Windows/System32/svchost.exe', '-k utcsvc -p',
            'automatic', 'running', '诊断和使用情况数据收集。'),
        svc('RtkAudioUniversalService', 'Realtek Audio Universal Service', 'C/Windows/System32/RtkAudUService64.exe', '',
            'automatic', 'running', 'Realtek Audio Universal Service'),
        svc('edgeupdate', 'Microsoft Edge Update Service (edgeupdate)', f'{PF86}/Microsoft/EdgeUpdate/MicrosoftEdgeUpdate.exe',
            '/svc', 'automatic', 'stopped', '使你的 Microsoft 软件保持最新状态。', True),
        svc('FooSoftLicense', 'FooSoft Licensing Service', f'{PF}/FooSoft/FooSoftSvc.exe', '', 'automatic', 'running',
            'FooSoft license validation'),
        svc('QVUpdateSvc', '快看图大师 更新服务', f'{PF}/QuickView/QVUpdateSvc.exe', '-service', 'automatic', 'running', ''),
        svc('Steam Client Service', 'Steam Client Service', f'{PF86}/Common Files/Steam/steamservice.exe', '/RunAsService',
            'manual', 'stopped', 'Steam Client Service'),
        svc('AdobeARMservice', 'Adobe Acrobat Update Service', f'{PF86}/Common Files/Adobe/ARM/1.0/armsvc.exe', '',
            'automatic', 'running', 'Adobe Acrobat Updater keeps your Adobe software up to date.'),
        svc('NVDisplay.ContainerLocalSystem', 'NVIDIA Display Container LS',
            f'{PF}/NVIDIA Corporation/Display.NvContainer/NVDisplay.Container.exe', '-s NVDisplay.ContainerLocalSystem',
            'automatic', 'running', 'Container service for NVIDIA root features'),
        svc('GoogleUpdaterService', 'Google Updater Service', f'{PF86}/Google/GoogleUpdater/updater.exe',
            '--system --windows-service', 'automatic', 'stopped', 'Keeps your Google software up to date.', True),
    ]

    info = {
        'chrome.exe': ('Google LLC', 'Google Chrome'), 'wechat.exe': ('Tencent', '微信'),
        'foosoft.exe': ('FooSoft Inc.', 'FooSoft 设计工具'), 'fooupdate.exe': ('FooSoft Inc.', 'FooSoft Update Helper'),
        'foosoftsvc.exe': ('FooSoft Inc.', 'FooSoft Licensing Service'),
        'quickview.exe': ('Fastview Network Technology Co., Ltd.', '快看图大师'),
        'qvhelper.exe': ('Fastview Network Technology Co., Ltd.', '快看图大师 资讯助手'),
        'qvupdatesvc.exe': ('Fastview Network Technology Co., Ltd.', '快看图大师 升级服务'),
        'steam.exe': ('Valve Corporation', 'Steam'), 'steamservice.exe': ('Valve Corporation', 'Steam Client Service'),
        'acrord32.exe': ('Adobe Inc.', 'Adobe Acrobat'), 'adobearm.exe': ('Adobe Inc.', 'Adobe Reader and Acrobat Manager'),
        'armsvc.exe': ('Adobe Inc.', 'Adobe Acrobat Update Service'), 'code.exe': ('Microsoft Corporation', 'Visual Studio Code'),
        'onedrive.exe': ('Microsoft Corporation', 'Microsoft OneDrive'),
        'securityhealthsystray.exe': ('Microsoft Corporation', 'Windows Security notification icon'),
        'rtkauduservice64.exe': ('Realtek Semiconductor', 'Realtek HD Audio Universal Service'),
        'nvdisplay.container.exe': ('NVIDIA Corporation', 'NVIDIA Container'),
        'svchost.exe': ('Microsoft Corporation', 'Host Process for Windows Services'),
        'spoolsv.exe': ('Microsoft Corporation', 'Spooler SubSystem App'),
        'searchindexer.exe': ('Microsoft Corporation', 'Microsoft Windows Search Indexer'),
        'updater.exe': ('Google LLC', 'Google Updater'), 'microsoftedgeupdate.exe': ('Microsoft Corporation', 'Microsoft Edge Update'),
    }
    return {
        'registry': reg,
        'services': services,
        'exe_info': {k: {'CompanyName': c, 'FileDescription': d, 'ProductName': d} for k, (c, d) in info.items()},
        'processes': ['msedge.exe', 'explorer.exe'],
        'admin': True,
        'recycle': [],
        'created': time.time(),
    }


# ---------------------------------------------------------------------------

class SandboxRegistry:
    """Same interface as startup.WinRegistry, backed by the sandbox state."""

    def __init__(self, sandbox: 'Sandbox'):
        self.sb = sandbox

    def _key(self, hive, path):
        keys = self.sb.state['registry'].get(hive, {})
        low = path.lower()
        for k in keys:
            if k.lower() == low:
                return k
        return None

    def values(self, hive, path):
        with self.sb.lock:
            k = self._key(hive, path)
            if k is None:
                return []
            return [(n, _decode(v)) for n, v in self.sb.state['registry'][hive][k].items()]

    def get(self, hive, path, name):
        for n, v in self.values(hive, path):
            if n.lower() == name.lower():
                return v
        return None

    def set_binary(self, hive, path, name, data: bytes):
        with self.sb.lock:
            k = self._key(hive, path) or path
            self.sb.state['registry'].setdefault(hive, {}).setdefault(k, {})[name] = {'__bin__': bytes(data).hex()}
            self.sb.save()

    def subkeys(self, hive, path):
        prefix = path.lower().rstrip('\\') + '\\'
        with self.sb.lock:
            out = []
            for k in self.sb.state['registry'].get(hive, {}):
                if k.lower().startswith(prefix):
                    rest = k[len(prefix):]
                    if rest and '\\' not in rest and rest not in out:
                        out.append(rest)
            return out

    def delete_key(self, hive, path):
        with self.sb.lock:
            k = self._key(hive, path)
            if k is not None:
                del self.sb.state['registry'][hive][k]
                self.sb.save()

    def delete_value(self, hive, path, name):
        with self.sb.lock:
            k = self._key(hive, path)
            if k is not None:
                self.sb.state['registry'][hive][k].pop(name, None)
                self.sb.save()


def _decode(value):
    if isinstance(value, dict) and '__bin__' in value:
        return bytes.fromhex(value['__bin__'])
    return value


class Sandbox:
    def __init__(self, root: str):
        self.root = os.path.abspath(root)
        self.lock = threading.RLock()
        self.state: dict = {}
        self.registry = SandboxRegistry(self)

    # -- paths ------------------------------------------------------------------------
    def native(self, rel: str) -> str:
        return os.path.join(self.root, *rel.split('/'))

    @property
    def drive_roots(self) -> list[str]:
        return [self.native(d) for d in DRIVES]

    def env(self) -> dict:
        home = self.native(f'C/Users/{USER}')
        local = os.path.join(home, 'AppData', 'Local')
        c = self.native('C')
        return {
            'SystemDrive': c, 'SystemRoot': os.path.join(c, 'Windows'), 'ProgramFiles': os.path.join(c, 'Program Files'),
            'ProgramFiles(x86)': os.path.join(c, 'Program Files (x86)'), 'ProgramData': os.path.join(c, 'ProgramData'),
            'USERPROFILE': home, 'LOCALAPPDATA': local, 'APPDATA': os.path.join(home, 'AppData', 'Roaming'),
            'TEMP': os.path.join(local, 'Temp'), 'PUBLIC': os.path.join(c, 'Users', 'Public'),
            'LOCALLOW': os.path.join(home, 'AppData', 'LocalLow'),
        }

    # -- lifecycle --------------------------------------------------------------------
    def exists(self) -> bool:
        return os.path.isfile(os.path.join(self.root, MARKER))

    def ensure(self) -> 'Sandbox':
        if not self.exists():
            self.build()
        else:
            self.load()
        return self

    def load(self):
        with self.lock:
            with open(os.path.join(self.root, 'state.json'), encoding='utf-8') as fh:
                self.state = json.load(fh)

    def save(self):
        with self.lock:
            tmp = os.path.join(self.root, 'state.json.tmp')
            with open(tmp, 'w', encoding='utf-8') as fh:
                json.dump(self.state, fh, ensure_ascii=False, indent=1)
            os.replace(tmp, os.path.join(self.root, 'state.json'))

    def reset(self):
        """Delete the simulated drives and build them again (settings and history are kept)."""
        if os.path.isdir(self.root) and not self.exists():
            raise RuntimeError('这个文件夹不是盘序的演练环境，不会删除它')
        with self.lock:
            for d in list(DRIVES) + ['state.json']:
                target = os.path.join(self.root, d)
                if os.path.isdir(target):
                    shutil.rmtree(target, onerror=_force_remove)
                elif os.path.exists(target):
                    os.remove(target)
            self.build()

    def build(self):
        with self.lock:
            os.makedirs(self.root, exist_ok=True)
            with open(os.path.join(self.root, MARKER), 'w', encoding='utf-8') as fh:
                fh.write('PanXu simulation. Safe to delete.\n')
            now = time.time()
            files, empty_dirs = _layout()
            dir_ages: dict[str, float] = {}
            for spec in files:
                path = self.native(spec.rel)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                _write(path, spec, now)
                parent = os.path.dirname(path)
                while parent.startswith(self.root) and parent != self.root:
                    dir_ages[parent] = min(dir_ages.get(parent, spec.age), spec.age)
                    parent = os.path.dirname(parent)
            for rel, age in empty_dirs:
                path = self.native(rel)
                os.makedirs(path, exist_ok=True)
                dir_ages[path] = age
            for d in self.drive_roots:
                os.makedirs(d, exist_ok=True)
            # folders carry the age of their newest content, deepest first
            for path in sorted(dir_ages, key=len, reverse=True):
                ts = now - dir_ages[path] * DAY
                winapi.set_file_times(path, created=ts, modified=ts, accessed=ts)
            self.state = _state(lambda rel: self.native(rel))
            self.save()

    # -- winapi hooks -----------------------------------------------------------------
    def is_admin(self) -> bool:
        return bool(self.state.get('admin', True))

    def known_folder(self, name: str) -> str:
        names = {'desktop': 'Desktop', 'documents': 'Documents', 'downloads': 'Downloads', 'pictures': 'Pictures',
                 'videos': 'Videos', 'music': 'Music'}
        return self.native(f'C/Users/{USER}/{names[name]}')

    def file_version_info(self, path: str) -> dict:
        if not path:
            return {}
        name = path.replace('\\', '/').rsplit('/', 1)[-1].strip('"').lower()
        return dict(self.state.get('exe_info', {}).get(name, {}))

    def processes(self) -> set[str]:
        return {p.lower() for p in self.state.get('processes', [])}

    def recycle_dir(self) -> str:
        return self.native('C/$Recycle.Bin/S-1-5-21-1000')

    def recycle_paths(self, items: list[str]) -> tuple[int, bool]:
        bin_dir = self.recycle_dir()
        os.makedirs(bin_dir, exist_ok=True)
        with self.lock:
            for path in items:
                if not os.path.lexists(path):
                    continue
                inside = any(paths.is_within(path, r) for r in self.drive_roots)
                if not inside:
                    continue  # never move anything from outside the simulation
                size = _tree_size(path)
                ident = uuid.uuid4().hex[:8].upper()
                stored = os.path.join(bin_dir, '$R' + ident + os.path.splitext(path)[1])
                shutil.move(path, stored)
                self.state.setdefault('recycle', []).append({'id': ident, 'original': path, 'stored': stored,
                                                             'time': time.time(), 'size': size})
            self.save()
        return 0, False

    def recycle_bin_info(self) -> tuple[int, int]:
        with self.lock:
            rows = [r for r in self.state.get('recycle', []) if os.path.lexists(r['stored'])]
            return sum(r['size'] for r in rows), len(rows)

    def recycle_items(self) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.state.get('recycle', []) if os.path.lexists(r['stored'])][::-1]

    def restore(self, ident: str) -> str:
        with self.lock:
            row = next((r for r in self.state.get('recycle', []) if r['id'] == ident), None)
            if row is None or not os.path.lexists(row['stored']):
                raise FileNotFoundError('回收站里没有这一项')
            if os.path.lexists(row['original']):
                raise FileExistsError('原位置已经有同名文件')
            os.makedirs(os.path.dirname(row['original']), exist_ok=True)
            shutil.move(row['stored'], row['original'])
            self.state['recycle'].remove(row)
            self.save()
            return row['original']

    def empty_recycle_bin(self) -> bool:
        with self.lock:
            bin_dir = self.recycle_dir()
            if os.path.isdir(bin_dir):
                shutil.rmtree(bin_dir, onerror=_force_remove)
            self.state['recycle'] = []
            self.save()
            return True

    def disk_usage(self, root: str):
        import collections
        usage = collections.namedtuple('usage', 'total used free')
        for letter, (_, total, base) in DRIVES.items():
            drive = self.native(letter)
            if paths.is_within(root, drive):
                used = min(total, base + _tree_size(drive))
                return usage(total, used, total - used)
        return shutil.disk_usage(root)

    def drives(self) -> list[dict]:
        out = []
        for letter, (label, _, _) in DRIVES.items():
            root = self.native(letter)
            u = self.disk_usage(root)
            out.append({'root': root + os.sep, 'label': label, 'fstype': 'NTFS', 'total': u.total, 'used': u.used,
                        'free': u.free, 'system': letter == 'C', 'removable': False, 'letter': letter + ':'})
        return out

    # -- system changes ---------------------------------------------------------------
    def services(self) -> list[dict]:
        with self.lock:
            return [dict(s) for s in self.state['services']]

    def sc(self, argv: list[str]) -> tuple[int, str]:
        """Pretend to be sc.exe: ``config <name> start= <type>`` and ``stop <name>``."""
        with self.lock:
            if len(argv) >= 3:
                svc = next((s for s in self.state['services'] if s['name'] == argv[2]), None)
                if svc is None:
                    return 1060, '[SC] OpenService 失败 1060：指定的服务未安装。'
                if argv[1] == 'config' and len(argv) >= 5:
                    mode = argv[4]
                    svc['start_type'] = {'auto': 'automatic', 'delayed-auto': 'automatic', 'demand': 'manual',
                                         'disabled': 'disabled'}.get(mode, svc['start_type'])
                    svc['delayed'] = mode == 'delayed-auto'
                    self.save()
                    return 0, '[SC] ChangeServiceConfig 成功'
                if argv[1] == 'stop':
                    svc['status'] = 'stopped'
                    self.save()
                    return 0, '[SC] ControlService 成功'
            return 87, '参数错误'

    def set_hibernate(self, enabled: bool) -> None:
        """Stand-in for powercfg /hibernate on|off: hiberfil.sys appears or disappears."""
        path = self.native('C/hiberfil.sys')
        if enabled and not os.path.exists(path):
            _write(path, _Spec('C/hiberfil.sys', 3270 * MB, age=0), time.time())
        elif not enabled and os.path.exists(path):
            os.remove(path)

    def run_cleanup_command(self, rule_id: str) -> None:
        """Stand-ins for DISM, Delete-DeliveryOptimizationCache and Disk Cleanup handlers."""
        targets = {
            'delivery_opt': ['C/Windows/ServiceProfiles/NetworkService/AppData/Local/Microsoft/Windows/DeliveryOptimization/Cache'],
            'component_store': ['C/Windows/WinSxS/Temp/PendingDeletes'],
            'windows_old': ['C/Windows.old'],
            'upgrade_leftovers': ['C/$WINDOWS.~BT', 'C/$WINDOWS.~WS'],
        }.get(rule_id, [])
        for rel in targets:
            path = self.native(rel)
            if not os.path.isdir(path):
                continue
            if rule_id in ('windows_old', 'upgrade_leftovers'):
                shutil.rmtree(path, onerror=_force_remove)
            else:
                for name in os.listdir(path):
                    full = os.path.join(path, name)
                    shutil.rmtree(full, onerror=_force_remove) if os.path.isdir(full) else os.remove(full)

    def uninstall(self, hive: str, key: str) -> dict:
        """Act like a typical uninstaller: remove the program folder, its registry entry, Run entries and
        services — and leave its AppData behind, as most uninstallers do."""
        with self.lock:
            values = dict(self.registry.values(hive, key))
            if not values:
                raise FileNotFoundError('这个程序已经不在了')
            location = values.get('InstallLocation') or ''
            removed = 0
            if location and any(paths.is_within(location, r) for r in self.drive_roots) and os.path.isdir(location):
                removed = _tree_size(location)
                shutil.rmtree(location, onerror=_force_remove)
            self.registry.delete_key(hive, key)
            if location:
                for h in (HKCU, HKLM):
                    for run in (RUN, RUN32):
                        for name, cmd in self.registry.values(h, run):
                            if isinstance(cmd, str) and paths.key(location) in paths.key(cmd.strip('"')):
                                self.registry.delete_value(h, run, name)
                self.state['services'] = [s for s in self.state['services']
                                          if paths.key(location) not in paths.key(s['binpath'].replace('"', ''))]
            self.save()
            return {'removed_bytes': removed, 'location': location}


def _write(path: str, spec: _Spec, now: float):
    with open(path, 'wb') as fh:
        if spec.data is not None:
            fh.write(spec.data)
        else:
            header = f'PANXU-SIM {spec.rel}\n'.encode('utf-8')[:min(spec.size, 512)]
            if spec.sparse and not winapi.make_sparse(fh):
                raise OSError('这个磁盘不支持稀疏文件（需要 NTFS），无法创建演练环境')
            fh.write(header)
            if spec.size > len(header):
                # Seek past the end and write the last byte: the gap stays a hole. (truncate() would
                # not do: on Windows the C runtime extends files by writing zeros, sparse or not.)
                fh.seek(spec.size - 1)
                fh.write(b'\0')
    created = modified = now - spec.age * DAY
    winapi.set_file_times(path, created=created, modified=modified, accessed=now - spec.atime_age * DAY)


def _tree_size(path: str) -> int:
    if os.path.isfile(path) or os.path.islink(path):
        try:
            return os.lstat(path).st_size
        except OSError:
            return 0
    total = 0
    for dirpath, _, files in os.walk(path):
        for name in files:
            try:
                total += os.lstat(os.path.join(dirpath, name)).st_size
            except OSError:
                pass
    return total


def _force_remove(func, path, _exc):
    try:
        os.chmod(path, 0o700)
        func(path)
    except OSError:
        pass


def default_root(data_dir: str) -> str:
    return os.path.join(data_dir, 'sandbox')


def activate(sandbox: Sandbox) -> None:
    """Route every system access of this process into the simulation."""
    sandbox.ensure()
    paths.set_simulated_environment(sandbox.env(), {sandbox.native(d): d + ':' for d in DRIVES})
    winapi.set_simulation(sandbox)


def deactivate() -> None:
    paths.set_simulated_environment(None)
    winapi.set_simulation(None)
