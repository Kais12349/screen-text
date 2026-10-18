# 截图取字 · Screen Text

Windows 离线截图取字小工具。框选屏幕，识别中英文、数字和标点，在窗口里检查、修改后再复制。

## 下载使用

从 [Releases](https://github.com/Kais12349/screen-text/releases) 下载 `screen-text-windows-x64.zip`，将整个文件夹解压到 **D 盘、E 盘等非 C 盘**，双击 `截图取字.exe`。

便携包包含 Python 运行环境、OCR 模型和依赖，不需要安装 Python，不需要 API 密钥，运行时无需联网。请保留 `runtime`、`vendor` 和 `ocr.py`，不要只移动 exe。

1. 点击「框选截图」，或在工具运行时同时按住 **A + S + D**。
2. 按住鼠标左键拖动框选，松开后自动识别；Esc 或右键取消。
3. 在右侧检查并修改文字。
4. 点击「复制全部文字」。复制的是当前编辑后的内容。

也可以点击「打开图片」识别 PNG、JPG、BMP、TIFF 图片。依次输入 asd 不会触发截图；长按组合键只触发一次。关闭窗口后快捷键停用。

## 识别与文件位置

- 使用 RapidOCR / PP-OCRv4 本地模型，支持中文、英文、数字和标点。
- 对常见代码截图，结合文字位置保留行序、空行、缩进和下划线，并改善 Python 类定义、类型标注中空格与 `|` 的识别。支持连续属性赋值代码，改善高亮背景下的等号、运算符间距及鼠标光标干扰。
- 输出可编辑的纯文本；小字、模糊文字、复杂背景、引号以及 `l / 1`、`O / 0` 等仍需人工检查，不保证逐字符准确。
- 本工具的临时图片与识别结果只写入工具目录的 `cache`，识别结束后删除；不上传截图、不保存识别历史。
- 本工具及 OCR 子进程的 `TEMP` / `TMP` 指向该目录。Windows 自身的日志、系统文件由操作系统管理。

要求：Windows 10 / 11 x64，系统提供 .NET Framework 4.x；工具文件夹需要写入权限。

## 源码与编译

`ScreenText.cs` 是 WinForms 界面、截图与快捷键处理；`ocr.py` 是本地 OCR 与文字排版；`ScreenText.manifest` 配置 DPI 行为。

仓库保存源码，体积较大的运行环境和依赖放在 Release 便携包中。重新编译：

1. 将本仓库克隆或解压到非 C 盘。
2. 从对应 Release 的便携包复制 `runtime` 和 `vendor` 到源码目录。
3. 在源码目录运行 `powershell -ExecutionPolicy Bypass -File .\build.ps1`。
4. 双击生成的 `截图取字.exe`。

编译使用 Windows 自带的 .NET Framework C# 编译器。OCR 运行环境为 CPython 3.11.9 x64，依赖版本记录在 `requirements.txt`。

可运行 `截图取字.exe --self-test` 生成中英文测试图及结果，输出在本目录 `checks` 中。该测试输出识别结果，需自行检查内容；它不自动判定识别准确率。

发布前已在实际窗口验证同时按下 A/S/D、取消截图、识别、编辑后复制，并检查中文、英文及 Python 类型定义截图。用户截图、编辑内容备份和本地调试输出均不包含在仓库与便携包中。

## 第三方组件

依赖来源和许可位置见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。便携包保留各组件的许可文件。
