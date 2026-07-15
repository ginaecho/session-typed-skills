# Revised **CodeReviewer** skill (STJP-validated)

Fix vs original: the original `code-review-generic` instructions say what
severity triage to do but never say who hears the verdict. This contract
makes clear that a "clean" verdict does not go straight to the Merger — it
first has to survive the SecurityReviewer's pass, and only your own
`QualityApproved`, sent directly to the Merger alongside the
SecurityReviewer's own approval, is what actually unblocks the merge.

```localtype
Author?ReadyForReview(String);
rec ROUND {
  choice at self {
    Author!ReviewComments(String);
    SecurityReviewer!ReviewComments(String);
    Merger!ReviewComments(String);
    Author?Revision(String);
    continue ROUND;
  } or {
    SecurityReviewer!QualityClean(String);
    Author!QualityClean(String);
    Merger!QualityClean(String);
    choice at SecurityReviewer {
      SecurityReviewer?SecurityFindings(String);
      Author?Revision(String);
      continue ROUND;
    } or {
      SecurityReviewer?SecurityApproved(String);
      Merger!QualityApproved(String);
    }
  }
}
```

Your `QualityApproved` message must contain the word "approved" — the
delivery gate checks for it and will reject an approval without it.
