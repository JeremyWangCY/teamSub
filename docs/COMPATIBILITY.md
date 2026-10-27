# Windows 兼容性与验证范围

本文记录配置支持范围、实际运行证据和未覆盖场景。测试日期为 2026-10-27。没有在所有电脑上实测，以下结果不能作为所有硬件、企业策略或会议客户端版本均可运行的保证。

## 支持目标与安装方式

发行包以 Windows 10 / Windows 11 x64 为支持目标。安装器配置为 `MinVersion=10.0`、`ArchitecturesAllowed=x64compatible`、`PrivilegesRequired=lowest`，默认安装在当前用户的 `Apps\TeamsSub` 目录。冻结包包含 Python、Tk、UI Automation、pywin32 和 Pillow，使用者无需另外安装 Python 或运行依赖。

Windows 11 ARM64 的 x64 模拟执行尚未验证，不能据安装器允许 `x64compatible` 就声称已支持 ARM64；未提供原生 ARM64 包。Windows x86、Windows 10 ARM64、Windows 7/8 不在本次发行验证范围内。

配置、缓存和会议记录默认保存在程序目录，可通过 `TEAMSSUB_DATA_DIR` 指定另一可写目录。默认按用户安装目录可避免一般的 `Program Files` 写入限制；便携包放入只读目录、企业受控目录或其他账户无写入权的目录时，需要先安排可写的数据目录。自检成功只证明自建临时目录可写，不代表任意安装目录可写。

## 本机实际验证矩阵

| 场景 | 环境与权限 | 结果 / 边界 |
| --- | --- | --- |
| 源码运行时依赖 | Windows 11 专业工作站版，OS `10.0.26300`，AMD64，Python 3.12.10，64 位进程，管理员令牌 | 8 项真实探针全部通过，见下表 |
| 正式冻结 EXE 0.1.13 | 上述主机，管理员令牌，发行二进制 | 退出码 0，`frozen=true`，8 项全部通过 |
| 冻结 EXE 的中文空格路径 | 上述主机，隔离副本位于 `中文 空格 运行兼容` 目录 | 退出码 0，8 项全部通过；启动目录中未生成配置或会议文件 |
| 冻结 EXE 的非管理员进程 | 上述中文空格副本，`CreateRestrictedToken` 禁用管理员 SID、移除特权，再将该子令牌设为 Medium integrity | 实际子进程 `elevated=false`，退出码 0，8 项全部通过；未修改账户、UAC、文件 ACL 或系统安全设置 |
| 安装、覆盖升级、卸载的受限 Medium 进程 | 正式安装器源码派生隔离 AppId/mutex、去图标后编译；三个实际安装器进程均禁用管理员组，Medium integrity；目标目录含中文和空格 | 三次退出码均为 0；已安装 EXE 与最终 payload 哈希一致，配置/会议 fixture 哈希不变，卸载移除 EXE 并保留数据。未启动正式应用 |
| 中文和空格路径 | 上述主机，自建临时目录下的 `中文 空格 自检` | SQLite 新建、重开、原文/译文编辑、会议改名、Markdown 投影，以及 9 种格式与内容组合导出均通过 |
| Explorer 降权尝试 | 尝试经桌面 `Shell.Application.ShellExecute` 启动隔离自检 | 该主机桌面 Explorer 自身也使用提权令牌，子进程报告 `elevated=true`；该次不计为普通用户实测，随后以 restricted Medium token 获得上行非管理员进程证据 |
| Windows 10 x64 | 安装与 API 以此为最低配置目标 | 尚无真实 Windows 10 机器/虚拟机证据，未声称实测通过 |
| Windows 11 ARM64 / x64 模拟 | 无真实 ARM64 设备 | 未实测 |
| 多显示器、125%–300% 缩放、远程桌面 | 尚无完整物理显示环境矩阵 | 未实测；需另做外观与拖动验收 |
| 企业杀毒、代理、禁用 UI Automation、禁用 DPAPI、网络磁盘和受控文件夹 | 未提供相应管理环境 | 未实测，依赖被策略阻止时不能保证正常工作 |

运行时探针实际结果：

| JSON 检查项 | 核查内容 | 本机源码结果 |
| --- | --- | --- |
| `tk_widgets` | 创建隐藏 Tk 窗口、输入控件、Canvas、PhotoImage 后销毁 | 通过 |
| `pillow` | Pillow RGBA 位图读写及 ImageTk 实际创建 Tk 图像 | 通过 |
| `uiautomation_com` | UI Automation / COM 初始化及销毁；不枚举用户窗口 | 通过 |
| `dpapi_roundtrip` | 随机临时字节经当前账户 DPAPI 加解密，内容不输出 | 通过 |
| `sqlite_unicode_persistence_and_edits` | CaptionStore 持久化、重开、原译文编辑、改名及可读投影 | 通过 |
| `unicode_exports_all_modes` | MD / TXT / SRT × 原文 / 译文 / 对照；检查内容包含及排除规则 | 通过 |
| `icon_resource` | 实际图标资源可以读取 | 通过 |
| `version_metadata` | 源码版本资源存在；冻结时改为读取 EXE 版本资源 | 通过（源码模式） |

原始 JSON 报告由发布核验流程保存：源码为 `runtime-diagnostics-source.json`，正式冻结包为 `runtime-diagnostics-frozen.json`，中文空格副本为 `runtime-diagnostics-frozen-unicode.json`，非管理员 Medium 令牌为 `runtime-diagnostics-frozen-medium.json`。`runtime-diagnostics-source-standard-user.json` 是最初 Explorer 降权尝试的实际报告，其 `elevated=true`，不作为普通用户成功证据。Medium 进程测试不能替代另一独立标准账户的完整桌面与企业策略测试。

本次冻结验证绑定 0.1.13 EXE SHA256：`02B06FEADCE75EDEF123D0CC4FE05ABF62745916406DB8B418BA2D1F2D56F2A6`。`runtime-diagnostics-frozen-evidence.json` 记录该二进制以及三份冻结报告各自的 SHA256、退出码和权限，避免把中间构建结果混作最终包证据。安装器生命周期测试另存记录。

`installer-validation-medium-0.1.13.json` 记录实际受限 Medium 安装、覆盖升级和卸载结果，绑定同一 payload SHA256。测试由父 Python 准备及校验自有 fixture，再直接以受限令牌执行三个 Inno 子进程；该主机无法在这种令牌下初始化 PowerShell，因此未把失败的 PowerShell 启动算作安装验证。每次子进程令牌均核对 `AdministratorsGroupEnabled=false`，完整性标签为 `S-1-16-8192`。从管理员令牌派生的原始 `TokenElevation` 标志仍为 true，原样保留在证据中；本诊断的 `elevated` 字段采用 `IsUserAnAdmin()` 的有效管理员组判断。这里证明受限 Medium 权限下的实际运行，不等同独立标准账户完整登录环境。未创建用户或改变 UAC、安全策略、账户权限和文件 ACL。

## 离线自检与隐私

发行程序支持在启动 UI、单实例锁、配置加载之前运行：

```powershell
& '.\TeamsSub.exe' --self-test "$PWD\runtime-report.json"
```

报告路径必须是绝对路径，父目录必须已存在且可写。成功返回进程退出码 0；检查失败或无法写出报告时返回非零值。开发环境可调用 `runtime_diagnostics.run_self_test(absolute_report_path)`，返回 `bool`。

自检不加载 `appconfig.json`，不读取现有会议、不连接翻译服务或外部网络、不取得用户窗口文字、不占用应用的单实例锁。探针数据仅在自己创建的临时目录中生成并清理，保留的只有调用者明确指定的 JSON 报告。报告只含 OS 版本、架构、Python 版本、是否冻结/提权、检查布尔结果和异常类型；不记录用户名、个人路径、密钥、随机 DPAPI 测试数据或会议内容。

自检不能证明真实 Teams / 飞书版本的字幕结构、实际翻译接口额度/模型、托盘与多显示器视觉效果、企业网络访问和完整安装回滚均已验收。升级安装、取消、失败恢复、原目录覆盖、数据保留及成功重启需要独立的安装器集成测试记录。

## 发布前与后续环境验收

1. 用本次实际冻结 EXE 执行自检，再从中文及含空格的隔离目录执行，确认所有检查为 true。
2. 在真实普通用户令牌的 Windows 10 x64 和 Windows 11 x64 上，执行安装、首次启动、设置保存、会议采集/编辑/导出、托盘关闭与任务栏最小化、升级和卸载保留数据。
3. 在 1366×768、1920×1080 和高 DPI 环境检查主窗口不遮挡任务栏、控件可达，悬浮窗四角、圆点、拖动与缩放可用。主窗口使用工作区尺寸约束时，也需要真实环境复核视觉表现。
4. 检查更新下载断网、校验失败、取消、目标目录无写权限、会议保存失败和安装失败等路径，核对旧版可重新启动且原数据保留。详情以本次安装器集成测试及发布检查记录为准。

以上未实际完成的环境项应继续标为未验证，不能用配置或模拟测试替代真实系统结果。
