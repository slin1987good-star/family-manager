# Family Manager AI Worker

Runs on a Mac. Polls https://gw-family-manager.fly.dev for pending AI analysis
jobs and dispatches each prompt to the local `claude` CLI (Claude Code
subscription), then posts the JSON reply back.

## Install

```bash
export FAMILY_WORKER_TOKEN=<the WORKER_TOKEN you set on Fly>
./install.sh
```

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
