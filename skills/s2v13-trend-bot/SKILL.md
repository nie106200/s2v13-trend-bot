---
name: s2v13-trend-bot
description: 4 小时趋势跟踪交易机器人（OKX USDT 永续）。三均线多头排列入场 + 道氏突破（meme 专用）+ ATR/回调自适应动态止损 + 死叉减半，按波动率反比分配仓位（锚定最低波动品种）。含牛熊闸门、回撤触发长均线过滤、每周每品种亏损上限停止。参数在私有 config.json，仓库只公开框架与回测证据。用于：生成多品种交易信号、本地模拟盘、经 OKX Trade CLI 自动下单并按 AI Builder 归因。Use when the user asks to 跑趋势策略 / 生成交易信号 / 模拟盘 / 自动下单 / 4H 趋势跟踪 / S2v13。
---

# S2v13 Trend Bot · 4H 趋势跟踪

![logo](assets/logo.png)

## 这个 skill 做什么

在 OKX USDT 永续上执行一套**验证过的 4 小时趋势跟踪系统**：

| 能力 | 命令 |
|---|---|
| 生成 19 品种交易信号（只读） | `python scripts/signal_engine.py` |
| 本地模拟盘（真实行情驱动） | `python scripts/paper_engine.py --init 500` |
| 实盘下单（默认 dry-run） | `python scripts/order_engine.py --plan plan.json` |

## 前置条件

1. **OKX Trade CLI** 已安装并配置 profile：`okx config init`
2. **AI Builder Code**（下单归因，可在 https://www.okx.com/agent-tradekit/builder 申请）
   ```powershell
   [Environment]::SetEnvironmentVariable('OKX_AI_BUILDER_CODE','<你的Code>','User')
   ```
3. Python 3.10+ 与 `pandas` / `numpy`

## 策略一句话

> 三条均线排好队、价格站上中线才买；跌破 ATR 倍数或回调深度就止损；
> 死叉时如果已经赚了 10% 就只减半；熊市不玩，周内亏两单就歇着。

## 工作流

```
① signal_engine.py  → 输出今日信号 + 权重表（只读，随时可跑）
② paper_engine.py   → 用真实行情推进本地模拟盘，验证规则/记账
③ order_engine.py   → 先 dry-run 打印命令，人工核对后加 --execute
```

## 关键参数

| 项 | 规则 | 是否公开 |
|---|---|---|
| 周期 | 4H（每根收盘计算） | ✅ |
| 入场（核心 / 高波动） | 三均线多头排列 + 站上中线 | ✅ 结构公开 |
| 入场（meme） | 道氏突破前高 + 站上中线 | ✅ |
| 止损 | max(ATR 倍数, 入场价 ×(1 − 回调深度倍数)) | ✅ 结构公开 |
| 出场（核心） | 死叉时：浮盈达阈值且仍在中长线上方 → 减半；否则全平 | ✅ |
| 出场（meme） | 日线趋势线破位 | ✅ |
| 最小持仓 | 防止刚开仓被噪音洗掉 | ✅ |
| 仓位 | 波动率反比，锚定最低波动品种 = 1.0 | ✅ |
| 闸门 | 熊市不新开 / 回撤后过滤 / 品种分级 | ✅ |
| 补丁 | 每周每品种亏损达上限后停开 | ✅ |
| **具体数值** | EMA 周期、ATR 倍数、回调系数、阈值、名义上限、杠杆 | ❌ **见私有 config.json** |

> 参数从 `config.example.json` 复制到 `config.json` 后自行填写。

## 参考文档

- [`references/strategy.md`](references/strategy.md) — 策略完整规格与逻辑推导
- [`references/backtest-results.md`](references/backtest-results.md) — 回测结果与证伪清单
- [`references/risk-controls.md`](references/risk-controls.md) — 风控设计与实测结论

## 重要提醒

- **回测不等于未来**：品种外样本检验显示，未参与调参的 13 个品种 Calmar 从 1.09 掉到 0.52；
- **预期回撤 40~55%**，且可能连续两年不赚钱；
- **不要手工挑品种**——本策略在 8 个精选品种上 6,027×，在 10 个公平选择的大市值上只有 141×；
- 本 skill **不构成投资建议**，实盘前请用小额验证并自行承担风险。