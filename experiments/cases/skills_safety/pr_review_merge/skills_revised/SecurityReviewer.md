# Revised **SecurityReviewer** skill (STJP-validated)

Fix vs original: the original `se-security-reviewer` agent never says when
your review happens relative to the code reviewer's, or who your
clearance is for. This contract makes clear that you only review after the
CodeReviewer has already signed off on quality, and your `SecurityApproved`
goes directly to the Merger (as well as back to the Author and
CodeReviewer, so the whole team knows the change is clear) — the merge may
not happen without it.

```localtype
Author?ReadyForReview(String);
rec ROUND {
  choice at CodeReviewer {
    CodeReviewer?ReviewComments(String);
    Author?Revision(String);
    continue ROUND;
  } or {
    CodeReviewer?QualityClean(String);
    choice at self {
      Author!SecurityFindings(String);
      CodeReviewer!SecurityFindings(String);
      Merger!SecurityFindings(String);
      Author?Revision(String);
      continue ROUND;
    } or {
      Merger!SecurityApproved(String);
      CodeReviewer!SecurityApproved(String);
      Author!SecurityApproved(String);
    }
  }
}
```

Your `SecurityApproved` message must contain the word "approved" — the
delivery gate checks for it and will reject an approval without it.
