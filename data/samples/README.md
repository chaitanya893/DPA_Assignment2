# data/samples/

Small committed samples. Large artefacts stay out of Git (`data/raw/` holds the raw source bytes
written by the pipeline and is git-ignored).

- `synthetic_distribution_table.html` - a SYNTHETIC distribution table (not real fund data) in the
  layout the HTML parser expects. Useful to try the parser by hand:

  ```python
  from pathlib import Path
  from src.parsers.html_table_parser import parse_html_distribution_tables
  html = Path("data/samples/synthetic_distribution_table.html").read_text()
  print(parse_html_distribution_tables(html, "CA_BMO_ZCN", "CA", "sample"))
  ```
