# 确定性工具的参考价值

核对日期：2026-11-02。只读取公开文档和固定版本源码；未安装、运行或接入这些工具，未复制上游实现。

## BleachBit

源码快照：`659f4059cf4823ba09c1ed5af45eb336a3ee47a1`。它是规则驱动清理器，不是自主调查智能体。

- CleanerML 将检测范围、选项、提示和具体动作写成声明式定义。我们应借鉴“知识与动作实现分开、可审阅、可版本化”，将其变为 LLM 可查的应用经验，而非把整个产品改回规则匹配器。[官方 CleanerML 说明](https://docs.bleachbit.org/cml/cleanerml/)、[定义解析实现](https://github.com/bleachbit/bleachbit/blob/659f4059cf4823ba09c1ed5af45eb336a3ee47a1/bleachbit/CleanerML.py#L250)。
- Chromium 定义有运行程序信息，并把缓存等选项分开。启发是将“软件正在运行”“清理哪类数据”“有何影响”做成工具事实和动作前置条件，不让模型只凭一个 Cache 名称判断。[Chromium 定义](https://github.com/bleachbit/bleachbit/blob/659f4059cf4823ba09c1ed5af45eb336a3ee47a1/cleaners/chromium.xml#L14)。
- Worker 用同一命令接口传入预览或真实执行标记，并逐步交回 UI 处理进度、取消与错误。适合借鉴共用生命周期，不能据此认定任何预览都能完整复现真实修改或恢复。[Worker.execute](https://github.com/bleachbit/bleachbit/blob/659f4059cf4823ba09c1ed5af45eb336a3ee47a1/bleachbit/Worker.py#L98)。

不采用：将删文件、注册表修改、truncate/shred 等所有动作直接暴露为通用模型命令；也不将 CleanerML 的路径命中当成未知软件清理的完整证明。其代码/定义有 GPL 许可约束，本轮仅参考设计。

## Czkawka

源码快照：`d776b0a8559fc3db1429fa29b91608026e97cd01`。这是本地文件分析工具，不能代替用途判断或用户意愿理解。

- 重复检查具有尺寸候选、预哈希、完整哈希阶段，并分别缓存、报告进度和响应停止。启发是将昂贵字节读取放在逐级缩小的候选集合上；模型只需取得证据摘要和重复组。[预哈希阶段](https://github.com/qarmin/czkawka/blob/d776b0a8559fc3db1429fa29b91608026e97cd01/czkawka_core/src/tools/duplicate/core.rs#L362)、[完整哈希阶段](https://github.com/qarmin/czkawka/blob/d776b0a8559fc3db1429fa29b91608026e97cd01/czkawka_core/src/tools/duplicate/core.rs#L541)。
- 明确按文件身份过滤硬链接，Windows 和 Unix 使用各自身份信息。启发是把“两个路径”“两份内容”和“能释放多少物理空间”分别建模。[硬链接过滤](https://github.com/qarmin/czkawka/blob/d776b0a8559fc3db1429fa29b91608026e97cd01/czkawka_core/src/tools/duplicate/mod.rs#L138)。
- 工具核心与不同界面分开。我们也应把扫描、哈希、应用盘点作为可独立调用和验证的能力，让列表与 LLM 使用同一事实层，而不是每个入口重扫一次。

不采用：将相同文件名、尺寸或预哈希当成可删除证明；将精确重复自动等同于多余副本；为获得这些思想而引入整个 Rust GUI 或模型无关工具的所有功能。当前盘序已有尺寸分组和 SHA-256，后续增量价值是预筛、可续接缓存和更好的 IO 预算。

## 对我们方案的直接结论

规则知识库、确定性观察工具、LLM 调查判断和动作适配器各自演进。知识向模型提供线索和能力说明；扫描结果携带范围、时间和未检查项；动作只执行用户明确审阅的具体对象。工具事实能减少模型 token 与 IO，但不接管用户目标，也不将处理路径写死。
