-- 002: separate internal recall features from publishable k-anonymous tags.
--
-- Rationale: enforcing the k-anonymity tuple rule on *matching* features as well as
-- on disclosed ones collapses every profile to a single tag at realistic pool sizes,
-- which silently destroys match quality. Cluster-level tags now live in
-- internal_tags (sandbox-only), while `tags` remains the k-anonymous set that
-- cards, exports and AgentCards are built from.

ALTER TABLE general_profiles ADD COLUMN internal_tags JSON;
