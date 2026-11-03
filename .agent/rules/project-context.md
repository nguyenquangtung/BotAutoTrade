# Project Context & Operating Rules for AI Agents

## Core Principles
1. **Single Source of Truth**: All AI agents (Antigravity, Claude, Codex, Cursor) must read `AGENT_STATE.md` before starting work and update it before finishing.
2. **Production Safety**: The live trading account (#<ACCOUNT_LOGIN>) is currently operating on Google Cloud VPS `<VPS_IP>`. Never modify remote configurations, deploy code, or restart container processes without explicit user confirmation. Real IP/login/passwords are in the gitignored `.env.vps` file — never write them into tracked files.
3. **Commit Workflow**:
   - Update `AGENT_STATE.md`
   - Run `scripts/git-push.ps1`
   - Update `graphify . --update --code-only`
   - Update `ARCHITECTURE.md` if flows changed
   - Verify dependencies in `requirements.txt`

