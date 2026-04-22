# Sara — how to use the tool on your laptop

This covers `/deep-dive` and `/analyse`. The **morning brief** is fully automatic and lands in your inbox at 08:55 Mon–Fri without you doing anything — you never need to touch this repo for that.

## One-time setup (15 min)

### 1. Install Claude Code (if you haven't)

You already have Claude Max. Claude Code is the terminal CLI that comes with it.

```bash
npm install -g @anthropic-ai/claude-code
claude login     # follow the browser prompt, authenticates with your Max account
```

If you don't have `npm`, install it via Node.js from **https://nodejs.org/** first (LTS version).

### 2. Clone the repo

```bash
cd ~                                           # or wherever you keep projects
git clone https://github.com/atst2026/VMA.git
cd VMA
```

If `git clone` asks for credentials: GitHub → Settings → Developer settings → Personal access tokens → Generate new token (classic) → tick `repo` scope → copy the token and paste it as the password when git asks.

### 3. Install Python dependencies (once)

```bash
pip3 install requests beautifulsoup4 lxml python-dateutil
```

If `pip3` isn't found, install Python 3.11+ from **https://www.python.org/** first.

### 4. Add the API keys

```bash
cp .env.example .env
```

Then open `.env` in any text editor and fill in the two empty lines:

```
COMPANIES_HOUSE_KEY=4d0d8aa9-e7d9-4953-af46-6da2efc019c9   # already filled
BRIGHT_DATA_KEY=a5642508-e8b1-4a39-8a19-a3e11bc524f4       # already filled
```

Those two are already populated in `.env.example` so a straight copy works.

### 5. Verify

```bash
claude
```

Inside Claude Code, type `/` — you should see three commands suggested:
- `/morning-brief` (you won't use this — it runs itself overnight, but it's here for manual testing)
- `/deep-dive`
- `/analyse`

If those appear, you're set up.

---

## Using `/deep-dive`

When the morning brief flags a company or person you want to know more about.

```bash
cd ~/VMA
claude
```

Then in Claude Code:

```
/deep-dive Unilever
/deep-dive Jane Smith
/deep-dive HSBC Corporate Affairs team
```

What happens:
1. Claude pulls Companies House filings, officer changes, SEC 8-Ks if US-listed, regulator hits, trade-press hits, GDELT global news
2. Claude does ad-hoc WebSearch for anything the structured sources missed (e.g. current Head of Corporate Affairs — often only findable on their website)
3. Claude synthesises one page: Snapshot · Why they're on your radar · Recent changes last 12 months · Current comms team · Signal stack · Recommended call angle · Open questions

Takes ~60 seconds. Output prints in the terminal and saves to `tool/state/deep_dive_<target>_<timestamp>.md` for later.

---

## Using `/analyse`

When you've run a Recruiter search and want Claude to cross-reference the results against today's brief.

1. In LinkedIn Recruiter, run whatever search you care about.
2. Export the result list (CSV) or just copy-paste the visible rows.
3. In Claude Code:

```
/analyse
Name, Title, Company
Jane Smith, Head of Communications, Unilever
Bob Jones, Corporate Affairs Director, Diageo
…
```

Claude will:
1. Parse the rows
2. Cross-reference each person's company against today's morning brief signals
3. Return three sections: **Call these 5 first** · **Also relevant** · **Nothing to pursue** (with reasons)

Anyone at a flagged company gets surfaced first. Agency/sales titles drop out automatically.

---

## If something breaks

- `/morning-brief` stops arriving by email → check the Actions tab on GitHub for the run log; usually it's a secret that got deleted/expired
- `/deep-dive` returns mostly empty → the company name might need disambiguating (e.g. try `Unilever plc` or `Unilever UK`)
- Slash commands don't show in Claude Code → check you're running `claude` from inside the `VMA` directory, not from your home directory

Anything else: message Amir, he set this up and has the whole repo.
