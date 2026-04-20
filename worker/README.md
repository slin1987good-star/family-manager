# Family Manager AI Worker

Runs on a Mac. Polls https://gw-family-manager.fly.dev for pending AI analysis
jobs and dispatches each prompt to the local `claude` CLI (Claude Code
subscription), then posts the JSON reply back.

## Install

Step 1 — get a long-lived Claude Code token (one time):

```bash
claude setup-token
```

This opens a browser, you approve, and it prints a token starting with
`sk-ant-oat01-`. Copy it.

Step 2 — install the LaunchAgent:

```bash
export CLAUDE_CODE_OAUTH_TOKEN=<paste the token from step 1>
export FAMILY_WORKER_TOKEN=<the WORKER_TOKEN you set on Fly>
./install.sh
```

The short-lived OAuth tokens Claude Desktop writes to the macOS keychain
expire every couple of days, which is why `setup-token` exists — it gives
a token designed for headless/CI use.

This writes a LaunchAgent to `~/Library/LaunchAgents/com.gw.family-manager.worker.plist`
so it starts at login and restarts on crash.

## Check it's running

```bash
launchctl print gui/$(id -u)/com.gw.family-manager.worker | head -20
tail -f ~/Library/Logs/family-manager/worker.log
```

## Stop / uninstall

```bash
launchctl bootout gui/$(id -u)/com.gw.family-manager.worker
rm ~/Library/LaunchAgents/com.gw.family-manager.worker.plist
```

## Notes

- The Mac must be running (lid open or plugged in) for AI analysis to
  complete. If it's asleep, jobs queue on the server and process once the
  Mac wakes up.
- Uses the `claude -p` (print mode) subcommand. Each call counts against
  your Claude Code subscription's 5-hour window.
