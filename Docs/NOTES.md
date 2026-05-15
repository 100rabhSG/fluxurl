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

### What is "build context"?
Build context is the set of files/folders Docker sends to docker daemon during `docker build .`. Here `.` is the build context.

Docker can access only the files inside build context during build. Unnecessary files in context can increase image size (though not necessarily). Use `.dockerignore` to exclude unwanted files.

### On what factors does docker image size depend?
Docker image size mainly depend on these factors:
1. **Base image:** ubuntu, node, sdk, images are larger. Alpine, slim, distroless are smaller.
2. **Dependencies:** Installed packages (npm, NuGet, pip, OS packages). More dependencies = bigger image.
3. **Build artifact:** Source code, test files, logs, .git, temp file accidentally copied into image.
4. **Build context:** Large context slows build and may increase image size. (Only if we do broad copies like `COPY . .`)
5. Single-stage vs multi-stage build.
6. **Docker layers:** Each instruction creates a layer.

Key optimization techniques:
- Use smaller base images
- Use multi-stage builds
- Add .dockerignore
- Remove caches/temp files
- copy only required files


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
   - Multi-stage with `python:3.12-slim` runtime: ~300 MB
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

## AWS Basics

### IAM (Identity and Access Management)
When you create an AWS account, you get a default user - _root user_ - which is like God-mode for that account. It has unrestricted access to everything in that account. Obviously we don't want a developer/script/service running around with God-mode access. So AWS needs a system that can answer **Who the user is (Authentication)** and **Are they allowed to do this? (Authorization)**. That system is IAM.

The four key concepts:

1. **Users:** It represent a person that needs to interact with AWS. A user has credentials - either password for AWS console, or access keys for CLI/SDK. Each user is a permanent identity living inside your AWS account.

2. **Groups:** Groups are just convenience for managing users. Instead of giving same permissions to 20 devs, you create a "Developers" group, attach permissions there, and add all devs to that group. Groups are purely for organizing users.

3. **Roles:** A role is an identity without permanent credentials. Instead it is temporary credentials.

4. **Policies:** Policies are JSON docs (list of rules) that actually define what's allowed or denied. Policies are attached to users, roles, or groups - they don't do anything on their own. A simple policy looks like:

``` json
{
  "Effect": "Allow",
  "Action": "s3:GetObject",
  "Resource": "arn:aws:s3:::my-photos/*"
}
```

### EC2 (Elastic Compute Cloud)
EC2 is essentially Virtual Machine you rent by hour. Under the hood, EC2 instances are VM running on AWS's physical servers (called "hosts").

**AMI (Amazon Machine Image):** This is the boot template for your instance, basically a snapshot of an operating system and preinstalled softwares. (Ubuntu, Amazon Linux, custom, etc.)

**Instance type:** This is the hardware specs - how many vCPU, how much RAM, what kind of networking. (t3.micro, m5.large, c6i.4xlarge).

**EBS (Elastic Block Store):** virtual hard drives that exist independently of the instance. The disk persists even if the instance is stopped. You can detach it from one instance and attach it to another instance.

**Lifecycle states:**
- Running - on, charged for compute.
- Stopped - off, no compute charge, still pay for EBS disk.
- Terminated - deleted, gone forever.
- Reboot != stop/start (reboot stays on same host, stop/start may move it).

### VPC and Networking
VPC (Virtual Private Cloud) is your own private, isolated network inside AWS. Every EC2 instance, RDS database, Lambda function, etc lives inside a VPC. By default, nothing inside your VPC is reachable from the outside internet unless you explicitly allow it.

**Subnets:** A VPC is divided into smaller chunks called subnet. Each subnet live in exactly one AZ.
- Public subnet: resource here can have public IPs and can communicate with internet directly
- Private subnet: no direct internet access, used for db, internal services, anything that shouldn't be reachable from outside.

**Internet Gateway:** It is the virtual router attached to VPC that lets traffic flow between the VPC and the public internet. One per VPC. 
Without an IGW, VPC has no path to internet at all.

**Route table:** The rules that decide where traffic goes. Every subnet is associated with a route table. A subnet becomes "public" by having a route to IGW, "private" by not having one.

**NAT Gateway:** Needed when a resource in your private subnet needs to connect to internet, but you don't want internet to be able to reach your resource. A NAT gateway sites in public subnet and let's private-subnet resources make outbound connection to internet, while blocking inbound connection.

### Security groups
A security group is a virtual firewall attached to AWS resource that controls what network traffic is allowed in and out of that resource.

The core rules:
- **Default deny-inbound, default-allow outbound:** A newly created security group blocks all incoming traffic and allows all outgoing traffic. You add inbound rules to open specific ports.

- **Stateful:** If you allow inbound traffic on port X, the response is automatically allowed back out. You need not to write rules for ourbound traffic.

- **Rules are evaluated as union:** You can attach multiple security groups to one resource. The effective rules are the combination of all of them. If any rule across any attached group allows the traffic, it's allowed.

- **Why SSH from `My IP` only?:** SSH is administrative, it gives you shell access to the machine. Hence we restrict the access to smallest IP set possible i.e. my own IP.
The cost is that the rule needs to be updated whenever my IP changes.

### AWS regions
Any resource in one region doesn't exist in other regions. AWS resources are region-scoped, EC2 instances, security groups, Elastic IPs, snapshots, almost everything. A resource you created in ap-south-1 doesn't exist in us-east-1. If you can't find a resource you just created, check the region selector first.

## Deployment

### Q. Why do we need two separate docker-compose.yml files?


### Tags to docker images - two tags pointing to same image in our case


### EC2 reboot vs stop/start
The public IP of our EC2 instance doesn't change on a reboot, but if we stop/start then resources are released during stop and new resources are allocated at start. This may change our public IP. Change in public IP will also change our Base url which in turn invalidate all our previously generated short links because all of those depend on our public IP. To address this we can use elastic IPs.

### Why we run `docker compose up` in detached mode?
We run docker compose in detached mode so the container survive our SSH disconnect. Without detached mode (`-d`), the containers will be tied to foreground process. Close SSH -> you loose the app.


## Container Registry
A container registry is an HTTP server that stores and serves Docker images. You send HTTP request, it responds with image data.

### What actually a docker image is?
A docker image is not one file, it's a manifest + a set of layers.

```txt
Manifest (a JSON file describing the image)
├── Config (another JSON file with env vars, entrypoint, etc.)
└── Layers (zero or more tar files, each containing files for one Dockerfile step)
    ├── Layer 1: sha256:abc123... (base OS, ~50 MB)
    ├── Layer 2: sha256:def456... (Python install, ~30 MB)
    ├── Layer 3: sha256:789xyz... (dependencies, ~70 MB)
    ├── Layer 4: sha256:...      (your code, ~5 MB)
    └── Layer 5: sha256:...      (a couple of file copies, ~50 KB)
```

Each layer is a content addressed blob - its SHA256 hash is it's filename.

The manifest describers which layers, in what order, make up the image. It's how Docker knows _"to assemble fluxurl:latest, I need these 5 layers, applied in this order"_.

### What happens when you run `docker push fluxurl:latest`?
When you run `docker push fluxurl:latest`:
```txt
1. Docker CLI computes the manifest for fluxurl:latest
2. CLI asks the registry: "do you have layer sha256:abc123?"
   - Registry: yes → skip
   - Registry: no → CLI uploads it
3. Repeat for each layer
4. CLI uploads the manifest, tagged as 'latest'
```

If you push the same image twice, step 2 always returns "yes" for every layer. The second push is essentially free — a few API calls, no data transferred.

If you push a slightly-modified image (say, you changed one line of Python code, so layer 4 has a different hash), only layer 4 transfers. Layers 1–3 are unchanged; the registry already has them.

- **`docker pull` is `docker push` in reverse**

### What is Content-addressed storage
In traditional file storage system, when you store a file, you give it a name and a location. The address tells the filesystem where the file is, not what it is.

In content-addressed storage, you don't give file a name. Instead, it computed hash is its identity. For this reason the files in content-addressed storage are immutable.

- Two files with identical content have the same hash → stored once, automatic deduplication.
- You cannot modify a file in place. Changing any byte changes the hash → it becomes a different file with a different address. The original is unchanged. Immutability for free.
- The address is a function of the content. You cannot have "the same address with different content over time." Stable references.
- Anyone can verify integrity: download the file, hash it, compare to the address you used. Built-in integrity checking.

### Why layers at all? Why not just store images as monolithic tar files?
Layers map to dockerfile steps, and most of the steps don't change between builds.

Consider fluxurl dockerfile for example:
```txt
FROM python:3.12-slim          # Layer 1: ~50 MB, almost never changes
COPY requirements.txt /tmp/    # Layer 2: changes when deps change
RUN pip install -r /tmp/req... # Layer 3: ~70 MB, changes when deps change
COPY app /app/                 # Layer 4: changes on every code edit
```
If we just change one line in our app, layers 1, 2, 3 are identical to previous build. Only layer 4 is new. Builds are fast (because of docker's build cache), and pushes are fast (because only layer 4 need to be uploaded).

If the image was monolith tar file, every code change would mean uploading the whole image every time. Layers reduce that to uploading only changed layers.

### What are manifests?
Manifest is like recipe - the list of which layers, in what order. More formally, manifest is metadata that describes a container image, what layers it contains, what configuration it uses, and sometimes platform variants too.

A manifest typically contains - Image name/tag, Digest (immutable hash), Layers digest, size of layers, OS/architecture, config object reference.

## AWS Authentication

### Building blocks
1. **Principal:** A principal is 'an identity that can make request to AWS'. There are three kinds of principal - root user, IAM user, IAM role.
Think of principal as "a hat". The hat doesn't move on its own. something has to be wearing the hat to do anything. Different hats have different powers.

2. **Credentials:** Credentials are how principal proves it's the principal it claims to be when making a request. There are two kinds of credentials:

| Feature            | Long-Lived Credentials            | Temporary Credentials                             |
| ------------------ | --------------------------------- | ------------------------------------------------- |
| Expiration         | Do not expire automatically       | Expire after a short time                         |
| Components         | Access Key ID + Secret Access Key | Access Key ID + Secret Access Key + Session Token |
| Issued By          | IAM User                          | AWS STS                                           |
| Security           | Less secure if leaked             | Safer because they expire                         |
| Rotation           | Manual rotation needed            | Automatically rotated in many cases               |
| Common Use         | Users, external apps, scripts     | IAM Roles, EC2, Lambda                            |
| AWS Recommendation | Avoid when possible               | Preferred by AWS                                  |

3. **Policies:** A policy is a JSON document that says what's allowed or denied.
Policies are heart of authorization. They specify three things:
  - Effect: Allow or deny
  - Action: What API calls
  - Resources: Which AWS resources
  - Some policies also include Condition, Principal.

There are two types of Policies:

i) Identity based: attached to a principal (user, role, or group). They answer "What is this principal allowed to do?".
ii) Resources based: attached to a resource (S3 bucket, ECR repo). They answer "Who is allowed to do what to this resource?".

### The mental model
```txt
A "principal" (a user, a role, or a service) makes a request
        ↓
The request includes "credentials" (access key + secret key, or temporary session token)
        ↓
AWS receives the request, looks up the principal
        ↓
AWS checks the policies attached to the principal: "is this principal allowed to do this action on this resource?"
        ↓
Allow → request proceeds
Deny → 403
```

### Auth flow for ECR in our project
We already have credentials set in our CLI through access_key + secret_key. Now we request temporary docker password from AWS for ECR. This temporary password is used by Docker on our laptop to push images to ECR.
On EC2 side, we'll attach an IAM role that gives instance permission to talk to ECR. A new temporary docker password is generated here which will be used by docker on our EC2 instance to pull images from ECR.