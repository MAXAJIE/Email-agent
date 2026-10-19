---
name: mailkeep-gmail
description: Triage Gmail by type, label reviewed batches, audit Trash, and write draft replies without sending.
id: mailkeep-gmail
version: 0.1.0
risk: medium
tools:
  - mcp.workspace.search_gmail_messages
  - mcp.workspace.get_gmail_messages_content_batch
  - mcp.workspace.get_gmail_message_content
  - mcp.workspace.list_gmail_labels
  - mcp.workspace.modify_gmail_message_labels
  - mcp.workspace.batch_modify_gmail_message_labels
  - mcp.workspace.draft_gmail_message
  - mcp.workspace.get_gmail_attachment_content
verification:
  - Confirm every changed message ID appears in the user-approved batch.
  - Confirm a created draft exists and contains the approved body; confirm it was not sent.
  - For Trash analysis, state that nothing was permanently deleted.
recipe:
  objective: Help the user find specific mail, apply labels only after an explicit reviewed batch, draft replies without sending, and report actionable Trash facts.
  preflight:
    - Require the Gmail MCP server to be reachable and every Gmail tool call to include user_google_email.
    - Confirm the requested operation is read, label, draft, or trash analysis; sending is out of scope.
    - Select one capability from the request instead of running the whole recipe.
  steps:
    - step_id: search-mail
      tool_id: mcp.workspace.search_gmail_messages
      instruction: Translate the request into one Gmail query (type, sender, date window, unread, attachment) and search. The result contains message IDs and links, not bodies.
    - step_id: read-batch
      tool_id: mcp.workspace.get_gmail_messages_content_batch
      instruction: Fetch at most 25 message IDs at a time to classify and summarise. Never infer a body from a subject.
    - step_id: read-one
      tool_id: mcp.workspace.get_gmail_message_content
      instruction: Read the full message before drafting a reply or quoting an amount, date, or deadline.
    - step_id: review-list
      tool_id: mcp.workspace.list_gmail_labels
      instruction: Resolve the target label to its ID. If it does not exist, say that the approved write will create it; do not create it before approval.
    - step_id: apply-labels
      tool_id: mcp.workspace.batch_modify_gmail_message_labels
      instruction: Print the exact batch (message IDs, senders, subjects, target label) for the user, obtain confirmation, then apply with add_label_ids or remove_label_ids. Split batches above 1000 IDs.
    - step_id: draft-reply
      tool_id: mcp.workspace.draft_gmail_message
      instruction: Create a draft addressed to the original sender with a Re subject and thread_id when replying. Pass the approved body exactly. Draft creation is the final action; never send.
    - step_id: trash-audit
      tool_id: mcp.workspace.search_gmail_messages
      instruction: Search in:trash, fetch metadata in batches, and report count, size, types, top senders, duplicates, restore candidates, and likely-disposable mail. Do not empty Trash or delete anything.
  verification:
    - Re-read the first changed message and confirm the label ID is present or absent as requested.
    - Re-read the draft and confirm recipient, subject, thread, and body against the approved preview.
    - Report observed Gmail state rather than claiming success from a 204 response alone.
  rollback:
    - Reverse a label change with the inverse add/remove call on the same message IDs.
    - Delete an unwanted draft with the Gmail UI or draft deletion surface; do not send it first.
    - Restore Trash candidates by removing the TRASH label only when the user asks.
  stop_conditions:
    - Stop on a denied confirmation, a missing OAuth grant, or a label ID that does not resolve.
    - Stop before any send operation; the tool is not registered for this workspace.
    - Stop if the approved message IDs no longer match the reviewed batch.
  safety_notes:
    - Skill admission is not permission; ordinary policy confirmation still applies to every write.
    - The built-in confirmation prompt does not enumerate Gmail message or label IDs, so the agent must print the batch itself before asking.
    - Never request or echo OAuth client secrets, refresh tokens, or message bodies beyond what the user asked to see.
  idempotency_notes: Label changes are set operations and safe to repeat; a repeated draft creates a second draft, so check the existing draft before retrying after an uncertain failure.
---

# Summary

Use this skill for three separate jobs: finding specific mail, applying a
reviewed label batch, or producing a draft. Trash work is read-only analysis.
Select the capability the user asked for; do not chain unrelated mutations.

# Procedure

1. Build one Gmail query from the request. Supported categories are invoices,
   receipts, newsletters, notifications, jobs, travel, promotions, plus
   `is:unread`, `has:attachment`, and `in:trash`.
2. Search first. Search returns IDs and links only, so batch-fetch content
   before summarising or classifying.
3. Render the exact batch as a numbered list with message IDs, sender, subject,
   date, and the target label. This list is the review surface because the
   built-in approval prompt shows only a generic argument preview.
4. Ask for confirmation. On approval, resolve or create the label and apply the
   batch. Split calls above 1000 IDs.
5. For replies, read the full message, keep `thread_id`, use a `Re:` subject,
   and create a draft with the approved body. Never send.
6. For Trash, report facts: total count and size, category mix, top senders,
   duplicate subjects, restore candidates, and likely-disposable mail. State
   that nothing was permanently deleted.

# Verification

Re-read the first changed message and check the label ID. Re-read the draft
and compare recipient, subject, thread, and body with the approved preview.
A successful API call is not proof that the user-visible state changed.

# Rollback

Reverse a label batch with the inverse operation on the same IDs. Drafts can be
discarded without sending. Restore Trash items only by removing the TRASH label
after the user asks.

# Safety

This workspace runs the Gmail MCP server with `send_gmail_message` disabled.
Draft creation is the boundary. Never attempt to enable or emulate sending, and
never print OAuth secrets or token contents.
