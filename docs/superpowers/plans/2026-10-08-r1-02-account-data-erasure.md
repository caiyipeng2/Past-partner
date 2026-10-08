# R1-02 Multi-account Data Erasure

Goal: complete the previously approved owner/account data deletion boundary by removing historical data-subject notices in the existing metadata transaction. Preserve only the new deletion notice and anonymous receipt. Retain account identities; this is data erasure, not IdP logout or permanent identity removal.

Architecture: reuse POST /api/v1/data-deletion with explicit DELETE confirmation and authenticated owner_id. Add data_subject_notifications to the existing owner-filtered deletion list and its count to the bounded notification schema. Remove historical notices, revoke sessions and refresh tokens, create the receipt and the new notice in one transaction. Preserve the existing object-before-metadata cleanup behavior and its documented limits.

- [x] Write a failing HTTP regression with three accounts (target, same tenant, different tenant), completed imports, old notices and access/refresh credentials.
- [x] Verify old target notices remain in the baseline and cause the regression to fail (expected 1 notice, observed 3).
- [x] Include history cleanup in the metadata transaction and allow the bounded notification count.
- [x] Verify explicit confirmation, read-only scope rejection, repeat deletion and rollback when the completion notification fails, including OIDC refresh restoration and peer refresh survival.
- [x] Document remaining identity, Provider and cross-process concurrency limitations.
- [x] Run focused tests (25 passing) and the full Python/Web regression suite (864 run, 4 external-resource skips; Web 4 passing). Compile check passes. After the full suite, only OIDC regression assertions and documentation were refined; the final focused suite was rerun.
- [x] Complete independent review and prepare the feature branch for commit and user acceptance. Merge/push requires acceptance.
