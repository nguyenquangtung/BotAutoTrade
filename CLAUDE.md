# CLAUDE.md - Guidelines for Claude Code in AutoScalping

## 1. Project Overview & Environment
- **Project**: AutoScalping - Automated MetaTrader 5 Expert Advisor (`nyao_scalper v54.0`) & Ecosystem.
- **Pairs Traded**: `XAUUSDc` (M5, Magic 6926268) & `USDJPYc` (M5, Magic 6926270).
- **Broker**: Exness Real Account (`#<ACCOUNT_LOGIN>`, `Exness-MT5Real48`, Base Currency: USC - US Cent).
- **Live VPS**: Google Cloud `<VPS_IP>` (Singapore), container `mt5_trading` running WineHQ Staging.
- **Web Dashboard**: `http://<VPS_IP>:3000/dashboard/` (Basic Auth: `admin` / `<REDACTED>`).

## 2. Strict Security Rules
> [!CAUTION]
> 1. **Never perform destructive or modifying SSH/remote commands** on the VPS/live MT5 terminal without explicit approval from the user.
> 2. **Always ask confirmation** before deploying, restarting services, or editing configurations on the remote server.

## 3. Multi-Agent 5-Step Workflow
Every time code is changed or a commit is made:
1. **Update `AGENT_STATE.md`**: Update current snapshot, completed tasks, next action items, and changelog.
2. **Commit & Push**: Run `scripts/git-push.ps1 -Message "type(scope): description"` or follow Conventional Commits.
3. **Update Knowledge Graph**: Run `graphify . --update --code-only`.
4. **Update `ARCHITECTURE.md`**: Document any changes to data flow, parameters, or architecture.
5. **Sync Dependencies**: Keep `requirements.txt` up to date.

## 4. Key Commands
- **Git Push & Sync**: `powershell -File scripts/git-push.ps1 -Message "feat(...): ..."`
- **Knowledge Graph**: `graphify . --update --code-only`
- **Dashboard Service Syntax Check**: `python -m py_compile dashboard/service.py`
- **Database Module Syntax Check**: `python -m py_compile dashboard/analytics_db.py`
- **Daily Report Test**: `python reporting/reporter.py --type daily --no-mail --save-html reporting/preview_daily.html`
