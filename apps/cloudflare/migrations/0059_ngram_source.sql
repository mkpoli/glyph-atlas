-- A run's page can be placed by book (`sort=source`) and its books counted from one range of the run in
-- book order. The style group is in the index, so narrowing the page to one is a test on the index
-- entry and reads no table row.
CREATE INDEX IF NOT EXISTS unit_ngram_source ON unit_ngrams(size,text,document,first,style_order,vertical);
