-- Characters found by their components (水骨 finds 滑). `han_components` lists every component of every
-- character with an Ideographic Description Sequence, through every level and under each name the
-- component goes by; `n` counts it, `direct` marks one the sequence names at its top level, `tier` is 0
-- for the CJK Unified Ideographs block, 1 for the rest of the BMP and 2 beyond it, and `size` is how
-- many components the character is built from. The key serves both reads: one component's characters,
-- everyday and simple ones first, and whether a given character holds another component.
-- `han_component_names` resolves what a person types to the component it is indexed under (⺡ is 氵)
-- and says how many characters hold it, so a search starts from the rarest.
-- Both are written whole by scripts/export_components_cloudflare.py.
CREATE TABLE IF NOT EXISTS han_components (
 component TEXT NOT NULL, tier INTEGER NOT NULL, size INTEGER NOT NULL, code_point TEXT NOT NULL,
 n INTEGER NOT NULL, direct INTEGER NOT NULL,
 PRIMARY KEY (component,tier,size,code_point)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS han_component_names (
 query TEXT PRIMARY KEY, component TEXT NOT NULL, total INTEGER NOT NULL
) WITHOUT ROWID;
