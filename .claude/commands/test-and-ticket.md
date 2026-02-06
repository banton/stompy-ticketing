Run the full test suite and auto-create bug tickets for any failures.

Steps:

1. Switch to project: `mcp__stompy__project_switch(name="stompy_ticketing")`
2. Run the full test suite: `python3 -m pytest tests/ -v --tb=short 2>&1`
3. Parse the output:
   - If ALL tests pass: report the count and exit
   - If any tests FAIL: for each failure, create a bug ticket:
     ```
     mcp__stompy__ticket(
       action="create",
       type="bug",
       priority="high",
       title="Test failure: <test_name>",
       description="Test `<test_name>` in `<test_file>` failed.\n\nError:\n```\n<traceback>\n```"
     )
     ```
4. Show the board summary after creating any bug tickets.
5. Report: total tests, passed, failed, bug tickets created.
