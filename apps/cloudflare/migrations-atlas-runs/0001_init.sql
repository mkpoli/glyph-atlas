CREATE TABLE runs(size INTEGER, graphemes TEXT, hand INTEGER, shuffle INTEGER, first INTEGER,
  second INTEGER, third INTEGER, book INTEGER,
  PRIMARY KEY(size, graphemes, hand, shuffle, first)) WITHOUT ROWID;
CREATE INDEX run_book ON runs(size, graphemes, book, first);
CREATE INDEX run_first ON runs(first);
CREATE INDEX run_second ON runs(second) WHERE size = 2;
CREATE TABLE run_stats(size INTEGER, graphemes TEXT, n INTEGER, down INTEGER,
  PRIMARY KEY(size, graphemes)) WITHOUT ROWID;
CREATE INDEX run_rank ON run_stats(size, n);
