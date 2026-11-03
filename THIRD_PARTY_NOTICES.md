# Third-party notices / 第三方许可说明

> **1.0 说明（2026-11-03）：** 1.0 重写后的运行依赖仍是 pywebview、psutil（以及 pywebview 在 Windows 上依赖的 pythonnet 等）和 PyInstaller，下列清单是针对 0.29 便携包核对的。1.0 的代码本身不再使用 SQLite；实际收集的组件以首次 1.0 打包结果为准，届时需要重新核对本清单。

盘序（PanXu）的原创代码许可与下列第三方软件的许可分别适用。项目的 PolyForm Noncommercial 1.0.0 及 Liss 署名不改变第三方软件的版权、许可、商标或其接收者已有权利；本文也不把第三方成果归为盘序原创。第三方作者没有因此为盘序背书。

本清单核对日期为 **2026-11-02**，依据本机已安装发行包元数据、其原始许可文件和当时 `dist/PanXu` 的实际归档/资源。它服务于当前本地便携包与私有代码库维护，不是完整法律合规保证。更新依赖、收集钩子、运行时或公开分发方式后需要重新核对；当前有明确列出的来源待办，见下文。

完整文件清单、来源、版本、字节数与 SHA-256 位于 [licenses/third-party/manifest.json](licenses/third-party/manifest.json)。原文按收到的字节保留，未把署名改成项目作者；没有为排版而修补上游许可文本。清单中的源码文本是为保留第三方对应源码与许可而附带，不是盘序的新执行模块。

## 实际收集的 Python 软件

| 软件 | 核对版本 | 原文/说明 |
|---|---|---|
| CPython / Python 标准库 | 3.14.0，Windows x64 | [安装发行版完整 LICENSE](licenses/third-party/python/LICENSE.txt)，包含 PSF、历史条款及 Windows binary build 附加条款；另保留[发行版所附 incorporated-software 许可文档](licenses/third-party/python/incorporated-software-license.rst.txt)。 |
| pywebview | 6.2.1 | [BSD-3-Clause](licenses/third-party/pywebview/LICENSE)。其供应的 JS、互操作库和 SDK 不因此一概取得同一许可，另见下表。 |
| psutil | 7.2.2 | [BSD-3-Clause](licenses/third-party/psutil/LICENSE)。 |
| pythonnet | 3.1.0 | [MIT 原文](licenses/third-party/pythonnet/LICENSE)与[作者名单](licenses/third-party/pythonnet/AUTHORS.md)；其目录中的 Microsoft facade DLL 不能据此笼统归为 pythonnet 原创。 |
| clr_loader | 0.3.1 | [MIT 原文](licenses/third-party/clr_loader/LICENSE)。 |
| cffi | 2.0.0 | [MIT 原文](licenses/third-party/cffi/LICENSE)与[作者名单](licenses/third-party/cffi/AUTHORS)。 |
| pycparser | 3.0 | [BSD-3-Clause](licenses/third-party/pycparser/LICENSE)。 |
| proxy_tools | 0.1.0 | [上游完整 BSD 条款](licenses/third-party/proxy_tools/LICENSE.txt)与[实际随包源码版权头](licenses/third-party/proxy_tools/BUNDLED-COPYRIGHT.txt)。已安装元数据写 MIT，但源码头和上游 LICENSE 写 BSD 并注明 Armin Ronacher、Jonathan Tushman；这里保留原文，不仅采信元数据标签。 |
| bottle | 0.13.4 | [MIT 原文](licenses/third-party/bottle/LICENSE)。 |
| typing_extensions | 4.15.0 | [PSF-2.0 及所附条款](licenses/third-party/typing_extensions/LICENSE)。 |
| Jinja2 | 3.1.6 | [BSD-3-Clause](licenses/third-party/jinja2/LICENSE.txt)。 |
| MarkupSafe | 3.0.3 | [BSD-3-Clause](licenses/third-party/markupsafe/LICENSE.txt)。 |
| packaging | 26.2 | [许可说明](licenses/third-party/packaging/LICENSE)、[Apache-2.0](licenses/third-party/packaging/LICENSE.APACHE)、[BSD 原文](licenses/third-party/packaging/LICENSE.BSD)均保留。 |
| setuptools | 81.0.0 | [本体 MIT 原文](licenses/third-party/setuptools/LICENSE)；vendored 子组件及验证代码有各自许可，不能用这一行代替。 |

实际归档中出现的 setuptools vendored 软件包括：backports.tarfile 1.2.0、importlib_metadata 8.7.1、jaraco.text 4.0.0、jaraco.context 6.1.0、jaraco.functools 4.4.0、more_itertools 10.8.0、packaging 26.0、tomli 2.4.0、wheel 0.46.3、zipp 3.23.0。其原始许可保留在 `licenses/third-party/setuptools-vendor/`，逐文件来源与 hash 见 manifest。仅在已安装 setuptools 中存在、未进入实际 PYZ 或物理包的 autocommand、platformdirs 等没有被误报为已分发软件。

setuptools 的 `config/_validate_pyproject` 中还包括 validate-pyproject 的 MPL-2.0 代码和 fastjsonschema 的 BSD 代码。[对应完整 NOTICE](licenses/third-party/setuptools/config/_validate_pyproject/NOTICE/NOTICE.txt)与[配置 NOTICE](licenses/third-party/setuptools/config/NOTICE/NOTICE.txt)均保留。实际被收集的 6 份验证模块以原始字节保存在 `licenses/third-party/setuptools/covered-source/*.py.txt`，附相应来源/hash；这些文件的第三方许可不被盘序的原创代码许可取代。

## JS、SDK 与原生库

| 组件 | 核对版本 / 来源 | 保留材料与范围 |
|---|---|---|
| domJSON | pywebview 内 JS 标为 0.1.2 | [上游 MIT](licenses/third-party/domjson/LICENSE.md)及[随包的 Alex Zaslavsky 版权头](licenses/third-party/domjson/BUNDLED-COPYRIGHT.txt)。上游与随包头的年份差异原样保留。 |
| Microsoft WebView2 SDK | 1.0.3856.49 | 来自[该版本官方 NuGet](https://www.nuget.org/packages/Microsoft.Web.WebView2/1.0.3856.49)的 [LICENSE](licenses/third-party/webview2-sdk/LICENSE.txt)与 [NOTICE](licenses/third-party/webview2-sdk/NOTICE.txt)。已对两份 .NET DLL 与三种架构的 Loader 共 5 个实际文件逐一比对官方包 SHA-256，匹配路径记录在 manifest。 |
| OpenSSL | 3.0.18 | [该版本 Apache-2.0 原文](licenses/third-party/openssl/LICENSE.txt)；另见 Python 所附许可资料。 |
| SQLite | 3.50.4 | SQLite 的[官方 public-domain 声明](https://sqlite.org/copyright.html)，其原始 HTML 以[文本文件](licenses/third-party/sqlite/COPYRIGHT.html.txt)保留，未作为应用页面执行。 |
| zlib-ng | CPython 3.14.0 外部依赖固定为 2.2.4；运行时报告兼容版本 `1.3.1.zlib-ng` | [该版本原文](licenses/third-party/zlib-ng/LICENSE.md)。 |
| XZ / liblzma | CPython 外部依赖固定为 5.2.5 | [原始 COPYING](licenses/third-party/xz/COPYING)区分 liblzma 的 public-domain 范围和其他工具的不同许可；本便携包没有据此宣称分发 XZ 命令行工具。 |
| bzip2、libffi、Zstandard、Expat、libmpdec 等 Python 所附组件 | 依本次 Python 发行版；运行时/源码固定版本记录于 manifest | 保留 Python 安装发行版附带的完整原文和 incorporated-software 许可材料，而非只附一个 PSF 标签。未把文档中提及的每个可选库都声称为实际已打包组件。 |

WebView2 **SDK** 的 wrapper/loader 与用户机器上另行提供的 WebView2 **Runtime** 是不同分发对象。本包未收集 Evergreen Runtime 安装器或整个浏览器运行时；用户机器上的 Windows、.NET Framework 与浏览器运行时仍适用各自原有条款。[Microsoft 官方分发说明](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/distribution)。pywebview 自带的 WebBrowserInterop、polyfill 与 Android jar 仍保留上游文件；不把它们写成盘序原创，也不因 Windows 流程未调用就忽略物理包中的文件。

## 构建工具与嵌入的运行材料

PyInstaller **6.21.0** 的[原始 COPYING](licenses/third-party/pyinstaller/COPYING.txt)包含 GPL 条款、bootloader exception 和 runtime-hook 的 Apache 条款。官方说明区分打包工具与生成的组合应用；不能简单地把应用标为 GPL，也不能忽略应用自身依赖的许可。[官方说明](https://pyinstaller.org/en/stable/license.html)。本项目没有修改 PyInstaller，未将其整个开发环境作为应用运行时分发；原文在此作为保守保留和构建来源记录。

另保留本机构建使用的 pyinstaller-hooks-contrib **2026.6**、altgraph **0.17.5**、pefile **2024.8.26**、pywin32-ctypes **0.2.3** 原文。它们在 manifest 中标为 build-only，不能因保留许可证就误认作应用已包含其全部代码。实际嵌入的 bootstrap/runtime-hook 名称也单独记录。

## 公开分发前仍需核对

- 本次 pythonnet 发行内容带有 **96 个 Microsoft/.NET facade DLL**。manifest 保存相对路径、大小/hash 和待办；尚未精确建立每个文件与原始上游分发包及再分发条款的对应关系。NETStandard.Library 2.0.3 的官方许可/第三方说明仅保留为参考材料，**不表示这些 DLL 已与该包匹配**，也不表示把 Microsoft 文件重新许可为 MIT 或 PolyForm。
- Python Windows 发行版与包内 UCRT/VC runtime 等 Microsoft 文件有原始供应商条件；保留的 Python LICENSE 包含其 Windows binary build 附加条款。公开分发时还须核对实际微软运行材料、适用版本和再分发条件，不能把项目许可证当作替代。
- 当前清单针对已核对的本地便携包；依赖升级、新平台、不同 Qt/CEF 后端、另行打包浏览器或其他 SDK 都会改变范围。公开二进制发布前应重新检查实际构建及上述 pending 项。本文不承诺当前已具备公开分发的全部授权，也不擅自扩大任何上游许可。
