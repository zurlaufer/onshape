"""Assembly context: cache, index, and narrow queries.

Exists because the project brief's "~20 KB lightweight spatial graph" premise was
measured and falsified — a real FRC robot assembly is 1.09 MB, and even 83%
format-level reduction leaves 215 KB (~54k tokens). Fetching a small graph per
call is not possible; reducing *semantically* is.

Three ideas, each borrowed from a domain that solved the same problem:

  cache.py    microversion as a content hash        (git / rsync / HTTP ETags)
  graph.py    named tree + level of detail          (game engines, map tiles)
  queries.py  narrow answers, hard size ceiling     (databases, CAD agents)
"""
