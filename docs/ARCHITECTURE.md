# Customer Wise MVP: technical architecture plan

## Context

The repo is empty (README plus `aretfacts/customer-wise-logo-web.webp`). Linear holds the scope: the **MVP Shopper workspace pilot** milestone (~30 stories), the scope record **CUS-140**, and closed decisions D01–D10 and D21. We have an approved clickable prototype (https://claude.ai/artifact/UxrMxGDUQc3e4wDMaDY47o) with six screens: sign in, my assignments, assignment detail, report draft and evidence, staff monitoring, and staff review/return/export. This plan sets the architecture and build order so coding can start at CUS-123.

Fixed inputs:
- **Stack (D01):** Python, Django and PostgreSQL. Railway Hobby plan, EU region; dev and production environments; Railway default URL.
- **Sign-in (D02):** email magic link (single use, 15 minutes) or Microsoft/Google; no passwords; invitation only, with invites valid 7 days; 14-day sessions; email through Brevo.
- **Roles (D03):** staff and shopper only. All staff are equal.
- **Rules from the other decisions:**
  - assignment stages, the stalled flag and the returned flag (D08)
  - one active shopper per target; reassignment by staff only (D07)
  - versioned questionnaires per project, with section display styles (D06)
  - evidence file types, the 25 MB limit and the edit-lock rules (D10)
  - contacts recorded by job title only; no audio files (D21)

Decisions made 5–6 Nov 2026:
- **Setup screens:** all setup is done in **Django admin**: users and invites, client organisations, shoppers, the project, its names, questionnaire, brief and shopper assignments.
- **Front end:** **Django templates + HTMX**.
- **File storage:** **Railway Buckets**.
- **Malware scanning:** none. **File type and size checks only**, because the user is the only pilot shopper. This is a recorded pilot exception to D10/CW-084.

## Architecture overview

- **One Django service.** The server renders the pages and HTMX handles the partial updates.
- **No task queue for the pilot.** Without malware scanning nothing needs a worker. Emails go out over the Brevo API inside the request, and exports are generated when staff request them.
- **Railway project (EU), per environment:**
  - **web:** the Django app in a Docker image, run by gunicorn. Before each deploy, `manage.py migrate` runs.
  - **Postgres**
  - **Bucket:** private storage for evidence and exports.
- **Expected cost:** about $5–10 a month.
- **Static files:** served by WhiteNoise. HTMX and Trix are vendored into `static/`, so there is no CDN and no Node build.
- **Python and Django:** Python 3.13 and the current stable Django 6.x. Django 6 brings built-in CSP and template partials, which suit HTMX fragments.

### Key libraries
| Need | Choice |
|---|---|
| Microsoft/Google sign-in | `django-allauth` (social accounts only; sign-up disabled; an adapter only allows invited, active emails) |
| Magic links and invites | small in-house code in `accounts`: hashed single-use tokens with expiry |
| Email | `django-anymail` with Brevo |
| S3 storage | `django-storages[s3]` (presigned URLs, 5 minutes) |
| Edit history (soft delete, visible to staff) | `django-simple-history` |
| Sanitising rich text | `nh3`, allowing only p, br, ul, ol, li, strong and b |
| Rich-text editor | Trix, with the toolbar cut down to bold and bullets |
| PDF export | WeasyPrint (Pango installed in the Docker image) |
| Word export | `python-docx`, plus a small converter for the HTML subset above |
| File type sniffing | `filetype`, plus zip/OLE checks for Office files and emails |
| Settings | environment variables (`django-environ`) |
| Tooling | `uv`, `ruff`, `pytest-django`, `factory_boy` |

## Code layout

```
pyproject.toml  uv.lock  Dockerfile  railway.json  .github/workflows/ci.yml
docs/ARCHITECTURE.md            ← this plan, committed
config/settings/{base,dev,test,prod}.py  config/urls.py
apps/
  core/          base templates, design tokens CSS, HTMX helpers, access.py (query scoping), decorators
  accounts/      User, Invitation, LoginToken, login/magic-link views, allauth adapter, admin actions
  projects/      ClientOrganisation, Project, BriefVersion, BriefAcknowledgement, Target
  questionnaires/ Questionnaire, QuestionnaireVersion, Section, Question, publish service, seed command
  fieldwork/     Assignment, Activity, stage service, shopper list/detail, staff monitoring
  reports/       Report, Answer, ReportSubmission (frozen snapshot), ReviewComment, ReportNote; editor, submit, review/return
  evidence/      EvidenceFile, validators, upload/download views
  exports/       render_report() → PDF / DOCX / ZIP
  audit/         AuditEvent, record() helper
templates/  static/css/tokens.css (from prototype)  static/img/logo.webp
tests/
```

## Data model (summary)

- **User:** an email-based custom user (`AUTH_USER_MODEL`), with `role` (staff or shopper), `is_active` and an unusable password. Staff have `is_staff=True`, which gives them admin access; shoppers never do.
- **Invitation:** `email`, `token_hash`, `expires_at` (7 days), `used_at` and `revoked_at`. Resending revokes the old invitation.
- **LoginToken:** a magic link with `token_hash`, a 15-minute expiry and single use.
- **ClientOrganisation:** a name and a reference (internal only).
- **Project:** links to the client organisation and has three display names: `internal_name`, `client_facing_name` and `shopper_facing_name`. It also has `status` and `ended_at`; the `ended_at` field supports the 12-month retention rule later. A project has **no shopper field**. Shoppers are linked through Assignments, one per target, so each target in a project can have a different shopper.
- **BriefVersion:** belongs to a project, holds the text, and is never changed after saving. Admin saves create a new version.
- **BriefAcknowledgement:** links a user to a brief version.
- **Target:** belongs to a project and has `name`, an optional `website` and a `domain` (normalised, used to detect duplicates). It also has `added_by`, `added_by_shopper` and `archived_at`, and keeps edit history.
- **Assignment:**
  - **Links and approval:** target and shopper, plus `approval_confirmed_offplatform` and a note.
  - **Progress:** `stage` (Not started … Report drafting, Complete) and `unable_reason`/`unable_note`.
  - **Closure:** `closed_at` with a reason (reassigned, withdrawn or unable).
  - **Flags:** `returned` and `last_activity_at`, which drives the stalled flag.
  - **Rule:** a **partial unique index** on `target` where `closed_at IS NULL` enforces one active assignment per target.
  - **Why a separate table, not a shopper column on Target:** under D07, reassigning a target closes the old assignment, which keeps its activity log, draft and history for staff, and opens a fresh one for the new shopper. Stage, the returned and stalled flags, activities and the report all belong to one shopper's attempt at a target. So a target has **many assignments over time but at most one open**, and a project's targets can each have a different shopper.

```
ClientOrganisation 1─* Project 1─* Target 1─* Assignment *─1 User(shopper)
                                               │ (≤1 open per target)
                                               ├─* Activity
                                               ├─* EvidenceFile
                                               └─1 Report ─* Answer, ─* ReportSubmission ─* ReviewComment
Project 1─* QuestionnaireVersion 1─* Section 1─* Question      Project 1─* BriefVersion
```
- **Activity:** belongs to an assignment and has a type, a date and time, `planned` (derived from whether the date is in the future), `contact_job_title`, notes and `deleted_at`. It keeps edit history.
- **QuestionnaireVersion:** belongs to a project and has a number, a status (draft or published) and `published_at`.
  - A **Section** belongs to a version and has an order, a title and a `display_style`: Q&A table, questions as headings, answers only, or key facts.
  - A **Question** belongs to a section and has an order, a `key` that stays the same across versions, a prompt, a guidance note and an `answer_type` (short text, long text or date/time).
- **Report:** one per assignment. It records its questionnaire version, a status (draft, submitted or returned) and `version_no`.
  - An **Answer** links to a report and a question, and holds `text` (sanitised HTML) or `datetime`.
- **ReportSubmission:** created at each submit. It stores a frozen JSON snapshot of the answers, the questionnaire version and the evidence list, for audit and review.
- **ReviewComment:** links to a submission and optionally a question, with the author and the text.
- **ReportNote:** notes from the shopper to Customer Wise. These never appear in exports.
- **EvidenceFile:** belongs to an assignment and has `storage_key` (a random UUID path), `original_name`, `content_type`, `size`, `category`, `scan_status` (fixed to "not_scanned" for the pilot) and `deleted_at`. It keeps edit history.
- **AuditEvent:** records the actor, action, object type and ID, project, JSON data and time. A **Postgres trigger blocks UPDATE and DELETE** on this table, and admin shows it read-only.

All models use UUID primary keys, so URLs never expose IDs that can be guessed in sequence.

## Cross-cutting design

**Access control (CW-003, CW-084).**
- `apps/core/access.py` holds the only way to fetch protected objects: `assignments_for(user)`, `reports_for(user)`, `evidence_for(user)` and `targets_for(user)`. Shoppers get only their own active assignments; staff get everything.
- Every view uses `get_object_or_404(<scoped queryset>, pk=…)`. A shopper asking for someone else's object gets a 404, never a 403.
- Views are guarded by `@shopper_required` and `@staff_required`.
- File downloads go through the same scoping. Tests for guessing or changing IDs are required for every object view.

**Business rules live in service functions, not views.**
- **Fieldwork stages:** `fieldwork/services.py` has `set_stage()`, `mark_unable()`, `reassign()`, `withdraw()` and `add_target()`. `add_target()` warns about duplicates, assigns the target to the shopper automatically and records an audit event.
- **Reports:** `reports/services.py` has `save_answer()`, `submit_report()` and `return_report()`.
  - `submit_report()` checks every answer is filled in and the latest brief is acknowledged. It then saves the snapshot, sets the stage to Complete and locks editing.
  - `return_report()` saves the comments, sets the stage back to Report drafting with the returned flag, and unlocks editing.
- **Admin actions call the same services:** send/resend invite, reassign, withdraw, publish questionnaire (asking whether open drafts move to the new version), and create a new draft version.

**Locking (D10).** Activities, evidence and answers can only be edited while the report is in draft or returned. The service layer enforces this as well as the screens.

**Autosave (CW-038).** Each answer field posts through HTMX on `input changed delay:1.5s` and on blur. The server returns a "Saved hh:mm" or error fragment. Unsaved changes trigger a `beforeunload` warning. Save failures show a red status bar, never a silent loss.

**Questionnaire versions (D06).** Published versions are never edited. Moving open drafts to a new version copies answers by `Question.key`, leaves new questions blank, and keeps answers to removed questions in a history JSON on the report. Reports never depend on the pilot questions. A `load_questionnaire` command imports the D06 seed list from `apps/questionnaires/seed/pilot.json`.

**Brief changes (D06).** Saving a brief in admin creates a new `BriefVersion`. The shopper sees a paragraph-level diff (`difflib`) of the latest version against the last one they acknowledged, and must acknowledge it before submitting. Staff see who has acknowledged in admin.

**Report rendering (D06, MVP-02).** `exports/render.py:build_report_document(submission)` produces a neutral structure: a title page, sections, and evidence included on request. Three renderers consume it, each supporting the four display styles: the on-screen review template, the WeasyPrint PDF template and the DOCX builder. Exports use the client-facing project name, never include the shopper's name or private notes, and record an audit event. Evidence is bundled into a ZIP that streams from the bucket.

**Files (D10, CW-041, CW-084).**
- Uploads go through Django. The checks are:
  - a 25 MB limit, enforced in settings and in a validator
  - an allowed extension that matches the sniffed file type
  - Office files must be OOXML zips with no `vbaProject.bin`; .doc, .xls and macro-enabled files are rejected
  - text and transcript files must decode as UTF-8
  - audio is rejected
- Files are stored under `evidence/<uuid>` in the private bucket.
- Downloads go through a view that checks access, then redirects to a **5-minute presigned URL** set to download as an attachment.
- `scan_status` is kept so a ClamAV scanner can plug in later without changing the model.

**Personal data (D21).** Activities have a job-title field only, never a name, email or phone. Upload and activity forms show the transcript guidance. Each project's `ended_at` date drives the later 12-month deletion (CW-087, out of the MVP).

**Security settings.**
- `SECURE_*` settings, HSTS, secure session and CSRF cookies, and a 14-day session.
- Django's default `ModelBackend` drops inactive users on their next request, so deactivation takes effect immediately; a test covers this.
- CSP is set with no inline scripts, and the CSRF token reaches HTMX through the `hx-headers` attribute on `<body>`.
- Requests for sign-in emails are rate-limited by email and by IP, and the response is the same whatever the email.
- Admin sign-in redirects to the app's sign-in page.

**Bootstrapping.** `manage.py create_staff_user <email>` creates the first staff user and prints a one-time sign-in link, replacing `createsuperuser`, because there are no passwords.

**Audit (CW-085).** `audit.record(actor, action, obj, **data)` is called from the service functions. Events recorded:
- invite sent, resent or revoked
- target added or archived
- shopper assigned, reassigned or withdrawn
- stage changed, or marked unable to complete
- report submitted, returned or exported
- brief or questionnaire published
- user deactivated

**UI.**
- `tokens.css` carries the prototype's palette, as recorded in the brand palette memory. Primary blue #029BA9; links #007480; tags #3DC1CB with #10282B text; Nunito Sans; the logo from `aretfacts/`.
- Each prototype screen becomes one template with HTMX partials for filters, dialogs, the activity log, autosave and comments.

**Environments.**
- **Local:** `uv` with a local Postgres (Postgres-specific features are used: a partial index and a trigger), file-system storage and the console email backend.
- **Railway:** dev and production environments, each with its own Postgres and bucket. Secrets live in Railway variables.
- **CI (GitHub Actions on `jamesmrenshaw/customerwise`):** ruff, `makemigrations --check`, pytest against a Postgres service, and `check --deploy` with prod settings. Railway deploys `main` once CI passes.

## Build order (maps to Linear)

1. **Foundation (CUS-123 CW-088, CUS-118 CW-083):** commit this plan as `docs/ARCHITECTURE.md`; scaffold the project, settings, Docker, CI and the Railway dev and production environments with a bucket; base layout with tokens and the logo; a health check.
2. **Sign-in and roles (CUS-36, CUS-37, CUS-38, CUS-39):** the User model, invitations, magic links, Microsoft/Google through allauth, the staff and shopper decorators, `access.py`, the admin invite actions and `create_staff_user`.
3. **Setup in admin (CUS-40, CUS-41, CUS-44, CUS-45, CUS-50, CUS-54, CUS-62):** the models and admin for organisations, projects (three names), briefs, questionnaire versions with publishing, and assignment, reassign and withdraw actions; the seed command.
4. **Shopper fieldwork (CUS-63, CUS-64, CUS-65, CUS-66, CUS-67, CUS-141):** the my-assignments and assignment detail screens, the stage stepper, unable to complete, the activity log, adding targets with the duplicate warning, and the brief with acknowledgement.
5. **Report and evidence (CUS-70, CUS-71, CUS-72, CUS-73, CUS-74, CUS-75, CUS-76, CUS-78, CUS-79, CUS-84, CUS-119):** the section editor with autosave, Trix and sanitising; notes; uploading, validating and downloading evidence; submit validation and the snapshot; locking.
6. **Staff monitoring and review (CUS-68, CUS-80, CUS-81, CUS-83):** the monitoring screen with filters and stalled flags, the review screen with per-question comments, and returning reports.
7. **Export and audit (CUS-142, CUS-120):** the document builder with PDF, DOCX and ZIP; audit events with the protecting trigger; a read-only audit view in admin.

Each step is its own branch named from Linear's `gitBranchName`, with a PR. The Linear story moves to In Progress and then Done, with a comment linking the PR.

**Linear updates after approval:**
- Record the malware-scanning pilot exception in CUS-140 and CUS-119.
- Record Railway Buckets as the storage provider in D01 (CUS-5).
- Add a note to CUS-123 that there is no worker service in the pilot.

## Verification

- **Automated (pytest, run in CI):**
  - **Access:** access-scoping tests for every object view, covering another shopper's assignment, report, evidence and download, and staff-only views as a shopper. All must return 404.
  - **Sign-in:** expiry and single use for invites and magic links; an uninvited email gets no access; a deactivated user is signed out on their next request; Microsoft/Google refused for an email that wasn't invited.
  - **Business rules:**
    - stage changes, including that Complete can't be set by hand and that returning resets the stage
    - one active assignment per target, checked at the database level
    - two shoppers on different targets in the same project each see only their own targets; reassigning a target keeps the old assignment's history and the old shopper loses access
    - moving drafts to a new questionnaire version by question key
    - submit is blocked when an answer is missing or the brief isn't acknowledged
  - **Uploads:** each allowed type passes; .docm, .doc, audio, renamed executables and files over 25 MB fail.
  - **Export:** PDF and DOCX render all four display styles; the export contains the client-facing name and never the shopper's name or private notes.
  - **Audit:** an UPDATE or DELETE on `AuditEvent` raises an error.
- **Manual end-to-end on the Railway dev environment after each step:**
  - follow the prototype journey: an invite email arrives through Brevo; sign in; add a target; log activity; draft and autosave; upload evidence; submit
  - as staff: see the assignment in monitoring; return it with comments; as the shopper, see the returned flag, fix it and resubmit; staff export PDF/Word and ZIP
- **Security:** `manage.py check --deploy` is clean on production settings; a presigned URL stops working after 5 minutes; an evidence file can't be fetched by its bucket key without a presigned link.
