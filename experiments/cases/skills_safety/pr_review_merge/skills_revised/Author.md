# Revised **Author** skill (STJP-validated)

Fix vs original: the original `address-comments` file never says who your
revision goes to, or when you are actually free to stop looping. This
contract makes both concurrent reviewers first-class recipients of every
revision, and says exactly what "done" means: a `MergeDone` from the
Merger, which can only ever arrive after the loop has produced a clean
quality pass *and* a clean security pass on the same revision.

```localtype
CodeReviewer!ReadyForReview(String);
SecurityReviewer!ReadyForReview(String);
rec ROUND {
  choice at CodeReviewer {
    CodeReviewer?ReviewComments(String);
    CodeReviewer!Revision(String);
    SecurityReviewer!Revision(String);
    continue ROUND;
  } or {
    CodeReviewer?QualityClean(String);
    choice at SecurityReviewer {
      SecurityReviewer?SecurityFindings(String);
      CodeReviewer!Revision(String);
      SecurityReviewer!Revision(String);
      continue ROUND;
    } or {
      SecurityReviewer?SecurityApproved(String);
    }
  }
}
Merger?MergeDone(String);
```
