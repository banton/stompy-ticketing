Run a full TDD RED-GREEN-REFACTOR cycle for ticket $ARGUMENTS.

Steps:

1. **Claim ticket**: `mcp__stompy__ticket(action="move", id=$ARGUMENTS, status="in_progress")`
2. **Read ticket details**: `mcp__stompy__ticket(action="get", id=$ARGUMENTS)`
3. **RED phase** — Write failing tests first:
   - Read the relevant test file(s) in `tests/`
   - Write new test cases following the project pattern:
     - Use `FIXED_TIME = 1700000000.0`
     - Use `_make_ticket_row()` helper for mock data
     - Use `_mock_conn_and_cursor()` for DB mocking
     - Name tests: `test_should_[behavior]_when_[condition]()`
   - Run: `python3 -m pytest tests/ -v --tb=short`
   - Confirm tests FAIL (RED)

4. **GREEN phase** — Write minimal code to pass:
   - Edit only the files needed to make tests pass
   - Run: `python3 -m pytest tests/ -v --tb=short`
   - Confirm ALL tests PASS (GREEN)

5. **REFACTOR phase** — Improve without breaking:
   - Clean up code if needed
   - Run: `python3 -m pytest tests/ -v --tb=short`
   - Confirm tests still PASS

6. **Complete ticket**: `mcp__stompy__ticket(action="move", id=$ARGUMENTS, status="done")`
7. **Report**: Show test count before/after and which files changed.
