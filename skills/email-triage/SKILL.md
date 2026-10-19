---
name: email-triage
description: Triage recent unread Gmail into important, follow-up, and ignorable, then apply labels only after the user approves a printed batch list. Read-only until approval. Never deletes and never sends.
id: email-triage
version: 1.0.0
risk: medium
tools:
  - mcp.workspace.search_gmail_messages
  - mcp.workspace.get_gmail_messages_content_batch
  - mcp.workspace.get_gmail_message_content
  - mcp.workspace.list_gmail_labels
  - mcp.workspace.batch_modify_gmail_message_labels
  - mcp.workspace.modify_gmail_message_labels
verification:
  - Every labelled message ID appears in the batch the user approved.
  - Re-read one changed message and confirm the target label ID is present.
  - Report the observed Gmail state instead of claiming success from a 204 response.
recipe:
  objective: Classify recent unread mail into three buckets and apply only the approved label batch, without deleting or sending anything.
  preflight:
    - Require the Gmail MCP server to be reachable and pass user_google_email on every call.
    - Confirm the request is triage. Drafting and attachment archiving belong to other skills.
    - Keep the batch at or below 30 messages so the review list stays readable.
  steps:
    - step_id: search-unread
      tool_id: mcp.workspace.search_gmail_messages
      instruction: Search with query is:unread and page_size 30. The result carries message IDs and links only, so content must be fetched before classifying.
    - step_id: read-batch
      tool_id: mcp.workspace.get_gmail_messages_content_batch
      instruction: Fetch at most 25 message IDs per call to read sender, subject, and snippet. Never infer a body from a subject.
    - step_id: read-one
      tool_id: mcp.workspace.get_gmail_message_content
      instruction: Read the full message only when the snippet is ambiguous about whether an action is requested.
    - step_id: resolve-labels
      tool_id: mcp.workspace.list_gmail_labels
      instruction: Resolve the three target labels to IDs. If a label is missing, state that the approved write will create it and do not create it before approval.
    - step_id: print-batch
      instruction: Print the exact batch as a numbered list with message ID, sender, subject, bucket, and target label, then ask for approval. Do not call any write tool in this step.
    - step_id: apply-batch
      tool_id: mcp.workspace.batch_modify_gmail_message_labels
      instruction: Only after explicit approval, apply add_label_ids for the approved subset. Split calls above 1000 IDs and skip every message the user excluded.
    - step_id: record-preference
      instruction: Record the user's corrections as a memory candidate so the next triage reuses the same judgement.
  verification:
    - Re-read the first changed message and confirm the label ID is present.
    - Report success and failure counts, with the reason for each failure.
  rollback:
    - Reverse a label batch with the inverse remove_label_ids call on the same message IDs.
  stop_conditions:
    - Stop on a denied approval, a missing OAuth grant, or a label ID that does not resolve.
    - Stop before any delete, trash, or send operation. Those tools are out of scope for this skill.
  idempotency_notes: Label changes are set operations and safe to repeat. Repeating a batch does not duplicate a label.
  safety_notes:
    - The built-in confirmation prompt shows only a generic argument preview, so the printed batch list is the review surface.
    - Skill admission is not permission. Ordinary policy confirmation still applies to the label write.
    - Never request or echo OAuth client secrets, refresh tokens, or message bodies the user did not ask to see.
---

# Summary
Use this skill when the user asks to look at, sort, or clean up the inbox.
The output is a reviewable classification list plus, after approval, a label
batch. Classification is expressed as labels, never as deletion, so every
action stays reversible.

# Procedure
1. Search unread mail first. Search returns IDs and links only, so batch-fetch
   content before summarising or classifying.
2. Classify each message into exactly one bucket.
   - `重要` — known contact, explicit action request, or an urgent deadline.
   - `需跟进` — needs a reply but not urgently.
   - `可忽略` — newsletters, automated notices, no action required.

   If the console cannot encode these names (Windows cp1252 raises
   `UnicodeEncodeError`), print the ASCII equivalents `Important`,
   `Follow-up`, and `Ignorable` instead. The Gmail label names are always
   ASCII, so the applied result is identical either way.
3. Render the batch as a numbered list. Each line carries the message ID, the
   sender, the subject, the bucket, and the target label. This list is the
   review surface because the built-in approval prompt does not enumerate
   Gmail message or label IDs.
4. Ask for approval with three options: approve all, approve a subset, or
   cancel. A partial approval names the exceptions; apply only the approved
   subset.
5. On approval, resolve or create the labels and apply the batch. On cancel,
   perform no write and ask which classification rule should change.
6. Report the result as completed and failed counts, then record the user's
   corrections as a memory candidate.

# Verification
Re-read the first changed message and confirm the label ID is present. Compare
the applied message IDs against the approved list. A successful API call is not
proof that the user-visible state changed.

# Rollback
Reverse a batch with the inverse remove_label_ids call on the same message IDs.
No message is ever moved to Trash by this skill, so no restore step is needed.

# Safety
This skill never deletes, never trashes, and never sends. Sending is not
registered in this workspace, and deletion is out of scope by design. Never
present a count-only approval request such as "process 30 messages"; the list
must name the specific mail.

