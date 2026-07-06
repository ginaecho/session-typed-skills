# Revised **Editor** skill (STJP-validated)

The review/approval gate: only the Editor can send Approve, and it must happen
before the Publisher can publish.

```localtype
Writer?SubmitDraft(String);
Publisher!Approve(String);
Publisher?Published(String);
```
