# Sara's Desk — dashboard setup

A local web app running on your Mac/PC. One URL (`http://localhost:8765`),
bookmarked once. Shows today's leads and predictors at the top, three
action boxes underneath (Pitch Pack, Reverse Match, 14-Day Catch-up).
Each action button triggers the corresponding GitHub Actions workflow
and emails the result to Sara.

## One-time setup (~10 minutes)

### 1. Clone the repo (if you haven't)
```bash
cd ~
git clone https://github.com/atst2026/VMA.git
cd VMA
```

### 2. Install Python deps
```bash
pip3 install --user flask requests beautifulsoup4 lxml python-dateutil
```
(The launcher script also auto-installs these if missing.)

### 3. Generate a GitHub Personal Access Token

1. Go to https://github.com/settings/tokens?type=beta
2. Click **Generate new token**
3. **Token name**: `Sara's Desk dashboard`
4. **Expiration**: pick whatever (90 days, no expiry, etc.)
5. **Repository access**: Only selected repositories → `atst2026/VMA`
6. **Permissions** → Repository permissions:
   - **Actions**: Read and write (lets the dashboard trigger workflows)
   - **Contents**: Read (lets the dashboard download brief artefacts)
   - **Metadata**: Read (default)
7. Click **Generate token** → **copy the `github_pat_…` string**

### 4. Add the token to `.env`

Open `.env` in any text editor. Add these lines (the file should already
contain the other API keys):

```
GITHUB_TOKEN=github_pat_xxxxxxxxxxxxxxxxxxxx
GITHUB_OWNER=atst2026
GITHUB_REPO=VMA
```

Save.

### 5. Launch

**macOS**: double-click `start_dashboard.command`
**Windows**: double-click `start_dashboard.bat`

Your browser opens `http://localhost:8765` automatically. Bookmark it.

The dashboard runs as long as the terminal window stays open. To stop:
close the terminal window.

## Using the dashboard

- **Today's Leads** + **Today's Predictors**: shows the latest morning
  brief data. Top 5 of each visible by default; click "Show all" to
  expand. Click any link to open the source in a new tab.
- **Refresh from GitHub** (top-right): downloads the latest morning
  brief artifact from GitHub Actions. Click after the daily run has
  completed each morning to pull the freshest data into the dashboard.
- **Pitch Pack box**: fill in account name + role → click "Run and send
  via email" → email lands at `stehrani@vmagroup.com` within 1–2 minutes
- **Reverse Match box**: candidate name + current company + current
  title → click "Run and send via email" → same delivery
- **14-Day Catch-up box**: set window (default 14, max 60) → click "Run
  and send via email" → fortnightly catch-up brief lands in 1–2 minutes

## What's happening under the hood

The dashboard does two things:

1. **Reads** the latest morning brief output from `tool/state/`. The
   "Refresh from GitHub" button downloads the most recent
   `morning-brief` artifact from GitHub Actions and unpacks it into
   that folder.

2. **Triggers** workflows by POSTing to GitHub's REST API:
   - `POST /repos/atst2026/VMA/actions/workflows/pitch-pack.yml/dispatches`
   - `POST /repos/atst2026/VMA/actions/workflows/reverse-match.yml/dispatches`
   - `POST /repos/atst2026/VMA/actions/workflows/fortnightly-sweep.yml/dispatches`

   The workflow runs on GitHub's runners, generates the brief, sends
   the email via Gmail SMTP — same path as the daily morning brief.

## Troubleshooting

**"GITHUB_TOKEN not set" banner on the dashboard**
You haven't added the token to `.env`. Re-do step 3+4 above.

**"No leads loaded yet"**
The local state files are empty. Click "Refresh from GitHub" — if there's
a recent morning-brief artifact, it'll download. If not, wait for the
morning brief to fire.

**Buttons disabled / "Network error" on dispatch**
Check the token has `workflow` (Actions write) scope. Regenerate if
needed.

**Port already in use**
Set `DASHBOARD_PORT=8766` (or any free port) in `.env`. Restart the
launcher.

**Want it to start automatically on boot?**

macOS — create a launchd plist at `~/Library/LaunchAgents/com.sara.desk.plist`:
```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.sara.desk</string>
  <key>ProgramArguments</key>
  <array>
    <string>/path/to/VMA/start_dashboard.command</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
</dict>
</plist>
```
Then `launchctl load ~/Library/LaunchAgents/com.sara.desk.plist`.

Windows — add a shortcut to `start_dashboard.bat` in:
`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\`
