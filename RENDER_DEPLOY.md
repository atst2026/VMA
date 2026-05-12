# Deploy the dashboard to Render (free, public URL)

The result: Sara gets a real URL like `https://vma-dashboard.onrender.com`
she can bookmark on her laptop and phone. All buttons work. No local
install for her. £0/month.

You only do this once. Takes ~10 minutes.

---

## Step 1 — Create a Render account (2 min)

1. Go to **https://render.com**
2. Click **Get Started for Free**
3. Sign in with GitHub (uses your `atst2026` account — easiest)
4. No card needed

## Step 2 — Connect this repo as a Blueprint (3 min)

1. On your Render dashboard, click **New +** (top-right) → **Blueprint**
2. Render asks for a GitHub repo. Click **Connect a repository**
3. If prompted, install the Render GitHub app and grant it access to **`atst2026/VMA`** only
4. Pick the `VMA` repo
5. Render reads the `render.yaml` we just committed and pre-fills everything: service name `vma-dashboard`, free plan, Python 3.11, Frankfurt region
6. Click **Apply** at the bottom

Render now starts the first build. Takes ~3-5 minutes (installing deps).

## Step 3 — Set the two secrets (2 min)

While the first build is running, set the two secrets that aren't in the file:

1. In Render, click on the `vma-dashboard` service → **Environment** tab on the left
2. You'll see two grey-out rows: `GITHUB_TOKEN` and `DASHBOARD_PASSWORD`
3. Click **Edit** on `GITHUB_TOKEN` → paste the same `github_pat_...` you used for the local dashboard → **Save**
4. Click **Edit** on `DASHBOARD_PASSWORD` → pick any password (e.g. `vma2026sara`). Sara will type this once on first visit and her browser remembers it. → **Save**

When you save the second one, Render automatically redeploys with the new env. Watch the **Events** tab — it'll show "Deploy live" when it's done (~2 min).

## Step 4 — Open the URL

Top of the service page shows your URL: `https://vma-dashboard.onrender.com` (or similar — Render appends a random suffix if the name is taken).

1. Open it. Browser pops up a "Sign in" dialog.
2. Username: anything (e.g. `sara`). Password: the `DASHBOARD_PASSWORD` you set.
3. Browser remembers it for the session.

Click around. **Daily Refresh** pulls the latest morning brief from GitHub Actions. **Pitch Pack / Reverse Match / 14-Day Catch-up** dispatch the corresponding workflows.

## Step 5 — Send Sara the URL + password

Tell her:
> Bookmark this: `https://vma-dashboard.onrender.com`
> Sign-in: any username, password `vma2026sara` (or whatever you set)

---

## Things to know about the free tier

- **Spin-down**: if no one opens the dashboard for 15 minutes, Render puts it to sleep. First visit after sleep takes ~30 seconds to wake up. Subsequent loads are instant.
- **750 hours/month**: more than enough for a single dashboard.
- **Auto-deploy**: every commit to `main` triggers a fresh deploy. So when you push tweaks, the live dashboard updates automatically within ~2 minutes.

## Troubleshooting

**Build fails on first deploy** — usually a transient pip resolver issue. Click **Manual Deploy → Deploy latest commit** in Render's UI and it'll retry.

**Dashboard loads but banner says "GITHUB_TOKEN not set"** — you didn't save the secret correctly. Re-check the Environment tab.

**Buttons say "GitHub returned 401"** — the token expired or has the wrong scopes. Regenerate at https://github.com/settings/personal-access-tokens with Actions (read+write) and Contents (read) scopes on `atst2026/VMA`.

**Want to rotate the dashboard password** — Environment tab → edit `DASHBOARD_PASSWORD` → save. Tell Sara the new one.

**Want a nicer URL** — Render's free tier gives you `*.onrender.com`. To use a custom domain like `dashboard.vmagroup.com` you'd add it in Render's Custom Domains tab and create a CNAME record. Paid tier required for that.
