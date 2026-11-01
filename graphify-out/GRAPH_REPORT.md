# Graph Report - AutoScalping  (2026-11-01)

## Corpus Check
- cluster-only mode — file stats not available

## Summary
- 80 nodes · 176 edges · 6 communities
- Extraction: 99% EXTRACTED · 1% INFERRED · 0% AMBIGUOUS · INFERRED: 2 edges (avg confidence: 0.85)
- Token cost: 28,809 input · 809 output

## Graph Freshness
- Built from commit: `cf70e9e7`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- Terminal & Git Utilities
- Backtest Process Management
- Report Rendering & Formatting
- EA Compilation & Parsing
- Report Generation & Charts
- MT5 Terminal Management

## God Nodes (most connected - your core abstractions)
1. `cmd_run()` - 28 edges
2. `build_report()` - 14 edges
3. `render_html()` - 10 edges
4. `parse_report()` - 8 edges
5. `cmd_report()` - 8 edges
6. `render_markdown()` - 7 edges
7. `compile_ea()` - 7 edges
8. `set_tester_tab_graph()` - 6 edges
9. `limit_processes()` - 6 edges
10. `fmt_pct()` - 6 edges

## Surprising Connections (you probably didn't know these)
- `main()` --indirect_call--> `cmd_run()`  [INFERRED]
  tools/backtest/nyao_backtest.py → tools/backtest/nyao_backtest.py  _Bridges community 0 → community 1_
- `main()` --indirect_call--> `cmd_report()`  [INFERRED]
  tools/backtest/nyao_backtest.py → tools/backtest/nyao_backtest.py  _Bridges community 1 → community 5_
- `cmd_run()` --calls--> `sys`  [EXTRACTED]
  tools/backtest/nyao_backtest.py →   _Bridges community 0 → community 5_
- `cmd_run()` --calls--> `build_report()`  [EXTRACTED]
  tools/backtest/nyao_backtest.py → tools/backtest/nyao_backtest.py  _Bridges community 0 → community 4_
- `cmd_run()` --calls--> `compile_ea()`  [EXTRACTED]
  tools/backtest/nyao_backtest.py → tools/backtest/nyao_backtest.py  _Bridges community 0 → community 3_

## Import Cycles
- None detected.

## Communities (6 total, 0 thin omitted)

### Community 0 - "Terminal & Git Utilities"
Cohesion: 0.13
Nodes (12): close_terminal(), cmd_run(), common_files(), git_info(), month_range(), next_month(), plain_number(), powershell() (+4 more)

### Community 1 - "Backtest Process Management"
Cohesion: 0.19
Nodes (5): default_cpu_mask(), _kernel32(), limit_processes(), main(), PROCESSENTRY32W

### Community 2 - "Report Rendering & Formatting"
Cohesion: 0.23
Nodes (12): cell_text(), ea_label(), fmt_pct(), fmt_pf(), render_html(), run_cell(), sign_cls(), render_markdown() (+4 more)

### Community 3 - "EA Compilation & Parsing"
Cohesion: 0.20
Nodes (5): compile_ea(), ea_version(), parse_report(), read_text_any(), safe()

### Community 4 - "Report Generation & Charts"
Cohesion: 0.20
Nodes (6): build_report(), clear_report(), equity_svg(), read_equity(), report_files(), save_equity()

### Community 5 - "MT5 Terminal Management"
Cohesion: 0.32
Nodes (5): cmd_report(), find_data_dir(), find_terminal(), save_plan(), terminal_candidates()

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `cmd_run()` connect `Terminal & Git Utilities` to `Backtest Process Management`, `EA Compilation & Parsing`, `Report Generation & Charts`, `MT5 Terminal Management`?**
  _High betweenness centrality (0.095) - this node is a cross-community bridge._
- **Should `Terminal & Git Utilities` be split into smaller, more focused modules?**
  _Cohesion score 0.13157894736842105 - nodes in this community are weakly interconnected._
- **Why does `parse_report()` connect `EA Compilation & Parsing` to `Backtest Process Management`, `Report Generation & Charts`?**
  _High betweenness centrality (0.076) - this node is a cross-community bridge._
- **Why does `render_html()` connect `Report Rendering & Formatting` to `Backtest Process Management`, `Report Generation & Charts`?**
  _High betweenness centrality (0.066) - this node is a cross-community bridge._