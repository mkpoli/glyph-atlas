-- The family list is read in order of size (`count DESC, code_point`) on every cache miss, which sorted
-- all of form_families each time. This index holds that order with the columns the list shows, so the
-- read walks it in order, touching no table row and sorting nothing.
CREATE INDEX IF NOT EXISTS form_family_order ON form_families(count DESC, code_point, char, label, cluster_count, assigned, rejected, revision);
