# Running the pipeline unattended on EC2

Two runs a day, 00:00 and 12:00 IST. Each one pulls the client's export,
renders every new submission end to end, and stops. Nobody types anything.

```
timer fires ─> castrol cycle ─> intake ─> schedule ─> work/poll until quiet ─> exit
```

`castrol cycle` is the whole run. It is the only command the server executes,
and everything below is either how to install it or how to read what it did.

---

## What a cycle actually does

1. **Takes a database advisory lock.** If the midnight run is still rendering
   when noon fires, the noon run logs `cycle.skipped` and exits 0. It does not
   queue, and it does not run alongside — two cycles would double the concurrent
   load on a paid vendor for no gain. The next window is a superset anyway.

2. **Pulls the export**, over a window computed on the *client's* calendar:
   `[today − 1, today + 1]` in `Asia/Kolkata`. Both ends are loose on purpose.
   `from` reaches back because a submission can arrive after its window was
   pulled; `to` reaches forward because we have not confirmed which timezone the
   API filters on, and a future date is accepted and simply returns nothing.
   Re-pulling is free: intake dedupes on the client's own row `id`.

   A failed pull does **not** abort the run — the jobs already in the database
   still need finishing. It sets exit code 1 so the failure is visible.

3. **Schedules every unfinished job.** Intake creates jobs but does not schedule
   them, and a previous cycle may have stopped at its deadline. This step is why
   a cycle needs no memory of what the last one did.

4. **Works, then waits, then works again** until nothing is queued and nothing
   is in flight at a vendor. This is the part `drain` gets wrong for unattended
   use: `drain` stops as soon as a sweep moves nothing, which for an avatar
   render means "still rendering" — it would submit every paid job and exit
   before collecting a single result.

5. **Stops at a deadline** (default 4 hours, well inside the 12-hour gap) and
   exits 1 if work is still outstanding. Nothing is lost when this happens —
   readiness is recomputed from `stage_runs`, so the next cycle picks up exactly
   where this one stopped — but a batch that outlasts its own window is worth
   knowing about.

**Delivery is still off.** `DELIVERY_ENABLED=false` means step 8 logs what it
would have POSTed and records the delivery row without posting. Everything up to
and including publish runs for real, so turning delivery on later requires no
re-render: flip the variable and the deliver stage runs on the next cycle.

---

## One-time server setup

Ubuntu 24.04 or Amazon Linux 2023, `t3.large` or better — the composite step is
ffmpeg and is CPU-bound. Disk: the working set is small (assets go to S3), but
give it 30 GB.

### 1. System packages

Two of these are load-bearing and fail *silently* rather than loudly:

```bash
sudo apt-get update && sudo apt-get install -y ffmpeg fonts-dejavu-core git curl
```

`ffmpeg` carries `ffprobe`, which sits in the charge path for the video stage —
that stage bills per output second and a missing probe produces a wrong bill,
not a crash (invariant 12). `fonts-dejavu-core` is the only font in
`render_card`'s list that exists on Linux; without it Pillow falls back to a
bitmap default and every card ships wrong.

Verify both before trusting a run:

```bash
ffprobe -version | head -1 && fc-list | grep -i dejavusans-bold
```

### 2. User and checkout

```bash
sudo useradd --system --create-home --home-dir /opt/castrol-video-pipeline castrol
```

Clone as that user into `/opt/castrol-video-pipeline`. The repo remote is the
SSH alias `github-castrolinfra` — the deploy key belongs to `castrolinfra-sys`,
not to any personal account.

### 3. Python environment

```bash
curl -LsSf https://astral.sh/uv/install.sh | sudo -u castrol sh
```

```bash
sudo -u castrol sh -c 'cd /opt/castrol-video-pipeline && uv sync --frozen --no-dev'
```

This creates `.venv/bin/castrol`, which is what the systemd unit runs. `uv run`
is deliberately *not* in the unit: it re-resolves the environment on every fire,
which needs the network and can change what runs at midnight without anyone
deciding to.

### 4. Credentials

Copy `.env.example` to `/opt/castrol-video-pipeline/.env` and fill it in.

```bash
sudo chown castrol:castrol /opt/castrol-video-pipeline/.env && sudo chmod 600 /opt/castrol-video-pipeline/.env
```

The file is read by pydantic **relative to the working directory**, which is why
the unit sets `WorkingDirectory=` and does not use `EnvironmentFile=` — systemd's
parser is not a shell and would mangle quoted values that pydantic reads fine.

For a real run the pipeline needs, beyond the database URL:
`CLIENT_EXPORT_URL` + `CLIENT_EXPORT_API_KEY`, AWS keys +
`S3_BUCKET` + `CDN_BASE_URL` with `STORAGE_BACKEND=s3`, `CARTESIA_API_KEY` +
`TTS_VOICE_ID`, `APIMART_API_KEY`, `KIE_API_KEY`. And:

```
ENVIRONMENT=prod
USE_STUB_STAGES=false
DELIVERY_ENABLED=false
```

### 5. Prove it before arming the timer

```bash
sudo -u castrol sh -c 'cd /opt/castrol-video-pipeline && .venv/bin/castrol doctor'
```

Then a full free rehearsal — set `USE_STUB_STAGES=true`, run one cycle, confirm
jobs reach `completed`, and set it back:

```bash
sudo -u castrol sh -c 'cd /opt/castrol-video-pipeline && .venv/bin/castrol cycle'
```

### 6. Install the timer

```bash
sudo cp /opt/castrol-video-pipeline/deploy/castrol-cycle.{service,timer} /etc/systemd/system/
```

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now castrol-cycle.timer
```

```bash
systemctl list-timers castrol-cycle
```

The timer carries its own timezone (`00,12:00:00 Asia/Kolkata`), so the server
can stay on UTC. That needs systemd 252+; on anything older, drop the suffix and
run `sudo timedatectl set-timezone Asia/Kolkata` instead.

`Persistent=true` means a run missed because the instance was down or rebooting
happens at the next boot rather than being skipped. A missed pull is not
recoverable by anything downstream — nobody notices videos that were never made.

---

## Running it as a container instead

CI already builds and pushes an image on every push to `main`, and that image
installs `ffmpeg` and `fonts-dejavu-core` and smoke-tests both. If Docker is on
the box, this is the more reliable path — it removes the entire class of "did
someone install the font on the new instance" failure.

```bash
echo 'CASTROL_IMAGE=acct/castrol-video-pipeline:main-abc1234' | sudo tee /etc/castrol-image.env
```

Pin the **sha** tag, never `latest`: a worker that spends a dollar a job must not
change what it runs because someone merged a branch.

```bash
sudo cp /opt/castrol-video-pipeline/deploy/castrol-cycle-docker.service /etc/systemd/system/castrol-cycle.service
```

Then the timer install is identical. Note the unit *mounts* `.env` rather than
passing `--env-file`: Docker's `--env-file` parser does not strip quotes, so
`KEY="value"` would arrive with the quotes attached.

---

## Reading what happened

Everything goes to journald as structlog JSON.

```bash
journalctl -u castrol-cycle --since today -o cat
```

```bash
journalctl -u castrol-cycle --since today -o cat | jq -c 'select(.level=="error")'
```

The last run's own summary — window, intake counters, per-stage run counts,
elapsed time, jobs by status — is the final JSON object the command prints.

Against the database, from any machine with the `.env`:

```bash
uv run castrol report
```

```bash
uv run castrol costs --since 2026-09-09
```

```bash
uv run castrol show <job-id>
```

```bash
uv run castrol events <job-id>
```

The admin panel reads the same tables and is the right place for anyone who is
not going to run a CLI.

### Exit codes

| code | meaning |
|---|---|
| 0 | ran clean, or skipped because another cycle held the lock |
| 1 | the export pull failed, **or** the deadline was reached with work outstanding |

Neither loses work. Alert on `systemctl --failed` containing `castrol-cycle`.

---

## Intervening

Stop the schedule without touching anything in flight:

```bash
sudo systemctl disable --now castrol-cycle.timer
```

Run one cycle by hand, right now:

```bash
sudo systemctl start castrol-cycle.service
```

Finish outstanding work without pulling anything new:

```bash
sudo -u castrol sh -c 'cd /opt/castrol-video-pipeline && .venv/bin/castrol cycle --no-fetch'
```

Backfill a window the timer never covered — a day the instance was down longer
than a reboot, or the first run against historical submissions:

```bash
sudo -u castrol sh -c 'cd /opt/castrol-video-pipeline && .venv/bin/castrol cycle --lookback-days 7'
```

Turning delivery on is a deliberate act (invariant 28) and takes effect on the
next cycle — set `DELIVERY_ENABLED=true` in `.env`. Jobs that already completed
without posting will not re-run on their own; `castrol redo <job-id> --stage
deliver` reopens one, and that stage is free.
