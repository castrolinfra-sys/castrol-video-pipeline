# EC2 deployment — as built

What actually exists in AWS, what was decided while building it, and what
proved it works. Written 2026-09-15.

**This is the record, not the runbook.** How to install and operate the worker
is [`deploy/README.md`](../deploy/README.md); how to recreate this instance from
nothing is its "Provisioning the instance" section. This file says what is
running *today*, which ids it has, and why each choice was made — the things a
runbook stops telling you once the work is done.

---

## 1. The account is shared. This is the first thing to know.

CLAUDE.md used to say AWS was a dedicated login like GitHub, Supabase and
Vercel. **It is not**, and that was corrected on 2026-09-15 when this instance
was provisioned.

`castrol-local` lives in account **872515254882**, which is the shared BeHooked
account. Running in it, in the same default VPC:

| instance | state |
|---|---|
| `behooked-studio-backend-prod` | running |
| `hooked-micro-apps` | running |
| `hooked-nodeflow` | running |
| `orchestrator-prod` | running |
| `caption-studio` | stopped |

and buckets `behooked-dokploy-backups`, `cached-brolls`, `caption-studio`
alongside `castrol-video-pipeline`.

What *is* dedicated is the **IAM user** and the **bucket**. That is the whole
of the separation. Every resource created for this project is therefore named
`castrol-*`, and the security group is the boundary that does real work.

---

## 2. What exists

Region **`ap-south-1`** throughout — the same region as the bucket, which
invariant 21 already ties presigning to.

| | |
|---|---|
| instance | `i-0d7560cd333c94cde` (`castrol-pipeline`) |
| type | `t3.medium`, `CpuCredits=standard` |
| AMI | `ami-0c0fd09cfe77b59dc` — Ubuntu 24.04 LTS |
| root volume | 30 GB gp3, **encrypted** |
| subnet / AZ | `subnet-09d461dcda7204047`, `ap-south-1a` (default VPC `vpc-0f7de061b0fa8c299`) |
| security group | `sg-050a83ee0b548a14c` (`castrol-pipeline`) — **zero ingress**, full egress |
| instance profile | `castrol-pipeline-ec2` → role `castrol-pipeline-ec2` |
| role policy | `AmazonSSMManagedInstanceCore` and nothing else |
| IMDS | `HttpTokens=required` (IMDSv2) |
| public IP | dynamic, no EIP |

Provisioned with the **root** account because `castrol-local` is denied
`ec2:CreateSecurityGroup` and all of IAM. That denial is deliberate and should
stay: the pipeline key ships in `.env` on the worker, and a credential that can
launch instances is a far worse thing to leak than one that can write objects.

---

## 3. Decisions, and why

### `t3.medium`, not `t3.large`

The runbook's original "t3.large or better" was a rule of thumb, not a
conclusion. Worked from the measurements instead:

- composite is **28s of ffmpeg per video** and is the only CPU-bound step;
  everything else is ~1s
- throughput is capped by money, not time — `vendor_limits.daily_cost_cap_usd`
  is **$5000/day on `kie_video`** after migrations `0011`/`0012`, about 4,732
  videos at the measured $1.0567 each. That is a runaway guard, not a budget;
  observed volume is ~40 videos (~$44) a day

So the real daily load is roughly **21 minutes of ffmpeg**. The decisive
detail: `t3.small`, `t3.medium` and `t3.large` all have **2 vCPUs** — they
differ in RAM and credit accrual, not core count, so a render takes the same
wall-clock on any of them. `t3.medium` earns ~576 credit-minutes/day against
~42 vCPU-minutes of work, two orders of magnitude of headroom.

Revisit only if the video lane's cap rises a long way. Even the `$200` example in the
runbook (~190 videos/day) is about an hour and a half of ffmpeg. If composite ever really
does bind, the answer is more workers — claiming is `SKIP LOCKED` — not a
bigger box.

`CpuCredits=standard` rather than the T3 default of `unlimited`: unlimited
bills extra instead of throttling when credits run out. For a pipeline built
entirely on reserve-before-spend and hard caps, a silent overage charge is the
wrong failure mode, and with this much headroom it will never trigger anyway.

### No inbound. No SSH. No key pair.

The security group has **no ingress rules** and never should. This box accepts
nothing; it only dials out — the export API, three vendors, S3, Supabase.

Shell access is **Session Manager**, which works without an open port because
the agent dials outbound and the session is relayed back down that connection.
EC2 Instance Connect and plain SSH both need port 22 open inbound and are
therefore unavailable by design — if the console reports "Error establishing
SSH connection", that is the *EC2 Instance Connect* tab, and the fix is to use
the *Session Manager* tab, not to open a port.

`HttpTokens=required` matters here more than it does on a typical box: this
pipeline fetches URLs it did not choose, taken from the client's export feed.
IMDSv2 is what stops a fetch bug from becoming instance-credential theft.

### Static AWS keys, not the instance role

The obvious improvement is to drop `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`
from `.env` and let the instance role supply credentials — and the code already
supports it, since `S3Backend` passes `access_key or None` and `None` falls
through to boto3's default chain.

**It was considered and rejected.** `presigned_get_url` signs for **six hours**,
deliberately: the docstring records that the video step can queue for 20 minutes
and take 20 more, and "a link that dies mid-render fails the stage for a reason
no log explains". A URL signed with *temporary* credentials is only valid until
the session token expires, so its real lifetime is
`min(requested, credential remaining)` — and IMDS credentials rotate, so a
presign issued shortly before a rotation would die well inside the render
window. Intermittently, and invisibly.

This was already the recorded design: see
[`TECH_DESIGN.md` §11](TECH_DESIGN.md) and
[`PROJECT_PLAN.md` §risks](PROJECT_PLAN.md), both of which call out that a URL
signed with EC2 instance-role credentials dies when the session token does.

The instance role therefore carries `AmazonSSMManagedInstanceCore` and no S3
access at all. An unused permission on a box that fetches client-supplied URLs
is just surface.

If the static keys are ever to go, the clean route is not a different credential
source — it is to stop handing vendors presigned URLs. Invariant 16 already
wants a URL that returns bytes on the first GET, and a CloudFront URL over an
unguessable key satisfies both with no signature to expire. That is a design
change, not a deploy step.

### Continuous deploy, tracking `:latest`

Decided 2026-09-15, reversing the earlier "deploys pin the sha tag". The unit
carries `--pull always` and `/etc/castrol-image.env` holds
`gethooked/castrol-video-pipeline:latest`.

The spend argument for pinning did not survive checking. `_schedule_all()`
selects `status NOT IN ('completed','cancelled')`, so a changed `input_hash`
**never** re-renders a finished video — only jobs still open when the new image
first runs, and the pull lands between cycles rather than mid-batch. A cycle is
`Type=oneshot`, so an in-flight run always finishes on the image it began with.

The two halves are not separable: `docker run` reuses a cached image when the
tag exists locally, so `:latest` *without* `--pull always` would pull once and
then run that build forever while appearing to track main. A silent freeze is
worse than either choice made on purpose.

What the choice actually costs is a review gate. A merge reaches a paying worker
with no staging and no visual check, and `pytest` green says the DAG is sound,
not that a render looks right — every prompt and card revision in this project
was judged by eye. Before merging anything touching `AVATAR_PROMPT`,
`IMAGE_PROMPT`, a model id or the card geometry, prove it through
`spikes/prototype.py` or pin a sha until you have looked at a render.

### Host uid pinned to 10001

`useradd --uid 10001` on the host, matching the uid the Dockerfile creates. The
unit bind-mounts a `0600` `.env` into the container, a bind mount carries the
host's numeric ownership through unchanged, and a mismatch means the container
reads **an empty config** rather than reporting a permission error. The run then
fails complaining about missing settings and sends you looking in the wrong
place entirely.

Note `--system` was dropped from `useradd`: it asks for a uid below
`SYS_UID_MAX` (999), so pairing it with a fixed 10001 only produces a warning.

The file ends up `castrol:UNKNOWN` in `stat`, because gid 10001 does not exist
on the host. That is correct and should be left alone — mode `0600` means group
permissions are never consulted, and the owner uid is what the container matches.

### `.env` written on the box

Not copied from a laptop, so the file never exists in two places. Deliberately
omitted from it:

| omitted | why |
|---|---|
| `IMAGE_PROMPT_VERSION`, `CARD_TEMPLATE_VERSION` | the local `.env` pinned both to `v1`, cancelling the bumps to `v2`/`v4`. Hash-inputs only, so output still renders correctly — but open jobs would **skip stage B and composite and ship stale inventory**, which is exactly what invariant 30 warns about. Letting the code defaults win is the fix. |
| `SCRIPT_VERSION`, `NORMALISE_RULES_VERSION` | same convention; the values matched the defaults anyway |
| `SUPABASE_SERVICE_ROLE_KEY` | legacy key style, and the pipeline uses no Supabase API key at all |
| `LOCAL_STORAGE_DIR`, `CLIENT_EXPORT_FORMAT` | unused under `STORAGE_BACKEND=s3`; the second is not a setting |

`ENVIRONMENT=prod`, `USE_STUB_STAGES=false`, `DELIVERY_ENABLED=false`.

Docker Hub is private, so the pull is authenticated. `docker login` was run **as
`castrol`** with `-H`, because systemd reads that user's
`~/.docker/config.json` and not root's. Use a **Read-only** access token, not
the Read & Write one CI holds: the credential is stored base64-encoded, not
encrypted, and a token that can only pull is the difference between a leak that
reads the image and one that can replace it with a build this box then runs
twice a day.

---

## 4. Verified

Each of these was checked, not assumed.

| check | result |
|---|---|
| instance posture | `running`, `t3.medium`, IMDSv2 `required`, credits `standard`, 30 GB gp3 encrypted |
| security group | `IpPermissions: []` — confirmed twice, before and after setup |
| SSM registration | `PingStatus: Online`, agent `3.3.4793.0` |
| host user | `uid=10001(castrol)`, in the `docker` group |
| docker as `castrol` | `docker ps` succeeds without root |
| `.env` | mode `600`, owner uid 10001, all 32 expected keys present |
| image identity | pulled digest `sha256:cd6ff0bc…` — **matches the digest CI pushed** |
| `castrol doctor` through the real mount | `db: ok`, `jobs: 9`, `environment: prod`, `storage_backend: s3`, `use_stub_stages: false` |
| unit files | `systemd-analyze verify` clean |

The `doctor` line is the one that matters: it ran as the real user, through the
real bind mount, against the real database. A wrong uid, a wrong tag, a bad
credential or a missing setting would all have surfaced there rather than at
midnight.

---

## 5. Not done yet

- **The timer is not armed.** `castrol-cycle.timer` is installed but not
  enabled, so nothing runs on a schedule and nothing has spent.
- **Delivery is off.** `DELIVERY_ENABLED=false`. When it is turned on, the first
  cycle afterwards reopens every suppressed delivery at once — that is invariant
  32 working, not a bug, and the client stores `{phone, videoLink}`
  idempotently.
- **Continuous deploy makes an interrupted cycle routine**, which is why
  `_repair_orphans` exists (invariant 33): a cycle killed between intake's three
  transactions leaves a submission with no job or a job with no photo, and
  nothing anywhere reports it. Every cycle repairs both, at warning level. There
  were zero orphans of either kind when it was written, so it is a guard for the
  first real batch rather than a fix for an observed incident — and it has no
  test yet.
- **The first run will spend.** At the time of writing the pull window held
  **15 submissions, all new** — none matching the 9 hand-seeded rows by
  `client_submission_id` or by phone. At the measured 26.9s average and the
  corrected `$0.036/s`:

  ```
  video   $0.014 + 26.9s × $0.036  =  $0.982
  image   gpt-image-2 @2K          ≈  $0.012
  tts     ~400 chars × $0.00005    ≈  $0.020
                                      ──────
  per video                           ~$1.01
  15 submissions                      ~$15
  ```

  Retries do **not** add to that, which this estimate originally assumed they
  would: every failed vendor job refunds its credits, so a render that fails and
  is retried bills once (migration `0013`). What retries still consume is the
  daily CALL cap, which is deliberately not refund-adjusted.

  Submissions arrive continuously — the window grew by one row in eleven
  seconds while this was being measured — so the real figure on the day will be
  higher. The 2026-09-15 pull bore that out: **44 rows in the 14-day window, 42
  valid, ~$46 all-in**, which would have hit the original $50 cap with one
  retry to spare. That is what prompted migration `0011`. The ceiling is now
  the video lane's $5000/day COST cap (~4,732 renders), with the call caps
  raised by
  `0012` to match so the money limit is what trips.

- **Root was used for provisioning.** Switch to `castrol-server` for
  administration; root cannot be scoped, revoked per-action, or attributed to a
  person.

---

## 6. Operating

Everything below is covered in full by [`deploy/README.md`](../deploy/README.md);
this is the short list.

```bash
aws ssm start-session --region ap-south-1 --target i-0d7560cd333c94cde
```

```bash
sudo systemctl enable --now castrol-cycle.timer
```

```bash
sudo systemctl start castrol-cycle.service
```

```bash
journalctl -u castrol-cycle --since today -o cat | jq -c 'select(.level=="error")'
```

Exit codes: `0` ran clean or skipped on the advisory lock; `1` the export pull
failed **or** the deadline hit with work outstanding. Neither loses work —
readiness is recomputed from `stage_runs`. Alert on `systemctl --failed`
containing `castrol-cycle`.
