# /test-and-ticket — Run Tests & Create Tickets for Failures

Run the stompy-ticketing test suite and auto-create Stompy tickets for any failures.

## Steps

1. **Run the test suite** (pass `project="stompy"` on all Stompy tool calls):
```bash
cd ~/Sites/stompy/stompy-ticketing && python3 -m pytest tests/ -v --tb=short 2>&1
```

2. **Parse the output**:
   - If ALL tests pass: report the count and exit
   - If any tests FAIL: proceed to create tickets

3. **Check for duplicates** before creating tickets:
```python
mcp__stompy__ticket_search(query="test failure ticketing", project="stompy")
```

4. **For each failure**, create a Stompy ticket:
```python
mcp__stompy__ticket(
    action="create",
    type="bug",
    priority="P3",
    title="Test failure: <test_file>::<test_name>",
    description="Test `<test_name>` in `<test_file>` failed.\n\nError:\n```\n<traceback>\n```\n\nReproduce: `cd ~/Sites/stompy/stompy-ticketing && python3 -m pytest <test_path> -v`",
    tags="bug,test-failure,ticketing,automated",
    project="stompy"
)
```

5. **Skip** if a ticket with the same test name already exists.

6. **Show the board** after creating any bug tickets:
```python
mcp__stompy__ticket_board(view="summary", project="stompy")
```

7. **Report**: total tests, passed, failed, tickets created.
