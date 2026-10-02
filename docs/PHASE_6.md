# Phase 6 — Account-free student experience

The owner excluded student accounts on 2 October 2026. There is no student username, password, registration, authentication user or password-recovery flow. Staff issue a private, expiring link instead.

## Implemented

- Person 360 has **Student private links and requests**. Authorized administrators, managers and counselors can issue a one-, three- or seven-day link for records within their editing scope. A new link revokes earlier links. Staff can revoke all links immediately.
- Only a SHA-256 digest of the random 256-bit secret is stored. The secret appears once in the staff response. It travels in the URL fragment, is exchanged through a CSRF-protected request, then is removed from the address. The private view uses a one-hour session and checks link expiry, revocation and record eligibility on every API request.
- Students can view their own application states, offers and conditions, visa status, pre-departure checklist, enrollment and open deadlines. Internal notes, blockers, contacts and finance records are excluded.
- Documents stay private until explicitly shared by responsible staff. Readable PDF/PNG/JPEG uploads are limited to 5 MB and preserve immutable versions. Student uploads have no staff author; the audit record identifies the private-link origin. Staff must review and verify them.
- Requested actions can be submitted through the link. Staff record completion, request changes or cancel with a reason. Responses never change application or visa decisions.
- Office messages and student-link responses share an immutable conversation history. Staff receive in-app notifications for incoming messages and uploads. Older messages can be loaded.
- Person merges revoke private links to both records. Requests move with the case; staff and newly authorized survivor views retain source conversation history. Archived or merged source records deny private access.
- The student screen is responsive and uses no-index, no-store and no-referrer protections. Staff permissions still apply to every management API.

## Access meaning

Possession of the link authorizes access; it does not independently prove the visitor's identity. Staff must confirm the recipient and share it privately. Do not print these links on public visitor posters. The shared visitor-registration QR remains the public entry point.

## Verification

The final PostgreSQL functional suite passed 132 tests across the application. Focused checks cover issuance without creating a user, hashed storage, cross-person denial, CSRF/throttling, revocation, expiry, archival/merge denial, private documents, safe uploads, immutable versions/messages and staff action review. Browser checks passed link exchange, fragment removal, upload, two-way messaging and action review with no script errors or page overflow at 320, 390, 768 and 1280 px.

Actual staff/student acceptance and safe real-recipient sharing remain operational acceptance work.
