# NZT-48 Security & Secrets Specification

| Field          | Value                       |
|----------------|-----------------------------|
| Document ID    | NZT48-ANNEX-SSS-001         |
| Version        | 1.0                         |
| Date           | 2026-02-27                  |
| Status         | **BINDING**                 |
| Classification | Internal / Security         |
| Review Cadence | Quarterly or after any security incident |
| Owner          | System Operator              |

---

## 1. PURPOSE

This document defines the security posture, secrets management policy, access control model, redaction requirements, and hardening roadmap for the NZT-48 Leveraged ISA Intraday Trading System across all operational phases.

### 1.1 Scope

Every rule in this document applies to:

- The EC2 instance (54.242.32.11) and all processes running on it.
- All Docker containers (`nzt48`, `nzt48-dashboard`).
- All configuration files, environment files, and runtime state.
- All network communication (API, dashboard, Telegram, SSH).
- All output channels (PDFs, Telegram messages, logs, dashboard UI).
- All operator actions, whether manual or automated.

### 1.2 Phased Security Model

Security requirements are graduated across three operational phases. Each phase inherits all requirements from previous phases.

| Phase          | Description                         | Gate Requirement                              |
|----------------|-------------------------------------|-----------------------------------------------|
| **PAPER**      | Paper trading, no real capital      | Current state. Baseline security sufficient.  |
| **LIMITED LIVE** | Live trading, restricted capital  | All LIMITED LIVE requirements satisfied.       |
| **FULL LIVE**  | Full-scale live trading             | All FULL LIVE requirements satisfied.          |

**Promotion Rule**: The system **MUST NOT** advance to a subsequent phase until every requirement for that phase has been satisfied and verified via the acceptance tests in Section 10. No exceptions.

---

## 2. SECRETS INVENTORY

All secrets used by the system, their purpose, classification, and rotation requirements.

| Secret                       | Environment Variable        | Purpose                          | Classification | Rotation  |
|------------------------------|-----------------------------|----------------------------------|----------------|-----------|
| Polygon API Key              | `NZT48_POLYGON_API_KEY`     | Market data (equities, options)  | DATA-READ      | Annual    |
| Finnhub API Key              | `NZT48_FINNHUB_API_KEY`     | Market data (quotes, news)       | DATA-READ      | Annual    |
| Alpha Vantage API Key        | `NZT48_ALPHA_VANTAGE_KEY`   | Market data (time series)        | DATA-READ      | Annual    |
| Twelve Data API Key          | `NZT48_TWELVE_DATA_KEY`     | Market data (real-time, history) | DATA-READ      | Annual    |
| Financial Modeling Prep Key  | `NZT48_FMP_KEY`             | Fundamental data, screening     | DATA-READ      | Annual    |
| NewsAPI Key                  | `NZT48_NEWSAPI_KEY`         | News sentiment data              | DATA-READ      | Annual    |
| FRED API Key                 | `NZT48_FRED_KEY`            | Federal Reserve economic data    | DATA-READ      | Annual    |
| Telegram Bot Token           | `NZT48_TELEGRAM_TOKEN`      | Bot authentication               | CONTROL        | 90 days   |
| Telegram Chat ID             | `NZT48_TELEGRAM_CHAT_ID`    | Message routing                  | CONTROL        | On change |
| SSH Private Key              | `~/.ssh/nzt48-key.pem`      | EC2 instance access              | INFRA-ADMIN    | Annual    |
| IBKR API Credentials (future)| `NZT48_IBKR_*`             | Live brokerage execution         | TRADE-EXEC     | 90 days   |

### 2.1 Classification Levels

| Level        | Definition                                                         | Compromise Impact                    |
|--------------|--------------------------------------------------------------------|--------------------------------------|
| DATA-READ    | Read-only access to third-party data APIs                          | Rate-limit abuse, billing exposure   |
| CONTROL      | Ability to send commands or messages on behalf of the system       | Unauthorized signal delivery, kill switch manipulation |
| INFRA-ADMIN  | Administrative access to compute infrastructure                   | Full system compromise               |
| TRADE-EXEC   | Ability to execute financial transactions                          | Direct financial loss                |

### 2.2 Compromise Severity

Any compromise of a TRADE-EXEC or INFRA-ADMIN secret is a **Critical** incident requiring immediate response per Section 8. Compromise of a CONTROL secret is **High**. Compromise of a DATA-READ secret is **Medium**.

---

## 3. SECRETS MANAGEMENT

### 3.1 Current State (PAPER -- Acceptable)

| Aspect               | Implementation                                                   |
|-----------------------|------------------------------------------------------------------|
| Storage               | Environment variables in Docker container runtime               |
| Injection             | `docker-compose.yml` `environment:` section referencing `.env` file |
| Source control         | `.env` excluded via `.gitignore`                                |
| Encryption at rest    | None (plaintext `.env` on EC2 filesystem)                       |
| Rotation mechanism    | Manual (edit `.env`, restart container)                          |
| Audit trail           | None                                                             |

**Assessment**: Acceptable for PAPER mode. Secrets are not committed to git and are isolated within the Docker runtime. The primary risk is plaintext storage on the EC2 filesystem, which is mitigated by SSH-key-only access.

### 3.2 Target State (LIMITED LIVE -- Required Before Live Trading)

Three options evaluated. **Option A is the recommended default.**

#### Option A: AWS Secrets Manager (RECOMMENDED)

| Aspect               | Implementation                                                   |
|-----------------------|------------------------------------------------------------------|
| Storage               | AWS Secrets Manager (encrypted at rest with KMS)                |
| Injection             | Container entrypoint script fetches secrets at startup           |
| Source control         | No secrets in repository                                        |
| Encryption at rest    | AES-256 via AWS KMS                                             |
| Rotation mechanism    | Secrets Manager automatic rotation (configurable)               |
| Audit trail           | CloudTrail logs every secret access                             |
| Cost                  | ~$0.40/secret/month + $0.05 per 10,000 API calls               |

**Rationale**: Native to EC2. Provides encryption, access logging, automatic rotation, and IAM-based access control with no additional infrastructure.

#### Option B: HashiCorp Vault

| Assessment | Overkill for single-instance architecture. Introduces operational complexity (Vault server, unseal process, HA requirements) without proportional benefit. **Not recommended.** |
|---|---|

#### Option C: Encrypted `.env` with age/sops

| Assessment | Simple and effective. Encrypts `.env` at rest using `age` keys. Decryption at container startup. No audit trail. No automatic rotation. **Acceptable fallback if AWS Secrets Manager is not viable.** |
|---|---|

### 3.3 Secrets Management Rules (ALL PHASES)

These rules are **NON-NEGOTIABLE** and apply from PAPER mode onward.

| ID     | Rule                                                                                              | Enforcement      |
|--------|---------------------------------------------------------------------------------------------------|-------------------|
| SM-01  | **NEVER** commit secrets to git. No exceptions.                                                   | Pre-commit hook   |
| SM-02  | **NEVER** log secrets in plaintext. All log output must pass through the redaction filter.        | Code review       |
| SM-03  | **NEVER** include secrets in Telegram messages, PDF reports, or dashboard responses.             | Output filter     |
| SM-04  | **NEVER** expose secrets via API endpoints (including health, debug, or diagnostic endpoints).    | Code review       |
| SM-05  | **NEVER** pass secrets as command-line arguments (visible in `ps` output).                        | Dockerfile review |
| SM-06  | Rotate Telegram bot token every 90 days.                                                          | Calendar reminder |
| SM-07  | Rotate all data API keys annually, or immediately upon suspected compromise.                      | Calendar reminder |
| SM-08  | Rotate SSH key annually, or immediately upon suspected compromise.                                | Calendar reminder |
| SM-09  | IBKR credentials (when introduced) must use separate paper and live credential sets.              | Configuration     |
| SM-10  | `.env` file permissions must be `600` (owner read/write only).                                    | Deploy script     |

### 3.4 Pre-Commit Hook Specification

A git pre-commit hook **MUST** be installed that scans staged files for patterns matching secrets. The hook must reject commits containing any of the following patterns:

```
# Patterns to detect (case-insensitive)
[A-Za-z0-9_]{20,}          # Long alphanumeric strings in assignment context
NZT48_.*_KEY=.+            # Direct key assignment
NZT48_.*_TOKEN=.+          # Direct token assignment
TELEGRAM_TOKEN=.+          # Telegram token assignment
api[_-]?key\s*[:=]\s*['\"]?[A-Za-z0-9]  # Generic API key patterns
```

The hook must **exclude** `.env.example` (which contains placeholder values only) and documentation files referencing variable names without values.

---

## 4. NETWORK SECURITY

### 4.1 Current State (PAPER)

| Component         | Port  | Protocol | Authentication | CORS        | Exposure        |
|-------------------|-------|----------|----------------|-------------|-----------------|
| FastAPI Engine    | 8000  | HTTP     | None           | Wildcard (*) | Public (EC2 SG) |
| Next.js Dashboard | 3001  | HTTP     | None           | N/A         | Public (EC2 SG) |
| SSH               | 22    | SSH      | Key-based      | N/A         | Public (EC2 SG) |

**Risk Assessment**: Unauthenticated HTTP endpoints are acceptable during paper trading because no real capital is at risk and no sensitive data is served. However, the kill switch endpoint accessible without authentication represents a denial-of-service risk to paper trading operations.

### 4.2 Target State (LIMITED LIVE)

| Requirement ID | Requirement                                                      | Implementation                                          |
|----------------|------------------------------------------------------------------|---------------------------------------------------------|
| NET-L01        | API authentication on all endpoints                              | API key in `X-API-Key` header, validated by middleware  |
| NET-L02        | Dashboard access control                                         | Basic auth or IP whitelist via nginx                    |
| NET-L03        | CORS restricted to known origins                                 | Explicit origin list in FastAPI CORS middleware         |
| NET-L04        | Security group restricts 8000/3001 to operator IP(s)            | AWS EC2 security group update                           |
| NET-L05        | HTTPS via reverse proxy                                          | nginx + Let's Encrypt (recommended) or self-signed     |
| NET-L06        | Kill switch API requires authentication                          | API key validation before processing kill commands      |

#### 4.2.1 API Authentication Specification (NET-L01)

```
Header:    X-API-Key: <api-key-value>
Storage:   API key stored alongside other secrets (Secrets Manager or .env)
Env Var:   NZT48_API_AUTH_KEY
Length:    Minimum 32 characters, cryptographically random
Rotation:  Every 90 days
Failure:   401 Unauthorized (no body, no detail leakage)
Exempt:    GET /api/health (returns only {"status": "healthy"}, no system details)
```

#### 4.2.2 nginx Reverse Proxy Configuration (NET-L05)

```
Internet --> :443 (nginx, TLS termination)
                |
                +--> :8000 (FastAPI, localhost only)
                +--> :3001 (Dashboard, localhost only)
```

After nginx is deployed, ports 8000 and 3001 **MUST** be bound to `127.0.0.1` only (not `0.0.0.0`). The security group must close inbound access to 8000 and 3001, leaving only 443 and 22.

### 4.3 Target State (FULL LIVE)

| Requirement ID | Requirement                                                      | Implementation                                          |
|----------------|------------------------------------------------------------------|---------------------------------------------------------|
| NET-F01        | TLS 1.2+ mandatory on all HTTP endpoints                        | nginx configuration, disable TLS 1.0/1.1               |
| NET-F02        | JWT-based API authentication                                     | Short-lived tokens, signed with RS256                   |
| NET-F03        | Dashboard behind VPN or SSO                                      | WireGuard VPN or OAuth2 proxy                           |
| NET-F04        | SSH via bastion host only                                        | Security group restricts port 22 to bastion IP          |
| NET-F05        | WAF for public-facing endpoints                                  | AWS WAF or ModSecurity                                  |
| NET-F06        | Security group: minimal surface                                  | 22 from bastion, 443 from operator IPs, all else denied |
| NET-F07        | Rate limiting on all API endpoints                               | nginx `limit_req_zone` or FastAPI middleware             |

---

## 5. ACCESS CONTROL

### 5.1 Access Control Matrix

| Resource               | PAPER                    | LIMITED LIVE                     | FULL LIVE                          |
|------------------------|--------------------------|----------------------------------|------------------------------------|
| EC2 SSH                | Single key, single operator | Single key, single operator    | Bastion host, key per operator     |
| FastAPI API            | Unauthenticated          | API key (X-API-Key header)       | JWT with expiry                    |
| Dashboard              | Unauthenticated          | Basic auth or IP whitelist       | VPN or SSO                         |
| Kill Switch (Telegram) | Chat ID restriction      | Chat ID restriction              | Chat ID + confirmation code        |
| Kill Switch (API)      | Unauthenticated          | API key required                 | JWT + 2FA confirmation             |
| Kill Switch (File)     | File presence on disk    | File presence on disk            | File presence + integrity check    |
| Docker exec            | Avoid (per DDG-001)      | Prohibited except emergency      | Prohibited except emergency        |
| SQLite Database        | Filesystem access        | Filesystem access (encrypted)    | Filesystem access (encrypted)      |
| Log Files              | Filesystem access        | Filesystem access                | Centralized logging (CloudWatch)   |

### 5.2 Kill Switch Security

The kill switch is a critical safety mechanism. Unauthorized activation halts trading and causes missed opportunities. Unauthorized deactivation (preventing a legitimate kill) could allow runaway losses.

| Kill Switch Vector | Authentication (PAPER) | Authentication (LIMITED LIVE) | Authentication (FULL LIVE) |
|--------------------|----------------------|------------------------------|---------------------------|
| Telegram           | `chat_id` match      | `chat_id` match              | `chat_id` + confirmation code |
| API                | None                 | API key                      | JWT + operator confirmation   |
| File               | File presence        | File presence                | File + HMAC integrity         |

**Rule**: From LIMITED LIVE onward, no kill switch activation or deactivation path may be unauthenticated.

### 5.3 Docker Security

| Requirement ID | Requirement                                                          | Phase           |
|----------------|----------------------------------------------------------------------|-----------------|
| ACC-D01        | Container runs as non-root user                                      | ALL             |
| ACC-D02        | No `--privileged` flag on container                                  | ALL             |
| ACC-D03        | Read-only root filesystem where possible (`--read-only`)             | LIMITED LIVE    |
| ACC-D04        | No `docker exec` in normal operations (per EC2_DOCKER_DRIFT_GUARDS) | ALL             |
| ACC-D05        | Base image pinned to specific digest (not `latest` tag)              | LIMITED LIVE    |
| ACC-D06        | Image scanned for known vulnerabilities before deployment            | FULL LIVE       |

---

## 6. REDACTION POLICY

### 6.1 Redaction Rules

All system output channels **MUST** apply the following redaction rules before emitting any content.

| Data Type                | Redaction Format       | Example                                    |
|--------------------------|------------------------|--------------------------------------------|
| API keys (any provider)  | `****` + last 4 chars  | `pk_abc123xyz789` becomes `****z789`       |
| Telegram bot token       | `****` + last 4 chars  | `1234567:AAH...xyz` becomes `****_xyz`     |
| Telegram chat ID         | Full redaction         | `-1001234567` becomes `[REDACTED]`          |
| SSH key path             | Filename only          | `/Users/rr/.ssh/nzt48-key.pem` becomes `nzt48-key.pem` |
| Internal IP addresses    | Full redaction         | `54.242.32.11` becomes `[INTERNAL-IP]`      |
| File system paths        | Relative only          | `/home/ubuntu/nzt48-signals/data/` becomes `data/` |
| HTTP headers (full)      | Omit auth headers      | `Authorization: Bearer ...` never logged    |
| IBKR credentials (future)| Full redaction         | Always `[REDACTED]`                         |

### 6.2 Redaction Enforcement Points

| Output Channel     | Enforcement Mechanism                                              |
|--------------------|--------------------------------------------------------------------|
| Application logs   | Logging formatter with regex-based redaction filter                |
| Telegram messages  | Output sanitizer in Telegram delivery module                      |
| PDF reports        | Template rendering must never bind secret variables               |
| Dashboard API      | Response serializer excludes secret fields                        |
| Error responses    | Generic error messages; no stack traces to external consumers     |
| Health endpoint    | Returns only `{"status": "healthy"}` or `{"status": "unhealthy"}`|

### 6.3 Redaction Filter Implementation

The redaction filter **MUST** be implemented as a centralized module (e.g., `utils/redaction.py`) and applied via Python's `logging.Filter` class. It must match against all known secret environment variable patterns and replace values before log records are emitted.

```
Patterns to redact (applied to all log output):
- Environment variable values matching NZT48_*_KEY, NZT48_*_TOKEN
- Any string matching known API key formats (length > 16, alphanumeric)
- IP addresses in RFC 1918 ranges and the EC2 public IP
- Full filesystem paths (replace with basename or relative path)
```

---

## 7. LEAST PRIVILEGE

### 7.1 Principle

Every component of the system must operate with the minimum permissions required to perform its function. No component should have access to resources or capabilities beyond its operational need.

### 7.2 Privilege Matrix

| Component              | Required Permissions                            | Prohibited Permissions                  |
|------------------------|-------------------------------------------------|-----------------------------------------|
| Docker container       | Non-root user, write to `data/` directory only  | Root access, host network, privileged   |
| Data API keys          | Read-only market data                           | Write, delete, account management       |
| Telegram bot           | Send messages to single chat ID                 | Broadcast, group management, admin      |
| EC2 IAM role           | Secrets Manager read (if Option A)              | S3 write, EC2 management, IAM changes   |
| FastAPI process        | Bind port 8000, read config, write logs/data    | System calls, network scanning          |
| Dashboard process      | Bind port 3001, read API                        | Write to any backend resource           |
| IBKR connection (future)| Place/cancel orders, read positions             | Withdraw funds, change account settings |

### 7.3 API Key Privilege Audit

| Provider        | Key Type Available | Required Capability | Privilege Level |
|-----------------|-------------------|---------------------|-----------------|
| Polygon         | Read-only          | Market data queries | Correct         |
| Finnhub         | Read-only          | Quotes, news        | Correct         |
| Alpha Vantage   | Read-only          | Time series         | Correct         |
| Twelve Data      | Read-only          | Real-time, history  | Correct         |
| FMP             | Read-only          | Fundamentals        | Correct         |
| NewsAPI         | Read-only          | News articles       | Correct         |
| FRED            | Read-only          | Economic data       | Correct         |
| Telegram        | Bot (send only)    | Message delivery    | Verify: ensure bot cannot read arbitrary chats |

All data API keys are inherently read-only by provider design. No action required. Telegram bot permissions should be verified via BotFather to confirm the bot is not a group admin and cannot read messages from other chats.

---

## 8. INCIDENT RESPONSE (SECURITY)

### 8.1 Incident Classification

| Severity     | Definition                                                          | Response Time |
|--------------|---------------------------------------------------------------------|---------------|
| **Critical** | INFRA-ADMIN or TRADE-EXEC secret compromised; EC2 intrusion        | Immediate     |
| **High**     | CONTROL secret compromised; unauthorized kill switch activation    | < 1 hour      |
| **Medium**   | DATA-READ secret compromised; unexpected API rate limiting          | < 24 hours    |
| **Low**      | Security misconfiguration detected; policy violation without breach | < 72 hours    |

### 8.2 Response Procedures

#### 8.2.1 Critical: EC2 Intrusion Detected

| Step | Action                                                                 |
|------|------------------------------------------------------------------------|
| 1    | Activate kill switch via Telegram (halt all trading immediately)       |
| 2    | Revoke EC2 security group: deny all inbound except operator IP on 22  |
| 3    | Rotate ALL secrets (every API key, Telegram token, SSH key)            |
| 4    | Preserve evidence: snapshot EC2 EBS volume before any changes          |
| 5    | Terminate compromised instance                                         |
| 6    | Launch replacement from clean AMI                                      |
| 7    | Deploy from known-good git commit                                      |
| 8    | Inject rotated secrets into new environment                            |
| 9    | Verify system health and resume operations                             |
| 10   | Post-incident review within 48 hours                                   |

#### 8.2.2 High: Telegram Token Leaked

| Step | Action                                                                 |
|------|------------------------------------------------------------------------|
| 1    | Revoke token immediately via BotFather (`/revoke`)                     |
| 2    | Generate new token via BotFather (`/newbot` or `/token`)               |
| 3    | Update `NZT48_TELEGRAM_TOKEN` in secret store                         |
| 4    | Restart container to pick up new token                                 |
| 5    | Verify Telegram delivery with test message                             |
| 6    | Audit recent Telegram activity for unauthorized messages               |

#### 8.2.3 Medium: API Key Abuse Suspected

| Step | Action                                                                 |
|------|------------------------------------------------------------------------|
| 1    | Check provider dashboard for unexpected usage patterns                 |
| 2    | Rotate the suspected key at the provider                               |
| 3    | Update environment variable with new key                               |
| 4    | Restart container                                                      |
| 5    | Monitor for continued anomalous activity                               |
| 6    | If abuse continues, investigate EC2 for compromise (escalate to Critical) |

#### 8.2.4 Critical: IBKR Credentials Compromised (Future)

| Step | Action                                                                 |
|------|------------------------------------------------------------------------|
| 1    | Activate kill switch immediately                                       |
| 2    | Log in to IBKR web portal manually and disable API access              |
| 3    | Change IBKR password and regenerate API credentials                    |
| 4    | Review all recent orders and positions for unauthorized activity       |
| 5    | Contact IBKR support if unauthorized trades detected                   |
| 6    | Rotate all other secrets (assume full compromise)                      |
| 7    | Full post-incident review                                              |

---

## 9. AUDIT & COMPLIANCE

### 9.1 Audit Log Requirements

| Event Type                 | Log Source                    | Retention | Phase Required |
|----------------------------|-------------------------------|-----------|----------------|
| SSH login/logout           | `/var/log/auth.log`          | 90 days   | ALL            |
| API requests               | FastAPI middleware            | 30 days   | ALL            |
| Kill switch activation     | Application log + Telegram   | Permanent | ALL            |
| Secret access              | AWS CloudTrail (if Option A) | 90 days   | LIMITED LIVE   |
| Docker container start/stop| Docker daemon log            | 30 days   | ALL            |
| Configuration changes      | Git commit log               | Permanent | ALL            |
| Failed authentication      | FastAPI middleware            | 90 days   | LIMITED LIVE   |
| Security group changes     | AWS CloudTrail               | 90 days   | LIMITED LIVE   |

### 9.2 Monitoring Alerts

| Alert Condition                              | Channel   | Phase Required |
|----------------------------------------------|-----------|----------------|
| SSH login from unknown IP                    | Telegram  | LIMITED LIVE   |
| 5+ failed API authentication attempts in 1m | Telegram  | LIMITED LIVE   |
| API key rate limit exceeded unexpectedly     | Telegram  | ALL            |
| Container restart (unexpected)               | Telegram  | ALL            |
| Security group modification                  | Telegram  | FULL LIVE      |

### 9.3 Periodic Reviews

| Review                                  | Frequency  | Phase Required |
|-----------------------------------------|------------|----------------|
| Secret rotation compliance check        | Monthly    | ALL            |
| Security group rule audit               | Monthly    | LIMITED LIVE   |
| Docker image vulnerability scan         | Monthly    | FULL LIVE      |
| Full security posture review            | Quarterly  | ALL            |
| Penetration test (self-assessment)      | Annually   | FULL LIVE      |

---

## 10. ACCEPTANCE TESTS

Each test must pass before the system may advance to the indicated phase. Tests are cumulative: FULL LIVE requires all PAPER and LIMITED LIVE tests to remain passing.

### 10.1 PAPER Phase Tests

| Test ID  | Description                                                    | Procedure                                                                                          | Pass Criteria                     |
|----------|----------------------------------------------------------------|----------------------------------------------------------------------------------------------------|-----------------------------------|
| SSS-T01  | No hardcoded secrets in codebase                               | `grep -rn 'NZT48_.*_KEY=\|NZT48_.*_TOKEN=' --include='*.py' --include='*.yaml' --include='*.yml'` | Zero matches                      |
| SSS-T02  | `.env` file excluded from git                                  | `git ls-files .env` returns empty; `.gitignore` contains `.env`                                    | Verified                          |
| SSS-T03  | API keys redacted in logs                                      | Trigger log entries involving API calls; grep logs for known key prefixes                           | Only `****XXXX` format found      |
| SSS-T04  | Docker container runs as non-root                              | `docker exec nzt48 whoami`                                                                         | Output is not `root`              |

### 10.2 LIMITED LIVE Phase Tests

| Test ID  | Description                                                    | Procedure                                                                                          | Pass Criteria                     |
|----------|----------------------------------------------------------------|----------------------------------------------------------------------------------------------------|-----------------------------------|
| SSS-T05  | Kill switch API requires authentication                        | `curl -s -o /dev/null -w '%{http_code}' http://localhost:8000/api/kill` (no API key)               | Returns `401`                     |
| SSS-T06  | CORS restricted to known origins                               | `curl -H 'Origin: http://evil.com' -I http://localhost:8000/api/health`                            | No `Access-Control-Allow-Origin: *` |
| SSS-T07  | API authentication enforced on all non-exempt endpoints        | `curl -s -o /dev/null -w '%{http_code}' http://localhost:8000/api/status` (no API key)             | Returns `401`                     |
| SSS-T08  | Security group restricts 8000/3001 to operator IPs             | `aws ec2 describe-security-groups` and verify inbound rules                                        | No `0.0.0.0/0` on 8000 or 3001   |
| SSS-T09  | `.env` file permissions are 600                                | `stat -c '%a' /home/ubuntu/nzt48-signals/.env`                                                     | Output is `600`                   |
| SSS-T10  | Secrets stored in Secrets Manager (if Option A)                | `aws secretsmanager describe-secret --secret-id nzt48-secrets`                                     | Secret exists and is active       |
| SSS-T11  | Pre-commit hook installed and functional                       | Stage a file containing `NZT48_POLYGON_API_KEY=test123` and attempt commit                         | Commit rejected                   |

### 10.3 FULL LIVE Phase Tests

| Test ID  | Description                                                    | Procedure                                                                                          | Pass Criteria                     |
|----------|----------------------------------------------------------------|----------------------------------------------------------------------------------------------------|-----------------------------------|
| SSS-T12  | HTTPS enabled with valid certificate                           | `curl -v https://<domain>:443/api/health`                                                          | TLS handshake succeeds, HTTP 200  |
| SSS-T13  | HTTP (port 80) redirects to HTTPS                              | `curl -s -o /dev/null -w '%{http_code}' http://<domain>/api/health`                                | Returns `301` or `308`            |
| SSS-T14  | TLS 1.0 and 1.1 disabled                                      | `nmap --script ssl-enum-ciphers -p 443 <domain>`                                                   | Only TLS 1.2 and 1.3 listed      |
| SSS-T15  | Dashboard requires VPN or SSO                                  | Access dashboard from non-VPN IP                                                                   | Connection refused or auth prompt |
| SSS-T16  | SSH restricted to bastion IP                                   | Attempt SSH from non-bastion IP                                                                    | Connection refused                |
| SSS-T17  | No sensitive data in health endpoint                           | `curl https://<domain>/api/health`                                                                 | Only `{"status":"healthy"}` returned |

---

## 11. CROSS-REFERENCES

| Document                          | Relevance                                                    |
|-----------------------------------|--------------------------------------------------------------|
| EC2_DOCKER_DRIFT_GUARDS (DDG-001) | Docker exec restrictions, deploy pipeline                    |
| RISK_CONSTITUTION (RC-001)        | Kill switch requirements, circuit breaker access             |
| SELF_HEALING_OPS_SPEC             | Automated restart security implications                      |
| TELEGRAM_TAPE_SPEC                | Telegram message content redaction requirements              |
| OUTPUT_POLICY_SPEC                | PDF and dashboard output content restrictions                |

---

## 12. REVISION HISTORY

| Version | Date       | Author           | Change Description        |
|---------|------------|------------------|---------------------------|
| 1.0     | 2026-02-27 | System Operator  | Initial release           |

---

**END OF DOCUMENT**
