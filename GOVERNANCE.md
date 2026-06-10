# DoSync Protocol — Governance

**Status:** Active  
**Version:** 0.1  
**Maintainer:** Rodrigo Giuliani (giulianireg@gmail.com)

---

## Overview

DoSync is an open protocol (Apache 2.0). This document defines how the specification evolves, how changes are proposed and accepted, and how the project is maintained.

The governance model reflects the current state of the project honestly: DoSync is maintained by a single author. This document establishes the process that allows the project to grow toward community governance without pretending that structure already exists.

---

## Principles

**The spec belongs to implementors, not to the maintainer.**  
Any change that breaks a conforming implementation requires a major version bump. The maintainer cannot unilaterally break implementations that followed the spec.

**Transparency over speed.**  
Spec changes are proposed publicly and documented before they are implemented. A decision made privately and then announced is not acceptable.

**Implementation experience informs the spec.**  
The reference implementation (Python) and any conforming third-party implementations have equal standing as evidence for or against a proposed change. A change that works in theory but fails in practice is rejected.

---

## Roles

### Maintainer
Currently: Rodrigo Giuliani. Responsible for:
- Merging accepted proposals into the spec
- Publishing new spec versions
- Maintaining the certification suite (certify.py)
- Responding to proposals within 14 days. If no response is given within 14 days, the proposer may add the label `needs-response` to escalate; non-breaking proposals with no objections after 21 days total are considered approved

### Implementors
Anyone who has published a DoSync-conforming implementation (Basic tier or higher). Implementors have standing to:
- Propose spec changes
- Object to changes that break their implementation
- Request clarification on ambiguous spec language

### Contributors
Anyone who submits a pull request, files an issue, or opens a Discussion. No prior relationship with the project required.

---

## Change process

### For non-breaking changes
Bug fixes, clarifications, and additions that do not change existing behavior:

1. Open a GitHub Issue with label `spec-change` describing the problem and proposed fix
2. Wait 7 days for objections from any GitHub participant
3. If no blocking objections: maintainer merges and updates the spec version (patch bump)

> **What counts as a valid objection:** An objection must include a specific technical reason why the change would harm a conforming implementation or contradict an existing guarantee. Objections without technical justification may be dismissed by the maintainer with a written explanation.

### For new features (additive, non-breaking)
New intent classes, new optional fields, new endpoints:

1. Open a GitHub Discussion in the `Proposals` category
2. Tag it `RFC` with a short title (e.g., `RFC: add location field to Intent`)
3. Describe: motivation, proposed change, impact on existing implementations, migration path
4. Discussion period: 14 days minimum
5. If no blocking objections from any GitHub participant: maintainer merges (minor version bump)

### For breaking changes
Changes that would fail a currently-conforming implementation:

1. Open a GitHub Discussion tagged `RFC` and `breaking`
2. Discussion period: 30 days minimum
3. Requires at least one independent implementor (different author, different organization) to validate the change against their implementation. **Before v1.0.0**, when no independent implementors exist, the maintainer may proceed after the 30-day discussion period with explicit written rationale.
4. Results in a major version bump (v0.x → v1.0 or v1.x → v2.0)
5. The previous major version receives security fixes on a best-effort basis for up to 12 months after the new version is published

### What cannot change without a breaking version bump
- Any field in the Capability Manifest that is currently required
- Any HTTP endpoint path or method
- Any HTTP status code currently specified for an error condition
- The SHA-256 audit log chaining algorithm
- The emergency bypass behavior for `bypass_on_emergency=True` policies

---

## Versioning

DoSync uses semantic versioning: `MAJOR.MINOR.PATCH`.

| Change type | Version bump | Example |
|---|---|---|
| Bug fix or clarification | PATCH | 0.1.0 → 0.1.1 |
| Additive feature (new intent, new optional field) | MINOR | 0.1.0 → 0.2.0 |
| Breaking change | MAJOR | 0.1.x → 1.0.0 |

The protocol version (`dosync/0.1`) and the hub implementation version (`v0.3.0`) are independent. A hub at v0.3.0 implements protocol v0.1. The protocol version only bumps when the spec changes.

**Stability guarantee:** From v1.0.0 onward, all changes within a major version are backward compatible. Until v1.0.0, the spec may have breaking changes between minor versions with 30 days notice.

---

## Implementation rights

Any individual, company, or organization may implement the DoSync Protocol under the terms of the Apache 2.0 license without asking permission, paying fees, or notifying the maintainer. This includes:
- Commercial implementations
- Proprietary firmware
- Cloud-hosted hubs
- Embedded implementations on constrained devices

The only requirement to call an implementation "DoSync-compatible" is passing the relevant certification tier using the official `certify.py` tool.

---

## Tag vocabulary

The protocol defines the format of intent resolution tags but not their canonical values. To enable interoperability between implementations from different organizations, a standard tag vocabulary will be maintained in `spec/TAG-VOCABULARY.md` (in progress).

When `spec/TAG-VOCABULARY.md` is published, implementors SHOULD use its tags for applicable capabilities. Custom tags for domain-specific use cases are permitted but reduce interoperability with implementations from other vendors.

To propose a new standard tag:
1. Open a GitHub Issue with label `tag-proposal`
2. Describe the tag name, semantic meaning, and which intent classes it is relevant for
3. Maintainer adds accepted tags to `spec/TAG-VOCABULARY.md` within 7 days

---

## Security vulnerabilities

Security issues should be reported privately to giulianireg@gmail.com before public disclosure. The maintainer will respond within 72 hours and coordinate a fix before any public announcement.

---

## Path to community governance

When DoSync has three or more independent implementations (different authors, different organizations), the governance model will be updated to:
- A Technical Steering Committee with one representative per active implementation
- Majority vote for non-breaking changes, supermajority (2/3) for breaking changes
- Rotating maintainer responsibility

This transition is tracked in the repository as the **"Community Governance Transition"** milestone on GitHub.

---

## Current RFC status

| RFC | Status | Opened |
|---|---|---|
| No open RFCs | — | — |

*To open an RFC: [GitHub Discussions → New Discussion → Proposals](https://github.com/giulianireg-spec/dosync-protocol/discussions)*

---

*DoSync Protocol v0.1 · Apache 2.0 · github.com/giulianireg-spec/dosync-protocol*
