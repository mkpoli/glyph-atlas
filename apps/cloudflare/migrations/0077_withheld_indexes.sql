-- A crop the alignment repair withheld (`quiz=0`) stays published and tagged; galleries leave it out
-- unless asked. Its counts are read from these partial indexes and taken off the counts the style
-- indexes give, so leaving withheld crops out reads no crop. Only withheld crops are indexed.
CREATE INDEX IF NOT EXISTS unit_character_withheld ON units(origin,character,style_order) WHERE quiz=0;
CREATE INDEX IF NOT EXISTS unit_family_withheld ON units(origin,family,style_order) WHERE quiz=0;
