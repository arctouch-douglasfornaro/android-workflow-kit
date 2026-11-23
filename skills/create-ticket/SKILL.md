---
name: create-ticket
description: Write a ready-to-paste ticket (title, description, acceptance criteria, tech detail) in English from a short bug, feature or improvement description. Returns the text only; filing it in a tracker (Linear, Jira, GitHub, a task board) is the caller's step. Use when the user types /create-ticket or asks to draft or write up a ticket or task, or when another workflow (bug triage) needs a ticket proposal.
---

# Create ticket

Turn a short description of a bug, feature or improvement into a ticket a teammate can pick up.

## Input

The description is whatever was passed as the argument (`$ARGUMENTS`), or what the calling workflow
hands over (a bug triage passes its symptom, reproduction, evidence and cause).

- With a description, use it directly.
- Without one, ask for one short paragraph about the bug, feature or improvement, then continue.
- Infer acceptance criteria and technical context from the description and, when it helps, from the
  current repository. Name a file, class or method only when you found it; never invent one.

## Output

- Return only the ticket text: no preamble, no closing remarks.
- Write it in English, whatever the conversation's language.
- This skill never creates anything. Filing the ticket in Linear, Jira, GitHub or a task board is a
  separate step that the developer or the calling workflow does with that tool, after the developer's OK.
- Keep personal data out: no real user names, emails or account ids; describe a test account by its role.

## Structure

```
<Title: short, starts with a verb>

<One or two sentences on what the ticket is about and why.>

**Acceptance criteria:**

- <Clear, testable criterion.>
- <…>

**Tech detail:**

<Useful technical context: files, classes, methods, the previous behavior, a similar implementation to
reuse.>
```

For a bug, the description says what happens and what is expected, and the ticket adds a
**Steps to reproduce** list (with the build or version and the device) before the acceptance criteria,
plus the evidence it has (a screenshot, a clip, a log line) and the probable cause with `file:line`
when one is known.

## Example

---

Add a "Sign in with Google" button to the login screen

Users can only sign in with email today. This adds Google sign-in to the login screen.

**Acceptance criteria:**

- Tapping the button starts Google sign-in.
- A successful sign-in opens the Home screen.
- A cancelled or failed sign-in keeps the user on the login screen with an error message.

**Tech detail:**

`LoginViewModel` already handles the email flow; the new action can follow `onEmailSignInClicked`.
The signup screen has a Google button whose handling can be reused.

---
