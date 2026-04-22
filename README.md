# 家庭管理系统 Family Manager

> 一个 AI 驱动的家庭事务记录 + 分析工具，帮四口之家把每天的冲突、温情、好习惯、坏习惯都沉淀下来，
> 由 AI 每天早上送一份温柔的小报告，给每位家长可执行的沟通/习惯建议。

> **这是一份开源代码，不提供公开体验站点**——原作者的部署连着真实家庭数据，
> 不对外开放。想试就按下面 [运行一份自己的](#运行一份自己的) fork 一份部署到自己账号。

![status](https://img.shields.io/badge/Status-Active-brightgreen) ![license](https://img.shields.io/badge/License-MIT-blue)

---

## 它是什么

在家的日常里，孩子的坏习惯、夫妻的小摩擦、全家的温情时刻很多都流失在记忆里。这个 app 让
家长用几秒钟记下来，然后让 AI：

- **总结**：每条事件用一句话提炼
- **分析**：冲突类事件自动给出 2-3 条原因 + 2-3 条建议 + 一句可以直接开口说的话
- **维护画像**：每天刷新一份家庭状态卡（关系氛围 / 近期主题 / 值得延续的习惯）
- **个性化推送**：妈妈看到给妈妈的建议，儿子打开看到给他自己的小日报

## 功能特性

- 📱 **纯前端 PWA**，手机添加到主屏幕即可
- 🔐 **4 位 PIN 登录**，每个家人一个账号
- 👁️ **视角系统**：孩子登录看到的都是关于自己的事（自动改写"女儿"→"姐姐"/"我"）
- ✏️ **只读/可编辑权限**：爸妈可编辑，孩子只读
- 🧠 **AI 事件分析**：事件保存后 20-30 秒自动出 `title/summary/cause[]/suggest[]/script`
- 📊 **每日 07:30 小报告**：北京时间定时触发
- 🎨 **成员档案**：年龄 / MBTI / 爱好 / 成绩 / 闪光点 / 成长空间，全部可编辑
- 💡 **建议中心**：从所有事件的 AI 建议聚合，可标记"已尝试"，可跳回源事件
- 🌓 深浅色 / 薄荷 / 玫瑰 配色切换 + 字号调节

## 技术栈

```
📱 React 18 + Babel Standalone       ← 单文件 index.html，无构建步骤
        ↓ HTTPS
🛫 FastAPI + SQLAlchemy + Postgres   ← Fly.io，sin 区域
        ↓ HTTPS
🤖 Anthropic-compatible LLM API      ← 可用官方 api.anthropic.com 或国内代理（tdyun.ai 等）
        ↑
🖥 Mac worker (LaunchAgent)          ← 轮询任务 + 调 LLM + 回传结果
```

- **前端**：`index.html` 一个文件，React 18 via CDN + Babel Standalone 浏览器端编译，零构建
- **后端**：Python 3.11 + FastAPI + SQLAlchemy + Postgres（Fly Unmanaged）
- **数据库**：`users` / `events` / `ai_jobs` / `daily_reports` / `family_context` 5 张表
- **AI 模型**：任意支持 Anthropic `/v1/messages` 协议的网关，默认 `claude-sonnet-4-6`

## AI 上下文工程

项目里最用心的部分 —— **不能把所有事件塞给 AI**，要控在 ~1k-2k tokens 内。
采用 4 层金字塔：

| 层级 | 内容 | 谁生成 | 每次 AI 调用用量 |
|---|---|---|---|
| **L0** 原始事件 | 用户输入的完整描述 | 用户 | 1 条（本次事件） |
| **L1** 事件摘要 | AI 提炼的一句话 | 分析事件时顺带 | 最多 5 条相关历史 |
| **L2** 每日报告 | 昨天的 good/watch/tip | 每天 07:30 | 仅 daily_report 任务用 |
| **L3** 家庭状态卡 | 滚动的 300 字家庭画像 | 每日报告完自动刷新 | 1 份 |

**相关事件召回策略**：按"成员交集 + 事件类型 + 情绪关键词（发脾气/吵架/打架/...）" 打分，
选前 5 条相关历史，避免粗暴取时间最近的。

详见 [`backend/ai.py`](./backend/ai.py)。

## 运行一份自己的

> 建议：**如果只是家里用**，直接 fork 本仓库，改下 `backend/seed.py` 里的成员信息和默认 PIN 即可。
> 下面是完整自部署流程。

### 1. 后端部署到 Fly.io

```bash
# 前提：安装 flyctl https://fly.io/docs/flyctl/install/
cd family-manager

# 创建 app + Postgres（把 YOUR-APP-NAME / YOUR-DB-NAME 换成你自己的）
fly apps create YOUR-APP-NAME
fly postgres create --name YOUR-DB-NAME --region sin \
  --vm-size shared-cpu-1x --volume-size 1 --initial-cluster-size 1
fly postgres attach YOUR-DB-NAME --app YOUR-APP-NAME

# 生成密钥
fly secrets set \
  SESSION_SECRET=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))') \
  WORKER_TOKEN=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))') \
  --app YOUR-APP-NAME

# 改 fly.toml 里的 app 名
sed -i '' 's/gw-family-manager/YOUR-APP-NAME/' fly.toml

# 部署
fly deploy --app YOUR-APP-NAME
```

### 2. 改种子数据

编辑 `backend/seed.py`：
- 把 4 个家人的名字、昵称、MBTI、职业、学校、成绩、爱好换成你家的信息
- **改 `DEFAULT_PINS`**，别留 1111/2222/3333/4444 公开示例

### 3. Mac worker 部署

worker 在 Mac 上常驻，负责把 AI 任务转发给 LLM API。需要 Mac 开机才能分析事件。

```bash
cd worker
FAMILY_WORKER_TOKEN=<step 1 生成的 WORKER_TOKEN> \
LLM_API_KEY=<你的 Anthropic 兼容 key> \
LLM_BASE_URL=https://api.anthropic.com \
LLM_MODEL=claude-sonnet-4-6 \
FAMILY_API=https://YOUR-APP-NAME.fly.dev \
./install.sh
```

Mac 自动注册成 LaunchAgent 开机自启。日志：`tail -f ~/Library/Logs/family-manager/worker.log`

### 4. 前端

改 `index.html` 里的 `API_BASE` 指向你的 Fly 域名，推送到任何静态托管（GitHub Pages / Cloudflare Pages / Netlify / 自己服务器）。

## 已知限制 & 没做的事

- ❌ 注册流程 —— 只有硬编码的 4 个家庭成员（在 `seed.py` 改）
- ❌ 图片 / 附件 —— 事件只支持文字描述
- ❌ 语音输入 —— UI 上的 `🎙` 按钮是占位
- ❌ 微信 / 企微推送 —— 目前只有 app 内通知弹窗
- ❌ 纪念册 / 趋势图表 —— 只有单事件和日报，没有周视图
- ⚠️ Mac 合盖休眠时 worker 停工 —— LaunchAgent 依赖 Mac 持续开机
- ⚠️ 事件详情 ❤️ 收藏、孩子送心心、评论发送 等按钮还是占位

## 致谢

- UI 原型用 [Claude Design](https://claude.ai/design) 画的（温暖治愈米色奶油风）
- 功能由 [Claude Code](https://claude.com/claude-code) 陪 AI 对敲出来
- 部署在 [Fly.io](https://fly.io)
- AI 模型：[Anthropic Claude](https://anthropic.com) 的 Sonnet 4.6

## License

[MIT](./LICENSE) · 随便用，拿去给你自己家改造也行。

---

**如果你把它 fork 给自己家用**，欢迎回来告诉我你做了什么改造 👋
