# stealth.min.js

自动生成的反检测规避脚本,不要手改。

- **来源**: https://github.com/berstend/puppeteer-extra/tree/master/packages/extract-stealth-evasions
- **许可**: MIT
- **sha256**: `88a36e3c52b30bce3d99bbc7773c56d8…`(与引入时一致,升级请同步更新)

## 它盖住什么

`add_init_script` 在每个页面加载前注入,覆盖这些检测点:

| 检测点 | 为什么对我们重要 |
|--------|------------------|
| `UNMASKED_RENDERER` / `WebGLRenderingContext` | **本容器实测报 `SwiftShader`**(软件渲染),是"这是服务器"的强信号 |
| `navigator.webdriver` | 已由 `--disable-blink-features=AutomationControlled` 覆盖,这里是双保险 |
| `navigator.plugins` | 容器里实测已正常(5 个),仍纳入 |
| `chrome.runtime` | 我们只有 `window.chrome`,runtime 未补 |
| `iframe.contentWindow` | 未覆盖过 |

## 它不是隐身衣

这是**公开**脚本,本身有可被识别的特征。它把最容易抓的几项盖住,不代表
不可检测。真正的环境层差距(容器 Chromium vs 真实 Chrome、Xvfb vs 真实
桌面、SwiftShader vs 真实 GPU)不是一个 JS 能补的。

## 升级

从上游重新生成后替换本文件并更新上面的 sha256。不要手工编辑——文件头
第一行就写着 auto-generated。
