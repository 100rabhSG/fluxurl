# Fluxurl — Concepts Log

Running log of things I learned while building Fluxurl, written in my own words.

## Phase 0: AWS account basics

### What's the difference between AWS root user and IAM user?
The difference is mainly about ownership and access control.
The root user is created automatically when creating AWS account and has unrestricted access to everything. 
IAM (Identity and Access Management) users get only permissions that are assigned/allowed to them. Use these for everyday tasks and use root user only for rare account-level tasks.

### Why never use root for daily work?
Root user have unrestricted access to everything, so any mistake or credential leak can compromise entire AWS account. If someone gets root user access, they effectively own the AWS account.

### What does `aws configure` actually do — where does it store credentials?
`aws configure` stores your AWS credentials and default settings locally on your system so AWS CLI can authenticate requests automatically.
Credentials are stored at `~/.aws/` (or `C:\Users\<you>\.aws\` on Windows)


## Phase 0.5: HLD Sketch


### Back of the envelope estimation
A quick rough calculation to estimate scale, feasibility, or performance without detailed analysis. The numbers don't have to be right — the *reasoning* has to hold up.

Three ways to anchor:

**1. Compare to a known product.** Roughly place the system on a ladder:
```
- Hobby project nobody uses: ~10 URLs/day
- Small startup: ~100–10k/day
- "Imagine it caught on": 10k URLs/day, 1M redirects/day
- bit.ly: 100M+ redirects/day
```

Pick the tier that fits the framing.

**2. Top-down from users.** Start from total users, multiply down through ratios.
```
1M users → 5% DAU = 50k → each user makes 1 URL per 10 days = 5k/day
50k DAU × 2 redirects each = 100k redirects/day
Read:write ratio = 20:1. The system is read-heavy.
```

**3. Bottom-up from one user.** Start with "what would I do?" Multiply by user count. Useful when we don't have a user count to start from.

#### Some ratios that come up a lot:
- DAU/MAU: 10–20% for casual tools, 50%+ for messaging
- Active users from registered: 10–30%
- Read:write for URL shorteners: 20:1 to 100:1
- Storage per row: ~500 bytes is a safe rough number


### Throughput analysis
It is easy to compute numbers and then ignore them. The whole point of doing the math is to ask 'so what?'.

Pattern to remember:
1. Compute average rate per second.
2. Multiply by a peak factor (2–10×).
3. Compare to what one small box can do.
4. State what I don't need.

For fluxurl:

```
5k writes/day  ÷ 86,400 = 0.06 writes/sec  (1 every 17 sec)
100k reads/day ÷ 86,400 = 1.2 reads/sec
Peak (10×) = 12 reads/sec, 0.6 writes/sec
```

**so what?** One t3.micro plus one postgres can handle this with tons of margin. No sharding, no read replicas, no caching for throughput reasons.

The mistake I want to avoid: computing throughput numbers and then designing a sharded, queued, replicated mess anyway because that's what "real systems" look like. If the numbers don't justify it, don't build it.


### Latency and it's reference points: 
Don't pick out latency numbers randomly. Ask "How much time will user comfortably wait for completion of task."

How humans perceive latency (worth memorising):

| Latency      | What it feels like                          |
|--------------|---------------------------------------------|
| < 100ms      | Instant. User doesn't notice.               |
| 100–300ms    | Responsive. Visible but fine.               |
| 300ms–1s     | Noticeable lag. User starts feeling it.     |
| 1–3s         | Slow. User wonders if it's broken.          |
| > 3s         | Broken. ~40% give up.                       |


### HTTP 301 vs 302
Both are HTTP redirect status codes. From the user's immediate experience, they're identical, the only difference is caching. 

**301:** Browser cache the mapping, subsequent clicks on same short url bypass querying our server.

**302:** No caching, every click hits the server. More load on server, but you see every click.