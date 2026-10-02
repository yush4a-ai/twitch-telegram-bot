# Apple Liquid Glass — 面向 AI 编码智能体的设计技能

[English](./README.md) · [中文](./README.zh-CN.md)

> **苹果冷调灰白底 + 统一白色表面 + 发丝分隔线；玻璃只用在图层真正重叠处。克制即奢侈。**

一个可移植的、markdown 形式的 **skill**，教 AI 编码智能体（Claude Code，以及任何能读取 skill 文件的 agent）产出**苹果级网页 UI**——apple.com / Apple Newsroom / 最新 macOS 那种沉静、高级的「液态玻璃」质感。它不是一个供你 import 的组件库，而是一套智能体用来**做决策的设计系统 + 纪律**——让产出「像苹果」靠的是*一堆小的正确决策之和*，而不是某个花招。

<p align="center">
  <img src="examples/screenshots/01-hero-tiers.png" width="880" alt="账户页 — hero、套餐档位、存储条">
</p>

---

## 它解决什么问题

让任何模型做一个「干净的、苹果风的页面」，你通常会拿到 **AI 默认产物**：每个卡片各自描边 + 底色 + 彩色左边条的碎片化布局、到处糊 `backdrop-filter` 模糊、彩虹渐变、暖米色底、Inter 当品牌字体。它读起来是「又一个 AI 页面」，不是苹果。

这个 skill 把「为什么那样是错的」和「苹果实际怎么做」编码成可执行规则：

- **统一表面 > 碎片卡片。** 多个同类项放在**一张**白色面板上、用发丝线分隔——碎片化是头号敌人。
- **玻璃是调味，不是主菜。** 只在图层真正重叠处磨砂（吸顶导航、弹层、彩色 CTA）。普通内容 = 实心白 + 柔和阴影。
- **层级靠字重 + 字号 + 灰阶，不靠颜色。** 正文是黑白灰；颜色**只做 accent**（一个蓝）。
- **克制即奢侈。** 一千个「不」换一个「是」。先用留白和层级解决，再谈加边框/填充/图标。
- **质感在细节。** 大标题负字距、`tabular-nums`、发丝线、轻悬停抬升、180% 饱和玻璃、滚动边缘导航。

---

## 安装

克隆到你的智能体 skills 目录。Claude Code：

```bash
git clone https://github.com/naplesblue/apple-design-skill.git \
  ~/.claude/skills/apple-liquid-glass
```

就这样。智能体会从 `SKILL.md` 的 frontmatter 自动发现该 skill，任务匹配时加载（触发词见下）。其他 agent 直接指向 `SKILL.md` 即可——全部是纯 markdown + CSS，无构建步骤、无依赖。

**触发词：** 「Apple style」「liquid glass」「做成 apple.com 那样」「干净 / 高级 / 极简 UI」「苹果风」「液态玻璃」——或任何要做「苹果级」网页/组件的请求。

---

## 工作方式

两种模式：

- **Build（构建）** — 做新 UI。智能体走固定流程：读 `design-system.md` → 放入 `tokens.css` → 从 `patterns.md` 选页面配方 → 从 `components.md` 组合 →（若有交互层）套 `motion.md` → 对照 `reference.html` → 跑 `checklist.md` 关卡。
- **Review（评审）** — 审已有 CSS/页面「够不够苹果 / 统一一下」。`review.md` 先定位真正的视觉图层，按系统打分，返回带 `file:line` 定位、映射回 token 的优先级修复清单。

整套**与框架无关**：文件假设纯 HTML/CSS。在 React/Vue/Svelte 里，把 CSS 翻成你的样式方案，但**保留精确的 token 值**、面板而非卡片的模式、以及玻璃只用于重叠处的规则。

---

## 文件清单

| 文件 | 作用 |
|---|---|
| `SKILL.md` | 入口——哲学、工作流、反 AI slop 清单、自检。智能体最先读它。 |
| `design-system.md` | 完整规格：色彩、字号阶、间距/圆角/阴影档位、玻璃配方、材质纵深语法、响应式。 |
| `tokens.css` | `:root` 自定义属性——放进任何项目，用 `var(--…)` 引用。 |
| `components.md` | 可直接粘贴的 HTML+CSS：玻璃导航、统一面板列表、卡片网格、分段控件、标签、按钮、彩色 CTA、开关、在线点。 |
| `patterns.md` | 页面级配方（阅读 720 / 网格 1080 容器 · detail/index/home/form 原型）+ 决策树。 |
| `motion.md` | 召唤/消失类交互层的流体动效：弹簧、可打断、同路径进出、materialize、reduced-motion。 |
| `app.md` | **App / iOS 外壳层**——设备框（iPhone 精确规格）、状态栏、大标题折叠导航、tab bar、底边升起 sheet、安全区、移动优先规则。做手机屏时读它。 |
| `icons.md` | **线性图标层**——精选的 [Lucide](https://lucide.dev) 内联 SVG 集（统一 24 格一套 stroke）、默认 `currentColor` 灰阶、只在操作/激活态染 accent、尺寸表，以及「图标要 earn its place」的克制规则（不用灰方块、不用 emoji）。 |
| `checklist.md` | 独立的「完成」关卡。 |
| `review.md` | 评审模式的审计流程。 |
| `reference.html` | 渲染好的活体样式指南——浏览器打开，它就是视觉基准。 |

---

## 示例

`examples/` 里放了一个用该 skill 端到端构建的完整真实页面——一个 **Account & Plan（账户与订阅）** 页，**故意埋满了反 slop 清单里的每一个陷阱**，好让你看到每个「苹果正解」。浏览器打开 `examples/account.html`（可交互：滚动看玻璃导航边缘、点开关、打开取消弹层）。

**套餐档位 + 存储条** — 碎片化与色彩装饰的陷阱。三档保持全白/灰阶，只在当前方案上用**一个** accent；存储条是灰阶轨 + 单 accent 填充、`tabular-nums`、实心白（不加玻璃）。

<img src="examples/screenshots/01-hero-tiers.png" width="820" alt="套餐档位与存储条">

**统一面板** — 账户信息和通知开关各自放在**一张**白面板 + 发丝分隔线上，而不是一堆卡片。玻璃导航只在内容滚到其下方时才显示滚动边缘发丝线。

<img src="examples/screenshots/02-unified-panel.png" width="820" alt="统一的账户与通知面板">

**交互层** — 「取消订阅」弹层在压暗的遮罩上 **materialize**（模糊 + 缩放 + 透明度一起动），并践行*宽容*原则：醒目的操作是温和的「改用 Free」，而破坏性的「仍要取消」是一个安静的文字操作——绝不是吓人的红色确认框。

<img src="examples/screenshots/03-modal-sheet.png" width="820" alt="取消弹层 — materialize 玻璃与宽容原则">

**响应式** — 一套 `clamp()` 设计；移动端档位塌成单列，触控目标 ≥ 44px。

<img src="examples/screenshots/04-mobile.png" width="300" alt="移动端 — 单列">

### App / iOS 界面

做手机屏时，`app.md` 补上 web 系统没有的移动外壳：**精确规格的 iPhone 设备框**（bezel、灵动岛、状态栏、home 指示条——绝不手搓）、**滚动折叠的大标题导航**、**玻璃 tab bar**（来自 `icons.md` 的真线性图标——灰阶，只在激活 tab 上一个 accent）、**从底边升起的 sheet**。*内容*仍来自核心系统——所以一个钱包屏就是：彩色玻璃余额卡（唯一的上色时刻）+ 带 `tabular-nums` 的**统一交易列表**（金额保持灰阶——不踩「收入染绿」的按状态上色陷阱）+ tab bar。见 `examples/wallet.html`。

<p>
  <img src="examples/screenshots/05-app-wallet.png" width="300" alt="iOS 钱包屏 — 设备框、余额卡、统一交易列表">
  <img src="examples/screenshots/06-app-sheet.png" width="300" alt="从底边升起的 sheet">
</p>

---

## 自检关卡

宣称「完成」前，产出要过一份清单（节选）：

- 底色 `#f5f5f7`（冷），内容在 `#fff` 上，容器居中。
- 同类项 → **一张面板 + 发丝线**，不是碎片卡片。
- 玻璃**只**在导航 / 弹层 / CTA；普通块是实心白 + 柔和阴影。
- 大标题负字距；数字 `tabular-nums`；圆角 + 阴影取自命名档位。
- accent 是一个蓝（heat-橙 / 品牌色仅在有意义时）；其余灰阶。
- 视觉上与 `reference.html` 同一家族。

---

## 致谢与许可

- **动效层**（`motion.md`）改编自 [emilkowalski/skills](https://github.com/emilkowalski/skills) 的 `apple-design`（MIT © Emil Kowalski），其本身蒸馏自 Apple WWDC 关于流体界面与排印的演讲。
- **图标路径**（`icons.md`）取自 [Lucide](https://lucide.dev)（ISC 许可 © Lucide Contributors）。
- 其余 © 2026 naplesblue。
- **[MIT 许可证](./LICENSE)**——自由使用、修改、分发；保留声明即可。

与 Apple Inc. 无隶属或背书关系。「Apple」「macOS」及相关商标归 Apple Inc. 所有。本 skill 教的是一套*视觉语言*，不含任何 Apple 素材。
