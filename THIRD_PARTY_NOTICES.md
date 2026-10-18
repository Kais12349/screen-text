# 第三方组件

便携包包含第三方运行环境、库和模型；其权利归各自权利人所有，适用各自随附的许可条款。

- CPython 3.11.9 x64：https://www.python.org/ 。完整许可随附于 `runtime/LICENSE.txt`。
- RapidOCR 1.4.4：https://github.com/RapidAI/RapidOCR ，Apache-2.0。许可随附于 `licenses/RapidOCR-LICENSE.txt`。
- PP-OCR 模型由 RapidOCR 包提供，来源于 PaddleOCR：https://github.com/PaddlePaddle/PaddleOCR 。模型文件位于 `vendor/rapidocr_onnxruntime/models/`，许可随附于 `licenses/PaddleOCR-LICENSE.txt`。
- ONNX Runtime：https://github.com/microsoft/onnxruntime 。许可随附于 `vendor/onnxruntime/LICENSE`。
- NumPy、OpenCV、Pillow、Shapely、pyclipper、PyYAML 等组件的版本见 `requirements.txt`。许可和软件包元数据保留在 `vendor` 下对应的 `.dist-info` 目录中；部分包另在包目录内提供许可文件。

Windows 系统的 .NET Framework、字体和系统 DLL 不作为本项目源码的一部分分发。
