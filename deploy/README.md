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

3. **Finishes the rows a crash left half-created.** Intake writes one mechanic
   across three transactions — submission, job, photo asset — so a process
   killed between them leaves a valid submission with no job, or a job with no
   source photo. Neither is an error anywhere: no stage fails, the counters look
   right, and that mechanic simply never gets a video. Both repairs are free and
   idempotent, so every cycle does them, and each one logs at **warning** level
   (`cycle.orphan_job_created`, `cycle.orphan_photo_restored`,
   `cycle.orphan_photo_failed`) because a silent self-heal hides a recurring
   crash. A photo whose SAS url has since expired is logged and skipped rather
   than failing the cycle.

4. **Reopens deliveries suppressed while `DELIVERY_ENABLED` was false.** See
   "The backlog reopens itself" below.

5. **Schedules every unfinished job.** Intake creates jobs but does not schedule
   them, and a previous cycle may have stopped at its deadline. This step is why
   a cycle needs no memory of what the last one did.

6. **Works, then waits, then works again** until nothing is queued and nothing
   is in flight at a vendor. This is the part `drain` gets wrong for unattended
   use: `drain` stops as soon as a sweep moves nothing, which for an avatar
   render means "still rendering" — it would submit every paid job and exit
   before collecting a single result.

7. **Stops at a deadline** (default 8 hours, inside the 12-hour gap) and
   exits 1 if work is still outstanding. Nothing is lost when this happens —
   readiness is recomputed from `stage_runs`, so the next cycle picks up exactly
   where this one stopped — but a batch that outlasts its own window is worth
   knowing about.

**Delivery is still off.** `DELIVERY_ENABLED=false` means the `deliver` stage —
the eighth and last of the pipeline, not a step in the list above — logs what it
would have POSTed and records the delivery row without posting. Everything up to
and including publish runs for real, so turning delivery on later requires no
re-render: flip the variable, and step 4 of the next cycle reopens the whole
suppressed backlog at once.

---

## How many videos a cycle can actually do

The deadline is not the limit, and the arithmetic looks like it should be. A
render takes ~8.6 minutes — but renders do not queue behind each other. The
image and video stages hand the job to the vendor and return immediately, so a
hundred renders are in flight at once and the wall clock is the *longest* one,
not the sum.

What is serial is only our own work. Measured end to end on a real completed job:

| stage | time | note |
|---|---|---|
| prep | 1s | |
| audio | <1s | voice provider, synchronous but fast |
| image | <1s | submit only |
| video | <1s | submit only — the 8.6 min render happens at the provider |
| **composite** | **28s** | ffmpeg burn-in, local, CPU-bound — the real bottleneck |
| checks / publish / deliver | 1s each | |

**~32 seconds of our own time per video.** At an 8-hour deadline that is roughly
900 videos per cycle, 1800 a day across two. Hundreds a day is not close to it.

The limit you will actually hit first is money:

```bash
uv run castrol costs
```

Since migrations `0011` and `0012` (2026-09-15) the caps are a runaway guard
rather than a budget. **`daily_cost_cap_usd` is the one that binds** — the call
caps were raised alongside it so the money limit trips first everywhere:

| vendor | cost cap | call cap | unit cost | calls when cost trips | binds on |
|---|---|---|---|---|---|
| `kie_video` | **$5000** | 5 000 | $1.0567 | ~4 732 | **cost** |
| `apimart_image` | **$500** | 40 000 | $0.0140 | ~35 714 | **cost** |
| `tts` | **$500** | 25 000 | $0.0226 | ~22 124 | **cost** |

Worst case $6000/day across the three, against an observed ~40 videos a day
(~$44). Both caps fail closed. The cap day is **IST** — the 00:00 and 12:00 IST
cycles draw on the same bucket, so a heavy midnight batch starves the noon one.

Raise or lower spend in the database. Move the COST cap, then check the call
cap still sits above it — otherwise the ceiling lands somewhere you did not
intend, which is exactly what `0011` did before `0012` fixed it:

```sql
UPDATE vendor_limits SET daily_cost_cap_usd = 200 WHERE vendor = 'kie_video';
```

If composite ever does become the constraint, the fix is more workers, not a
longer deadline: `castrol work --stage composite` can run in N processes against
the same database, because claiming is `SKIP LOCKED`.

---

## Provisioning the instance

**This is the shared BeHooked AWS account, not a Castrol-only one.** Account
`872515254882` runs `behooked-studio-backend-prod`, `hooked-micro-apps`,
`hooked-nodeflow` and `orchestrator-prod`, all in the default VPC, and the
worker goes in beside them. Only the IAM user and the bucket are dedicated
(CLAUDE.md, "Accounts"). So every resource below is named `castrol-*` and the
security group is the boundary that actually matters — nothing here accepts
inbound, and nothing here should touch another project's groups or roles.

Needs an **admin session**. The pipeline's own `castrol-local` is denied
`ec2:CreateSecurityGroup` and all of IAM, deliberately: that key ships in `.env`
on the worker, and a pipeline credential that can launch instances is a far
worse thing to leak than one that can write objects.

Region is **`ap-south-1`**, the same as `AWS_REGION` and the bucket: the
mechanics, the client and the data are all in India, and invariant 21 already
ties presigning to the bucket's own regional endpoint.

```bash
export AWS_REGION=ap-south-1
```

### The shape, and why

| | | why |
|---|---|---|
| type | `t3.medium`, `CpuCredits=standard` | composite is 28s of ffmpeg per video and is the only CPU-bound step, ~21 min of work a day. `t3.small`/`medium`/`large` all have **2 vCPUs** — they differ in RAM and credit accrual, not core count, so a render takes the same wall clock on any of them. This said `t3.large` as a rule of thumb; the measurement says medium, with two orders of magnitude of credit headroom. `standard` rather than the T3 default `unlimited`, which bills extra instead of throttling — a silent overage is the wrong failure mode here. See [`EC2_DEPLOYMENT.md` §3](../docs/EC2_DEPLOYMENT.md) |
| disk | 30 GB gp3 | the working set is small — artefacts go to S3 — but images, layers and journald need room. |
| AMI | Ubuntu 24.04 LTS | systemd 255, so the timer's `Asia/Kolkata` suffix works without touching the box clock. |
| inbound | **none** | this box accepts no connections. It only makes them: the export API, three vendors, S3, Supabase. |
| access | SSM Session Manager | no port 22, no key pair to lose, and every session is logged in CloudTrail. |

### 1. A role that grants shell access and nothing else

The instance role is for **Session Manager only**. The pipeline does not use it:
it reads static AWS keys from `.env` (CLAUDE.md, "Accounts"), so nothing here
needs S3 permissions and the role should not have any.

```bash
aws iam create-role --role-name castrol-pipeline-ec2 --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
```

```bash
aws iam attach-role-policy --role-name castrol-pipeline-ec2 --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore
```

```bash
aws iam create-instance-profile --instance-profile-name castrol-pipeline-ec2 && aws iam add-role-to-instance-profile --instance-profile-name castrol-pipeline-ec2 --role-name castrol-pipeline-ec2
```

### 2. A security group with no ingress rules

```bash
aws ec2 create-security-group --group-name castrol-pipeline --description "Castrol video pipeline worker - egress only" --vpc-id vpc-0f7de061b0fa8c299
```

That is the **default VPC**, which is where the existing BeHooked services
already live. Create the group and leave it alone: a new group has no inbound
rules and full outbound, which is exactly right — **do not add SSH**, and do
not attach any group belonging to another project. Outbound 443 carries the
vendors and S3; outbound 5432 carries Supabase's session pooler.

### 3. Launch

The AMI is resolved through SSM rather than pasted, so this command does not
rot into a stale image id the first time Canonical publishes a new build:

```bash
aws ec2 run-instances --image-id resolve:ssm:/aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id --instance-type t3.medium --credit-specification CpuCredits=standard --iam-instance-profile Name=castrol-pipeline-ec2 --security-group-ids <sg-id> --subnet-id <subnet-id> --block-device-mappings '[{"DeviceName":"/dev/sda1","Ebs":{"VolumeSize":30,"VolumeType":"gp3","Encrypted":true}}]' --metadata-options 'HttpTokens=required' --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=castrol-pipeline}]'
```

`HttpTokens=required` forces IMDSv2. It costs nothing and closes the SSRF path
that turns any "fetch this URL" bug into instance-credential theft — and this
pipeline fetches URLs it did not choose, from the client's export feed.

Use a default-VPC subnet that assigns public IPs — `subnet-09d461dcda7204047`
(`ap-south-1a`), `subnet-03cbf140798cc2add` (`1b`) or `subnet-06fcad4da114e3551`
(`1c`). The subnet must have a route to the internet, because Session Manager
needs outbound 443 just as the vendors do. The AZ does not matter: there is no
state on this box worth pinning to one.

```bash
aws ssm start-session --target <instance-id>
```

If that connects, the role, the agent and egress are all correct at once, and
you never opened a port to get there.

---

## One-time server setup

**Two paths, and you want the container one.** What follows installs the
pipeline as a checkout plus a venv, with `ffmpeg` and the fonts on the host.
[Running it as a container instead](#running-it-as-a-container-instead) is the
recommended path and is written standalone — if that is what you are doing, skip
straight there and come back only for the `.env` contents in section 4. This
section is still the reference for anyone debugging by hand on the box.

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
sudo useradd --uid 10001 --create-home --home-dir /opt/castrol-video-pipeline castrol
```

**The uid is pinned, and it has to match the image's.** The Dockerfile creates
its own `castrol` at uid 10001, and the container path below bind-mounts the
host's `.env` — which is `0600` and carries its host ownership through the mount
unchanged. A host user at any other uid means the container cannot read a single
line of it, and what you see is a run that dies on missing settings rather than
on anything that says "permission". If the user already exists at another uid,
`sudo chown 10001:10001 /opt/castrol-video-pipeline/.env` instead.

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

`OnBootSec=5min` covers the other half: a cycle that had already *started* when
the instance went down. That run was not missed, so `Persistent` does nothing for
it, and its queued stages and in-flight renders would otherwise wait for the next
00:00 / 12:00. Five minutes after any boot a normal cycle runs and picks up
exactly where the rows say. See [After a restart](#after-a-restart).

---

## Running it as a container instead

**This is the recommended path, and it is self-contained** — it replaces
sections 1 and 3 above entirely. Nothing is installed on the box but Docker:
the image carries `ffmpeg`, `ffprobe` and `fonts-dejavu-core`, and CI
smoke-tests all three against the exact artefact it pushed. That removes the
whole "did someone install the font on the new instance" class of failure,
which matters because both of those fail *silently* — a missing `ffprobe` is a
wrong bill, not a crash, and a missing font is a shipped card in the wrong face.

There is **no checkout and no venv**. The two files the box needs are copied
from your workstation.

### 1. Docker

```bash
sudo apt-get update && sudo apt-get install -y docker.io && sudo systemctl enable --now docker
```

### 2. The user, at the image's uid

```bash
sudo useradd --uid 10001 --create-home --home-dir /opt/castrol-video-pipeline castrol && sudo usermod -aG docker castrol
```

No `--system`: that flag asks for a uid under `SYS_UID_MAX` (999), and 10001 is
deliberately not one, so pairing them only produces a warning before useradd
does what you asked anyway.

The uid is not cosmetic. The unit bind-mounts a `0600` `.env` into a container
that runs as uid 10001, and a bind mount carries the host's numeric ownership
through unchanged — a mismatch means the process cannot read one line of it,
and the run dies on missing settings rather than on anything that says
"permission". The docker group is what lets this non-root user reach
`/var/run/docker.sock`; without it the unit fails before the image is even
pulled, so nothing in the journal mentions the pipeline at all.

### 3. Credentials

Written by hand on the box, not copied from a laptop — the file never has to
exist in two places, and nothing carries it over a wire we did not choose.
Section 4 above lists what a real run needs.

```bash
sudo -u castrol nano /opt/castrol-video-pipeline/.env
```

```bash
sudo chown 10001:10001 /opt/castrol-video-pipeline/.env && sudo chmod 600 /opt/castrol-video-pipeline/.env
```

The chown is not optional and is the single easiest thing to skip here: the
unit mounts this file into a container running as uid 10001, a bind mount keeps
the host's numeric owner, and `0600` owned by anyone else reads as an empty
config rather than as a permission error.

`USE_STUB_STAGES` and `DELIVERY_ENABLED` are already `false` in the image, so
the `.env` only decides when you want them *on*. Leave `DELIVERY_ENABLED=false`
for the first real run — turning it on is invariant 28's deliberate act, and by
invariant 32 the first cycle afterwards reopens every suppressed delivery at
once.

### 4. Set the image tag

```bash
echo 'CASTROL_IMAGE=gethooked/castrol-video-pipeline:latest' | sudo tee /etc/castrol-image.env
```

**The worker tracks `latest` and the unit carries `--pull always`.** CI moves
that tag on every green push to `main`, so a merge is a deploy and the next
cycle runs it.

The two halves are not separable. `docker run` reuses a cached image when the
tag is already present locally, so `:latest` *without* `--pull always` would
pull once on the first cycle and then run that build forever while appearing to
track `main` — a silent freeze, which is worse than either choice made on
purpose.

To freeze deliberately — a risky merge, a bad batch, an incident — pin the sha
instead and the same file does it:

```bash
echo 'CASTROL_IMAGE=gethooked/castrol-video-pipeline:main-331b386' | sudo tee /etc/castrol-image.env
```

### 5. Prove the mount before arming anything

This is the step that catches a wrong uid, a wrong tag and a bad `.env` in one
go — run it **as `castrol`**, because running it as root proves nothing about
the user systemd will actually use:

```bash
sudo -u castrol sh -c '. /etc/castrol-image.env && docker run --rm --volume /opt/castrol-video-pipeline/.env:/app/.env:ro "$CASTROL_IMAGE" doctor'
```

Then a free rehearsal: set `USE_STUB_STAGES=true` in the `.env`, run `cycle` the
same way, confirm jobs reach `completed`, and set it back.

### 6. Install the units

Copy `deploy/castrol-cycle-docker.service` and `deploy/castrol-cycle.timer`
from the repo on your workstation to the instance, then:

```bash
sudo install -m 644 castrol-cycle-docker.service /etc/systemd/system/castrol-cycle.service
```

```bash
sudo install -m 644 castrol-cycle.timer /etc/systemd/system/castrol-cycle.timer
```

The docker unit is installed **under the plain name** — the timer points at
`castrol-cycle.service` and does not know which path is in use.

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now castrol-cycle.timer
```

```bash
systemctl list-timers castrol-cycle
```

Everything section 6 says about the timer applies unchanged: it carries its own
`Asia/Kolkata` timezone so the box can stay on UTC, and `Persistent=true` runs a
window missed to a reboot at the next boot rather than skipping the batch.

### 7. Install the `castrol` command

Copy `deploy/castrol` to the instance the same way, then:

```bash
sudo install -m 755 castrol /usr/local/bin/castrol
```

```bash
castrol doctor
```

Every command in this README that reads `uv run castrol ...` now runs on the box
as plain `castrol ...` — same image tag as the timer, same `.env` mount, same
uid. See [Finding and running one job](#finding-and-running-one-job).

Note the unit *mounts* `.env` rather than passing `--env-file`: Docker's
`--env-file` parser does not strip quotes, so `KEY="value"` would arrive with
the quotes attached.

### Upgrading

**Nothing to do.** Merge to `main`, let CI go green, and the next cycle pulls
it. There is no step on the box.

What that buys, and what it costs, is worth being clear about. A cycle is
`Type=oneshot` and the pull happens at start, so an in-flight run always
finishes on the image it began with — a new build is never swapped in
mid-batch. And a finished video is never re-rendered by a code change:
`_schedule_all()` only touches jobs that are not `completed` or `cancelled`.
What a new image *can* re-run is a job left open by a failure or a deadline,
which is a handful at worst.

The real cost is that there is no staging and no visual review between a merge
and a worker that spends about a dollar a job. `pytest` passing says the DAG is
sound; it says nothing about whether a render looks right, and every prompt and
card revision in this project was judged by eye.

So before merging anything that touches `AVATAR_PROMPT`, `IMAGE_PROMPT`, a model
id or the card geometry, either prove it through `spikes/prototype.py` first or
pin the sha on the box until you have looked at a render.

To check what the box is actually running:

```bash
sudo -u castrol -H docker image inspect --format '{{index .RepoDigests 0}}' "$(. /etc/castrol-image.env && echo "$CASTROL_IMAGE")"
```

---

## Finding and running one job

Nobody on the box has a job UUID. Every per-job command takes **any** identifier
and tries all of them at once: job id, submission id, the client's export row
`id`, the WhatsApp number (any shape — `9773128990`, `+91 97731 28990`), the
card phone, `mechanic_id`, a stage run id, or a vendor task id lifted from a log.
Each hit says what it matched on; if one string matches two jobs you get both
and pass the job id.

```bash
castrol find 9773128990
```

```bash
castrol find --status failed --since 2026-09-16
```

```bash
castrol show 9773128990
```

```bash
castrol events 9773128990
```

**Run one job to a finish, now, without touching anything else queued:**

```bash
castrol run 9773128990
```

It prints each stage's latest state, lists the paid stages it may submit, and
asks before spending. Only that job's runs are claimed and polled — `work` and
`drain` would take the whole queue. It is fine alongside a running cycle
(claiming is `SKIP LOCKED`), and a per-job lock stops two people running the
same job at once (exit 3). It waits up to 150 min, which is past the 2h vendor
timeout, so it ends on `completed` (exit 0) or `failed` (exit 1).

**A render is 8–20 minutes, and an SSM session that drops kills a foreground
command.** That loses nothing, since running it again carries on from the
database, but the detached form just finishes on its own:

```bash
castrol --bg run 9773128990 --yes
```

It prints the `journalctl -u castrol-run-…` line to follow. `--bg` refuses
`run`/`redo` without `--yes` because there is no terminal to confirm on — look
at `castrol show` first.

**Failed stages stay failed unless you say otherwise.** A terminal failure —
content-safety rejection, vendor timeout, attempts exhausted — is not retried by
the cycle at the same inputs. To pay for another attempt:

```bash
castrol run 9773128990 --retry
```

Two things reopen a failure without `--retry`: its inputs changing (a new prompt
or plate is a different question), and `BUDGET_EXHAUSTED` from an earlier IST
day, because the cap is per day.

A **completed** job has nothing to run; re-rendering one is still
`castrol redo <any-id> --stage video`, followed by `castrol run <any-id>`.

Every manual command is also in journald, so what someone ran overnight is
readable in the morning. `castrol run` additionally writes `job.manual_run` and
`job.manual_run_done` into that job's `events`.

```bash
journalctl -t castrol-manual --since today -o cat
```

---

## Rehearsal before launch (dry run)

One full run against the client's **real** export, into the **real** database,
with only the three paid steps faked. Everything else is the production code:
the card is burned with real names and addresses, ffmpeg runs, the result is
uploaded to the CDN. **Nothing is spent and nothing is sent to the client.**

| stage | in the rehearsal |
|---|---|
| intake, prep, composite, checks, publish | real |
| audio | a quiet tone as long as the real voiceover would be |
| image | the job's own plate artwork, unedited |
| video | that frame held still over the audio, "rendering" for 2 min |
| deliver | real, with `DELIVERY_ENABLED=false`: logs what it would post |

`MOCK_FAILURES` fails a fixed share of jobs on purpose, the same jobs every
time. `castrol costs` afterwards is a **quote** for the real batch - the mocks
record what each call would have cost.

It is kept apart from a real run by the code, not by memory: mock mode refuses
to start unless files go under `castrol-dryrun/` and delivery is off, and a real
run refuses to start while any rehearsal row is left in the database.

### 1. Stop the timer

```bash
sudo systemctl disable --now castrol-cycle.timer
```

It must stay off until launch. A reboot during the rehearsal would otherwise
fire `OnBootSec` with the **real** `.env` and start a paid cycle.

### 2. Get this code onto the box

**The image first.** Mock mode lives in the image, not on the box. Push this
commit to `main` and wait for CI to go green; the wrapper pulls `:latest` on
every command. An older image does not know `STAGE_MODE` and would quietly run
the REAL stages — the dummy keys in step 4 would stop them at the provider's
login, and step 5's `doctor` check is what catches it: no `"stage_mode":
"mock"` in the output means the image is too old.

Then copy `deploy/castrol` from this commit to the box:

```bash
sudo install -m 755 castrol /usr/local/bin/castrol
```

### 3. Save the paid test history

The wipe at step 7 removes it. Write it to your own home directory - the
pipeline directory belongs to `castrol`:

```bash
castrol costs --since 2026-09-14 > ~/costs-before-dryrun.json
```

### 4. Create `.env.dryrun`

A copy of the real `.env`, with overrides appended. Later lines win, so nothing
in the copy needs editing by hand:

```bash
sudo cp -p /opt/castrol-video-pipeline/.env /opt/castrol-video-pipeline/.env.dryrun
```

```bash
sudo tee -a /opt/castrol-video-pipeline/.env.dryrun > /dev/null <<'EOF'

# ---- rehearsal overrides ----
STAGE_MODE=mock
MOCK_FAILURES=image:VENDOR_REJECTED:10,video:VENDOR_TIMEOUT:5,audio:TRANSIENT:10
S3_PREFIX=castrol-dryrun/
VENDOR_TASK_TIMEOUT_S=300
DELIVERY_ENABLED=false
CLIENT_WEBHOOK_URL=
KIE_API_KEY=dryrun-invalid
APIMART_API_KEY=dryrun-invalid
CARTESIA_API_KEY=dryrun-invalid
EOF
```

The dummy keys are a second lock: even a bug that reached a real provider could
not authenticate. `VENDOR_TASK_TIMEOUT_S=300` makes the injected video timeouts
fail in 5 minutes instead of 2 hours.

### 5. Check it, then run it

```bash
castrol --dryrun doctor
```

Expect `"stage_mode": "mock"`, `"s3_prefix": "castrol-dryrun/"` and
`"mode_guard": "ok"`. Anything else - stop.

```bash
castrol --dryrun --bg cycle --lookback-days 7
```

**`--dryrun` must come first on every command in the rehearsal.** Without it
the real `.env` is mounted. It is a flag rather than an environment variable on
purpose: `sudo` drops environment variables, a flag cannot be dropped.

### 6. Look at it

```bash
castrol --dryrun report
```

```bash
castrol --dryrun find --status failed
```

```bash
castrol --dryrun events <any id from the list>
```

```bash
castrol --dryrun run <a failed id> --retry
```

- Roughly 75% of jobs should be `completed`, and the failures split across image
  rejections, video timeouts, and audio errors that recovered on retry.
- Open 3-4 CDN links from `castrol --dryrun show <id>` and check the card: long
  names, long addresses, the phone number.
- The admin panel shows these jobs - check Jobs and Failures read correctly for
  the client. Do not give the client a login until after step 7.
- Every job logs one `checks.failed` warning for `bitrate_plausible`. **Expected
  here:** a still frame compresses far below real video. The check is
  non-blocking and real renders pass it.
- Reboot test: `sudo reboot` mid-cycle, then run the step-5 command again and
  confirm it carries on. (The timer is off, so the boot run does not fire.)

### 7. Wipe before launch

Real `.env` - no `--dryrun`:

```bash
castrol reset-for-launch
```

It lists every table and row count, keeps `plates`, `vendor_limits`, the panel
logins and the migrations ledger, and wipes only when you type `RESET`.

Then, from an **admin** session (`castrol-local` cannot delete):

```bash
aws s3 rm s3://<S3_BUCKET>/castrol-dryrun/ --recursive
```

**Only `castrol-dryrun/`.** Never `castrol/` - it holds the plates, the uniform
references and the real renders.

```bash
sudo rm /opt/castrol-video-pipeline/.env.dryrun
```

```bash
castrol doctor
```

Expect `"rehearsal_residue": {"mock_runs": 0, "dryrun_assets": 0}` and
`"mode_guard": "ok"`. If you skipped the wipe, every real command refuses here
with a message naming `reset-for-launch`.

### 8. Launch

The first real run reaches back to the campaign's first submission - a normal
cycle only looks back one day:

```bash
castrol --bg cycle --lookback-days <days since first submission>
```

```bash
sudo systemctl enable --now castrol-cycle.timer
```

---

## After a restart

Nothing needs doing. The pipeline keeps no state outside Postgres: readiness is
recomputed from `stage_runs`, a render submitted before the reboot keeps going at
the vendor, and a claim orphaned mid-stage is returned to the queue by the reaper
after `STAGE_CLAIM_TIMEOUT_S` (15 min).

What brings it back:

| what was happening | what resumes it |
|---|---|
| a cycle was running | `OnBootSec=5min` starts a cycle after boot |
| a cycle was due while the box was down | `Persistent=true` runs it at boot |
| a `castrol run` / `--bg run` was running | nothing automatic. The boot cycle collects that job with everything else; or run the same command again |
| a stale `castrol-cycle` container survived a hard stop | `ExecStartPre` removes it before the next start |

To confirm the boot run happened:

```bash
journalctl -u castrol-cycle -b -o cat | jq -c 'select(.event=="cycle.start" or .event=="cycle.done")'
```

---

## Reading what happened

Everything goes to journald as structlog JSON.

```bash
journalctl -u castrol-cycle --since today -o cat
```

```bash
journalctl -u castrol-cycle --since today -o cat | jq -c 'select(.level=="error")'
```

The last run's own summary — window, intake counters, `repaired`, `redelivering`,
per-stage run counts, elapsed time, jobs by status — is the final JSON object the
command prints.

**Three warning-level events mean something upstream was interrupted**, and all
three are self-heals rather than failures. Worth reading every morning, because
a repair that recurs is a crash that recurs:

```bash
journalctl -u castrol-cycle --since today -o cat | jq -c 'select(.event|startswith("cycle.orphan"))'
```

`cycle.orphan_job_created` — a submission had no job. `cycle.orphan_photo_restored`
— a job had no source photo and it was refetched. `cycle.orphan_photo_failed` —
the same, but the SAS url had expired and the photo is unrecoverable; that job
will never render and needs the row cancelling or the client re-issuing the
media.

Against the database, on the box as `castrol …` or from any machine with the `.env`:

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
next cycle — set `DELIVERY_ENABLED=true` in `.env`.

**The backlog reopens itself.** A suppressed delivery *succeeds* the stage — it
has to, or a finished video would sit as `failed` forever — so every job made
while the flag was false is already marked delivered, is `completed`, and has a
`deliver_hash` that does not change when the flag flips. Nothing would ever
schedule it again. So the first cycle after delivery is enabled reopens all of
them itself (`cycle.py:_reopen_suppressed_deliveries`, invariant 32) and logs
`cycle.redelivering` with the count. The stage is free and the client stores
`{phone, videoLink}` idempotently, so a repeat post is harmless where a missed
one is a video nobody ever gets. `castrol redo <job-id> --stage deliver` is for
a single job, not for the backlog.
