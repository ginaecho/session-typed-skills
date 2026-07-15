# Revised **Merger** skill (STJP-validated)

Fix vs original: the original `principal-software-engineer` agent's own
bias — "good over perfect", "bias to delivery... to keep the team
unblocked" — is exactly what an unprotocoled ship call would exploit: it
never says which approvals to wait for. This contract makes you stay
silent through every round of the review loop (you still see each round's
`ReviewComments`/`QualityClean`/`SecurityFindings` go by, so you always
know why the change isn't ready yet) and merge only once **both**
approvals — `SecurityApproved` and `QualityApproved` — have reached you on
the same, final revision.

```localtype
rec ROUND {
  choice at CodeReviewer {
    CodeReviewer?ReviewComments(String);
    continue ROUND;
  } or {
    CodeReviewer?QualityClean(String);
    choice at SecurityReviewer {
      SecurityReviewer?SecurityFindings(String);
      continue ROUND;
    } or {
      SecurityReviewer?SecurityApproved(String);
      CodeReviewer?QualityApproved(String);
    }
  }
}
Author!MergeDone(String);
```
