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

**301:** Browser caches the mapping, subsequent clicks on same short url bypass querying our server.

**302:** No caching, every click hits the server. More load on server, but you see every click.

Picking 301 will silently break click analytics because clicks will never reach our servers.


## Python basics

### What does `@app.get("/users")` do?
This decorator tells fast API:

``` Register the below function as handler for HTTP GET request on /users ```

Internally, it's similar to:
```
def get_users():
    return ["Alice", "Bob"]

app.get("/users")(get_users)
```

### What is databse migration?
Any application's database schema may change over it's lifetime - tables, columns, constraints, index. Now if you apply all these changes manually across all your environments, it'll get messy and there are high chances of mistakes.

A migration is a single, named, ordered schema change, consisting of two halves:
- "Going forward" - what to do (e.g., create url table)
- "Going backward" - how to undo it (e.g., drop the url table)

A migration is a file, committed to your repo, that captures one such specific change. It looks roughly like:
```python
# 0001_create_urls_table.py

def upgrade():
    op.create_table(
        'urls',
        Column('short_code', String(7), primary_key=True),
        Column('long_url', Text, nullable=False),
        Column('created_at', TIMESTAMP, nullable=False),
    )

def downgrade():
    op.drop_table('urls')
```


### Why do we need Alembic?
Alembic is migration tool for SQLAlchemy. It does three jobs:
- Generate migration files for you (`alembic revision --autogenerate`)
- Apply migrations (`alembic upgrade head` / `alembic downgrade -1`)
- Track which migrations have been applied (`alembic_version`)

### Pydantic models vs ORM Models
Two distinct types of model class even though both are called "model"
- Pydantic models = data contracts at the API boundary
- SQLAlchemy ORM models = db row mappings

| Aspect            | SQLAlchemy Url                | Pydantic ShortenRequest/Response |
|------------------|------------------------------|----------------------------------|
| Lives in         | app/models/url.py            | app/schemas/url.py               |
| Represents       | A row in the urls table      | A JSON body crossing the API     |
| Used for         | DB queries, persistence      | Request validation, response serialization |
| .NET analogy     | EF entity                    | DTO / record                     |


### Optimistic conflict handling vs check-then-insert 
_Check then insert_ i.e. query first, then insert if not found, is unsafe for concurrent systems. If two request, both query at the same time will see record not found so both will try to insert, and the later one will fail.

The correct approach is to enforce uniqueness at database level and use optimistic conflict handling. 
- Attempt the insert directly
- Let the database reject duplicates
- Catch the constraint error

### Route matching order
In `main.py` if we had put `app.include_router(urls_router.router)` before all other routes, the other routes will never be hit.

Unlike ASP.NET routing (which scores route by specificity), FastAPI just iterates routes in the order they were added and picks the first match. 

A catch-all registered first will swallow `/healthz` even though /healthz is more specific. Mount catch-all at last.


## Docker basics

### Image vs Container
An image is a blueprint, while a container is a running instance created from that blueprint. (similar to class and object).

### docker compose down vs down -v
| Command | What it does | When |
|---|---|---|
| `docker compose down` | Removes containers + network. Keeps volumes. | Daily — safe |
| `docker compose down -v` | Also removes volumes. Wipes data. | Only when you want a clean slate |
| `docker compose stop` | Pauses/stops containers (keeps everything) | If you'll restart soon |

### What does "build" mean in python vs compiled languages (C++, Go, C#, Java)
For compiled languages like C++, Rust, Go, "build" means turning source code into compiled binary.

For pure python, build doesn't produce binary. At build dependencies are installed, and runtime environment is prepared. We next stage we ship this prepared environment plus our source code - without the tools that did installing.

### What is a multistage docker file?
A normal docker file has one `FROM` instruction and everything happens in that one base image - install dependencies, copy code, run the app.

A multistage docker file has multiple `FROM` instructions, each starting a new stage. Stages can copy files from previous stages, but anything that is not copied is discarded.

```
Stage 1: builder
  - Heavy base image with all the build tools
  - Install dependencies, compile what needs compiling
  - Produces artifacts (installed packages, binaries, .so files, etc.)

Stage 2: runtime
  - Minimal base image (no build tools)
  - Copy only the artifacts from Stage 1
  - Run the app

Final shipped image = only Stage 2.
Stage 1 is discarded from the image but cached on the build machine.
```

### Why multistage even matters?
1. **Image size.** Typical Python app:
   - Single-stage on `python:3.12`: ~1.2 GB
   - Multi-stage with `python:3.12-slim` runtime: ~150 MB
2. **Security surface area.** Every package in the runtime image is potential attack surface. Smaller image = fewer CVEs to track.
3. **Build cache efficiency.** Multi-stage encourages structuring the Dockerfile so dependency installs are cached separately from code changes.
4. **Separation of concerns.** Each stage has a clear single role.

### Where does build and runtime actually happen?
This is the biggest thing that makes multistage worthwhile. The same docker file defines both stages, but each stage has different role and different destination.

| Stage | Where it runs | Lives forever on | What it produces |
|---|---|---|---|
| Builder | Build machine (laptop / GitHub Actions runner) | Build machine cache | Installed dependencies, compiled `.so` files |
| Runtime | Build machine briefly (during `docker build`), then EC2 | ECR (pushed image), EC2 (local copy after pull) | The small image that gets pushed to ECR |
 
The build machine is the *only* place that ever needs gcc, pip, or build headers. Those tools never leave the build machine — they're used during the builder stage and then *not copied* into the runtime stage. EC2 never sees them.