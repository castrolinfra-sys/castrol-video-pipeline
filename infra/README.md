# AWS setup

Commands to run yourself. The pipeline's IAM user (`castrol-local`) deliberately
holds only object read/write on the bucket — it cannot change bucket
configuration, delete objects, or touch CloudFront. Everything here is a
one-time administrative change made with an account that can.

Run from the repo root. Substitute nothing — the bucket and distribution names
below are this project's.

## 1. Object lifecycle (do this before first delivery)

Delivered links must live 6 months and then stop working. Presigned URLs cannot
express that — SigV4 caps expiry at 7 days — so a delivered link is a plain CDN
URL over an unguessable key, and it dies because **the object is deleted on
schedule**, not because a signature lapsed. That schedule is this rule.

```bash
aws s3api put-bucket-lifecycle-configuration --bucket castrol-video-pipeline --lifecycle-configuration file://infra/s3-lifecycle.json
```

Verify:

```bash
aws s3api get-bucket-lifecycle-configuration --bucket castrol-video-pipeline
```

What it does:

| Prefix | Expires | Why |
|---|---|---|
| `castrol/deliver/` | 180 days | the 6-month client link requirement |
| `castrol/prototype/` | 7 days | spike scratch; nothing depends on it |
| `castrol/share/` | 14 days | ad-hoc review links |
| *(all)* | 7 days | abort incomplete multipart uploads, which otherwise bill silently |

Note `castrol/jobs/` is **not** expired. Those are the working artefacts —
source photo, audio, image edit, raw video — and they are what you need to
diagnose a complaint about a video that shipped five months ago. Expiring the
delivered copy must not destroy the evidence.

## 2. Confirm the bucket stays private

Nothing is public-read. Reads happen through CloudFront (delivery) or a
presigned URL (provider inputs). Verify that is still true:

```bash
aws s3api get-public-access-block --bucket castrol-video-pipeline
```

All four flags should be `true`.

## 3. Optional — let the pipeline manage its own lifecycle

Only if you want lifecycle changes in code rather than here. Adds
`s3:PutLifecycleConfiguration` and `s3:GetLifecycleConfiguration` on the bucket
to `castrol-local`. Weigh it against keeping bucket-configuration rights off a
long-lived access key; the conservative choice is to leave this undone and run
section 1 by hand.

## 4. Housekeeping

A stray health-check object exists because `castrol-local` has no
`s3:DeleteObject` — the pipeline can write and read but never delete, which is
the right posture for a media bucket (retention belongs to lifecycle rules, not
application code).

```bash
aws s3 rm s3://castrol-video-pipeline/castrol/healthcheck/ --recursive
```

## URL construction

The distribution has **no Origin Path**, so the full S3 key — including the
`castrol/` prefix from `S3_PREFIX` — must appear in the CDN URL:

```
https://d1dgdtphnngtpp.cloudfront.net/<full s3 key>
```

`https://d1dgdtphnngtpp.cloudfront.net/castrol/share/x.png` → 200
`https://d1dgdtphnngtpp.cloudfront.net/share/x.png`         → 403

Delivered videos go to `castrol/deliver/<uuid4>/video.mp4`. The uuid is the
whole security model, so it must never be derived from a phone number, a
`job_id`, or anything else guessable.
