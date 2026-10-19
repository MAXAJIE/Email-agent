---
name: email-organize
description: Save Gmail attachments such as invoices and receipts into typed folders inside the workspace, after printing the exact folder and file plan for approval. Stretch skill; enable only after triage and draft are stable.
id: email-organize
version: 1.0.0
risk: medium
tools:
  - mcp.workspace.search_gmail_messages
  - mcp.workspace.get_gmail_messages_content_batch
  - mcp.workspace.get_gmail_message_content
  - mcp.workspace.get_gmail_attachment_content
  - file.write
  - file.list_dir
verification:
  - Confirm every written path is inside the workspace and appears in the approved plan.
  - Confirm no existing file was overwritten without the user being told.
recipe:
  objective: Write approved Gmail attachments into typed workspace folders without touching anything outside the workspace.
  preflight:
    - Require the Gmail MCP server to be reachable and pass user_google_email on every call.
    - Confirm the user explicitly asked to save attachments. Do not run this as a side effect of triage.
    - Confirm the destination root is inside the workspace.
  steps:
    - step_id: search-source
      tool_id: mcp.workspace.search_gmail_messages
      instruction: Search for the messages that carry the attachments, for example has:attachment newer_than:30d. The result carries IDs only.
    - step_id: read-attachments
      tool_id: mcp.workspace.get_gmail_messages_content_batch
      instruction: Fetch message content in batches of at most 25 to read attachment filenames, MIME types, and attachment IDs. There is no separate list-attachments tool.
    - step_id: read-one
      tool_id: mcp.workspace.get_gmail_message_content
      instruction: Read a single message when its attachment metadata is incomplete or the filename is ambiguous.
    - step_id: plan-folders
      instruction: Classify each attachment by filename and subject into invoices, receipts, contracts, or unsorted. Put anything uncertain in unsorted instead of guessing.
    - step_id: check-collisions
      tool_id: file.list_dir
      instruction: List the destination folders and mark any planned filename that already exists so the plan can rename it or ask the user.
    - step_id: print-plan
      instruction: Print the exact folder tree and every file to be written, with its source message subject, then ask for approval. Do not write anything in this step.
    - step_id: download-attachment
      tool_id: mcp.workspace.get_gmail_attachment_content
      instruction: Only after approval, download one attachment at a time by message ID and attachment ID.
    - step_id: write-file
      tool_id: file.write
      instruction: Write each approved attachment to its planned workspace-relative path. Never write outside the workspace and never overwrite an existing file silently.
  verification:
    - List the destination folders and confirm the written files match the approved plan.
    - Report written and skipped counts, with the reason for each skip.
  rollback:
    - Move an unwanted file to Trash with the file tool, or delete it after the user confirms. Never delete a Gmail message.
  stop_conditions:
    - Stop on a denied approval, a missing OAuth grant, or a planned path outside the workspace.
    - Stop and ask when a filename collision cannot be resolved by renaming.
  idempotency_notes: Writing the same attachment twice creates a duplicate file, so check the destination folder before retrying after an uncertain failure.
  safety_notes:
    - The built-in confirmation prompt shows only a generic argument preview, so the printed plan is the review surface.
    - Skill admission is not permission. Ordinary policy confirmation still applies to each file write.
    - Never request or echo OAuth client secrets or refresh tokens.
---

# Summary
Use this skill only when the user explicitly asks to save attachments to local
folders. It is a stretch capability: enable it after triage and drafting are
stable. The deliverable is a reviewed folder plan plus the written files.

# Procedure
1. Search for the messages that carry the attachments. Search returns IDs only,
   so fetch content before reading attachment metadata.
2. Read attachment filenames, MIME types, and attachment IDs from message
   content. There is no separate list-attachments tool in this workspace.
3. Classify each attachment into invoices, receipts, contracts, or unsorted.
   Anything uncertain goes to unsorted; do not guess a category.
4. List the destination folders and mark filename collisions.
5. Print the exact folder tree and every file to be written, with the source
   message subject, then ask for approval.
6. On approval, download one attachment at a time and write each file to its
   planned workspace-relative path.
7. Report written and skipped counts with reasons.

# Verification
List the destination folders and compare the written files against the approved
plan. Report the observed filesystem state rather than assuming success.

# Rollback
Move an unwanted file to Trash with the file tool, or delete it after the user
confirms. Never delete or modify a Gmail message as part of this skill.

# Safety
Every path must stay inside the workspace. Never overwrite an existing file
without telling the user; rename it or ask. Never write to an absolute path
outside the workspace, and never treat a downloaded attachment as executable.

