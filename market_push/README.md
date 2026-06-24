# A股云端定时推送

这个目录用于把 A 股相关任务放到云端执行，并通过 PushPlus 推送到手机微信。

优先推荐两种跑法：

- GitHub Actions 定时执行：免服务器，适合你当前场景。
- Linux 服务器 + cron：更稳，但需要自己维护机器。

## 任务

- 08:00：隔夜全球情报，包括全球产业利好、技术突破、美股指数涨跌和热门板块表现。
- 15:45：筛选“风华高科式分歧回踩试错”个股。
- 16:05：整理当日重点首板涨停股的涨停主因，并深挖次日可能继续发酵的预期差。
- 22:30：整理隔日可能被 A 股资金炒作的国内外行业热点。

## 数据来源和排序

- 新闻来源：Google News RSS 聚合，按主题关键词抓取财联社、证券时报、中国证券报、上海证券报、第一财经、华尔街见闻、Reuters、Bloomberg、CNBC、Nikkei、Investing.com、Moomoo、新浪财经等被聚合到的公开新闻。
- 美股行情：Yahoo Finance 的美股指数和行业 ETF 日线数据。
- A股行情：腾讯行情和东方财富历史行情接口。
- 排序逻辑：新鲜度、权威消息源、产业催化关键词、A股当前主流题材偏好共同加权。
- 输出里的“A股映射龙头”是题材代表股列表，用于观察情绪和板块映射，不构成买入建议。
- 首板涨停预期差日报已接入本地“公司业务画像库”和“供应链/客户映射”字段，后续可持续扩充高频个股资料。

## 方案一：GitHub Actions 定时执行

仓库里已经提供工作流文件：

`/.github/workflows/market-push.yml`

对应北京时间：

- `08:00` 早报
- `15:45` 形态筛选
- `16:05` 首板涨停预期差
- `22:30` 晚报热点雷达

注意：GitHub Actions 的 `cron` 用的是 UTC，上面的工作流已经换算好了，不需要你再改时区。

### GitHub 仓库配置

1. 把当前目录推到一个 GitHub 仓库。
2. 打开仓库 `Settings > Secrets and variables > Actions`。
3. 新增以下 `Secrets`：

```text
PUSHPLUS_TOKEN=你的pushplus_token
PUSHPLUS_CHANNEL=wechat
PUSHPLUS_TOPIC=可选
OPENAI_API_KEY=可选
```

4. 可选新增以下 `Repository variables`：

```text
OPENAI_MODEL=gpt-5.4
STOCK_TOP_N=10
STOCK_MIN_AMOUNT_YUAN=120000000
FIRST_LIMIT_TOP_N=0
FIRST_LIMIT_MIN_AMOUNT_YUAN=150000000
NEWS_TOP_N=10
NEWS_MAX_PER_THEME=2
MORNING_TOP_N=10
```

### 手动测试

推到 GitHub 后，可以在仓库 `Actions > market-push > Run workflow` 手动选择任务：

- `morning_brief`
- `stock_shape`
- `first_limit_up_radar`
- `news_radar`

说明：`FIRST_LIMIT_TOP_N=0` 表示推送全部首板涨停样本；如果你只想看前 N 只，再把它改成对应数字。

如果手动运行成功，后续定时任务就会按计划自动发到手机。

## 方案二：Linux 服务器 + cron

如果你后面觉得 GitHub Actions 不够稳，再切到服务器方案。

## 本地/服务器准备

1. 一台能长期联网运行的 Linux 服务器。
2. Python 3.10 或更高版本。
3. PushPlus token。
4. 可选：OpenAI API Key。没有它时，脚本会用规则打分生成简版；有它时会生成更接近人工投研口径的版本。

## 配置

复制环境变量模板：

```bash
cp .env.example .env
```

编辑 `.env`：

```bash
PUSHPLUS_TOKEN=你的pushplus_token
PUSHPLUS_CHANNEL=wechat
OPENAI_API_KEY=可选
OPENAI_MODEL=gpt-5.4
```

## 手动测试

只生成内容，不推送：

```bash
python run_morning_brief.py --dry-run
python run_stock_shape.py --dry-run
python run_first_limit_up_radar.py --dry-run
python run_news_radar.py --dry-run
```

真实推送：

```bash
python run_morning_brief.py
python run_stock_shape.py
python run_first_limit_up_radar.py
python run_news_radar.py
```

说明：现在 `run_*.py` 会自动加载当前目录下的 `.env`，不需要你先手动 `export`。

## 安装 cron 定时任务

服务器时区建议设为中国时间：

```bash
sudo timedatectl set-timezone Asia/Shanghai
```

安装 cron：

```bash
bash install_cron.sh /path/to/market_push
```

安装后会写入当前用户 crontab：

```cron
0 8 * * * cd /path/to/market_push && ... python run_morning_brief.py
45 15 * * 1-5 cd /path/to/market_push && ... python run_stock_shape.py
5 16 * * 1-5 cd /path/to/market_push && ... python run_first_limit_up_radar.py
30 22 * * * cd /path/to/market_push && ... python run_news_radar.py
```

## 说明

- 早8点和晚10点半任务每天运行，因为周末也可能有影响下一交易日的产业消息。
- 下午任务默认只在周一到周五运行；脚本内部也会检查当天是否有 A 股收盘数据。
- 结果用于投资研究和观察，不构成买入建议。
