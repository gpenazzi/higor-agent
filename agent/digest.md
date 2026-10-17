# Higor weekly digest

Build the researcher's literature digest. Follow the `higor` skill: load it first if it is
not already loaded, and use its commands, scoring rubric and summary style.

- Window: if a number of days was given with this command, use it. Otherwise use the
  profile's lookback window, which `higor-agent context` reports.
- Size: show as many papers as the profile's maximum per week.

Run `higor-agent context`, then collect all the candidates, paging until done. Read the
titles, read the abstracts of the promising ones, rate any unrated venues, and `show` the
best papers with scores and summaries. Then give a short overview of the week.
