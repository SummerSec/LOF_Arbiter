# CLAUDE.md — LOF_Arbiter

LOF 套利日报项目。通过 GitHub Actions 定时 ETL(akshare + 集思录)抓取数据,
生成日报(HTML / GitHub Issue),并部署到 GitHub Pages。

## CI / 定时调度(daily-etl.yml)

### GitHub Actions schedule 没有 SLA —— 经验教训

- **schedule 触发不保证准点,会被延迟数十分钟到数小时,高负载时甚至静默丢弃整次运行。**
  实测本仓库:配置 BJT 10:00–16:00,实际触发出现在 17:27、19:30。今天 10:00 那次直接没生成 run。
- **延迟现象**:实测 #10/#11 在 BJT 17:27、19:30 才执行,且 17:27 这种非整点/半点的时间戳,
  只可能是 GitHub 把某个 15:xx 的触发延迟了约 2 小时——cron 本身只会在配置的分钟数上触发。
- **要准点只能上外部触发器**(cron-job.org、自建定时器调 `workflow_dispatch` API)。
  GitHub 内置 schedule 给不了准点保证。
- 错峰(`:07`/`:37`)能否减少延迟证据不足,已回退;现配置仍用整点/半点(`:00`/`:30`)。

### cron 的 `timezone` 字段是官方支持的(2025+)

- `on.schedule` 的 cron 条目**支持同级 `timezone:` 字段**(IANA 字符串,如 `Asia/Shanghai`),
  官方文档明确:默认 UTC,可指定时区做 timezone-aware 调度。
  → 本仓库 cron 直接写北京时间 + `timezone: "Asia/Shanghai"`,**不要**再手动换算成 UTC。
- 验证平台行为时,**先拉真实运行记录**(`curl .../actions/workflows/<file>/runs`),
  不要凭旧知识推断 —— 这条字段是较新特性。

### Jisilu 全量同步门控

- `Decide Jisilu sync` 步骤靠 `github.event.schedule` **精确等于某条 cron 字符串**来判断"当日首次"。
- 因此"当日首次"必须**单独成一条 cron**(现为 `0 10 * * 1-5`),否则该判断永远匹配不上(死分支)。
- 改 schedule 时,**务必同步更新这一步里的 cron 字符串**,否则集思录全量同步不会触发。

## 提交规范

- Conventional Commits;CI 改动用 `ci:` 前缀,数据 ETL 用 `data:` 前缀。
- bot 会频繁向 `main` 推数据提交;本地推送前先 `git pull --rebase origin main` 再 push,保持线性历史。
