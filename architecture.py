"""Which layer each module belongs to, and the order they may depend in.

This is the project's shape, written down so it can be checked rather than
believed. `tests/test_architecture.py` reads it and fails if any module
imports something from a layer above its own.

The order was not chosen by taste. Every cross-layer import in the project
was counted and the ordering that leaves none pointing upward was searched
for; this is it. Before the job_title extraction no such ordering existed,
because sources and sheetops each imported the other.

    core      pure logic. No network, no sheet, no Gmail.
    store     everything that talks to Google.
    sources   reading the outside world into applications and decisions.
    people    who to write to, and at what address.
    sheetops  keeping the sheet correct.
    apply     helping fill an application in.
    outreach  writing and managing drafts.
    flows     orchestration only; no logic of its own.
"""

LAYERS = ["core", "store", "sources", "people", "sheetops", "apply", "outreach", "flows"]

LAYOUT = {
    # core: pure logic, no IO, no other layer
    "core": ["config", "date_utils", "status", "company_id", "company_match",
        "confirmation_parser",
             "app_identity", "company_aliases", "corrections", "job_title", "role_extract", "text_classifier", "job_posting",
             "resume_bank", "sender_profile", "linkedin_helper", "hook_forms"],
    # store: everything that touches the network or disk state
    "store": ["google_auth", "gmail_client", "sheets", "calendar_client", "notify"],
    # sources: reading the outside world into domain objects
    "sources": ["app_scanner", "rejection_scanner", "reply_scanner", "link_finder",
                "apply_capture", "muse", "job_finder", "interview_signals",
                "interview_calendar", "interview_model", "rejection_model"],
    # people: who to contact and at what address
    "people": ["people_finder", "edgar_finder", "team_pages", "people_search",
               "sourcing", "contact_finder", "contact_policy", "address_book",
               "company_formats", "findability",
               "recent_contacts", "network_audit"],
    # outreach: writing and managing drafts
    "outreach": ["draft_writer", "email_drafter", "auto_draft", "auto_outreach",
                 "batch_outreach", "bulk_draft", "draft_cleanup", "stage_drafts",
                 "style_profile", "style_check", "outreach_corpus",
                 "outreach_retrieval", "follow_up", "followup_model",
                 "followup_scheduler", "reoutreach", "open_loops", "targets"],
    # sheetops: keeping the sheet correct
    "sheetops": ["sheet_clean", "sheet_format", "dedupe", "fix_positions",
                 "clean_positions", "location_backfill", "jd_backfill", "enrich",
                 "stale"],
    # apply: helping fill an application in
    "apply": ["apply_autofill", "autofill_bookmarklet", "autofill_extension",
              "cover_letter", "prep_package", "resume_pdf", "setup_wizard"],
    # flows: orchestration only
    "flows": ["auto_pipeline", "watch"],
}
