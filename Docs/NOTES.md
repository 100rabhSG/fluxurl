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