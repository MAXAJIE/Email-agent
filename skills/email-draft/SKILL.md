---
name: email-draft
description: Turn a one-line instruction into a complete Gmail draft with recipient, subject, and body, shown in full for approval before creation. Creates drafts only and never sends.
id: email-draft
version: 1.0.0
risk: medium
tools:
  - mcp.workspace.search_gmail_messages
  - mcp.workspace.get_gmail_message_content
  - mcp.workspace.get_gmail_thread_content
  - mcp.workspace.draft_gmail_message
verification:
  - Re-read the created draft and compare recipient, subject, thread, and body with the approved preview.
  - Confirm the draft exists and was not sent.
recipe:
  objective: Produce one approved Gmail draft that matches the user's instruction, and stop at draft creation.
  preflight:
    - Require the Gmail MCP server to be reachable and pass user_google_email on every call.
    - Confirm the request is drafting. Triage and attachment archiving belong to other skills.
    - Treat sending as out of scope even when the user asks for it directly.
  steps:
    - step_id: locate-thread
      tool_id: mcp.workspace.search_gmail_messages
      instruction: When the instruction refers to an existing message, search for it and keep the message ID and thread ID for the reply.
    - step_id: read-original
      tool_id: mcp.workspace.get_gmail_message_content
      instruction: Read the full original message before drafting so names, dates, amounts, and deadlines in the draft are accurate.
    - step_id: read-thread
      tool_id: mcp.workspace.get_gmail_thread_content
      instruction: Read the thread only when the reply depends on earlier turns that the single message does not contain.
    - step_id: compose
      instruction: Write recipient, subject, and body. Match the formality and length of the user's instruction instead of padding it with pleasantries.
    - step_id: print-draft
      instruction: Print the complete draft with recipient, subject, and full body, then ask whether to create it. Do not call the draft tool in this step.
    - step_id: create-draft
      tool_id: mcp.workspace.draft_gmail_message
      instruction: Only after approval, create the draft with the approved body verbatim, a Re subject and thread_id when replying. Draft creation is the final action.
  verification:
    - Re-read the draft and compare it against the approved preview.
    - Report the draft location and state explicitly that nothing was sent.
  rollback:
    - Discard an unwanted draft in the Gmail UI. Never send it first to clear it.
  stop_conditions:
    - Stop on a denied approval or a missing OAuth grant.
    - Stop before any send operation. Sending is not registered in this workspace.
    - Stop and ask when the recipient address is ambiguous or missing.
  idempotency_notes: A repeated draft call creates a second draft, so check for an existing draft before retrying after an uncertain failure.
  safety_notes:
    - The built-in confirmation prompt shows only a generic argument preview, so the printed draft is the review surface.
    - Skill admission is not permission. Ordinary policy confirmation still applies to draft creation.
    - Never request or echo OAuth client secrets or refresh tokens.
---

# Summary
Use this skill when the user gives a one-line instruction such as "reply to
Zhang San that Wednesday works". The deliverable is one complete draft that the
user reviews in full before it is created. Sending is never part of this skill.

# Procedure
1. If the instruction refers to an existing message, search for it and keep the
   message ID and thread ID.
2. Read the full original message. Do not draft from a subject line alone,
   because names, dates, amounts, and deadlines must be accurate.
3. Compose recipient, subject, and body. Match the formality and length of the
   instruction; do not add filler courtesy text the user did not ask for.
4. Print the complete draft: recipient, subject, and the full body between
   delimiters. Ask whether to create it, and offer to revise instead.
5. On revision feedback, rewrite and print again. Do not decide on your own that
   the draft is good enough to skip the second confirmation.
6. On approval, create the draft with the approved body verbatim. When replying,
   keep the thread ID and use a `Re:` subject.
7. Report the draft location and state that nothing was sent.

# Verification
Re-read the created draft and compare recipient, subject, thread, and body with
the approved preview. Report the observed draft rather than assuming success
from a successful API call.

# Rollback
Discard an unwanted draft in the Gmail UI. Never send a draft to clear it.

# Safety
This skill creates drafts and stops there. If the user asks to send directly,
say that sending is outside this skill and outside this workspace's registered
tools, and leave the final send to the user in Gmail. Never create a draft
without first printing its full content.

