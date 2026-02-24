# stompy-ticketing Development Guide

## Project Overview

Native ticketing system for Stompy MCP. Replaces Linear's 33 tools (~5K tokens) with 4 MCP tools (~800 tokens) — 84% context reduction.

**Tech stack**: Python 3.11+, Pydantic, FastAPI, psycopg2, pytest
**Package**: `stompy_ticketing`
**Test runner**: `python3 -m pytest tests/ -v`

## Self-Dogfooding Protocol

This project uses its own ticketing system for development orchestration.

### Session Start
```python
# Pass project="stompy_ticketing" on each tool call rather than using project_switch
mcp__stompy__recall_context("ticketing_architecture_overview", project="stompy_ticketing")
```

### Claiming Work
```python
# View board
mcp__stompy__ticket_board(view="summary")

# Move ticket to in_progress when starting
mcp__stompy__ticket(action="move", id=<ticket_id>, status="in_progress")
```

### Reporting Bugs
```python
mcp__stompy__ticket(action="create", type="bug", title="...", description="...")
```

### Completing Work
```python
mcp__stompy__ticket(action="move", id=<ticket_id>, status="done")
```

## File Ownership

| Role | Files | Access |
|------|-------|--------|
| tester | `tests/` | read-write |
| implementer | `stompy_ticketing/service.py`, `stompy_ticketing/models.py` | read-write |
| integrator | `stompy_ticketing/plugin.py`, `stompy_ticketing/mcp_tools.py`, `stompy_ticketing/api_routes.py`, `stompy_ticketing/migrations.py` | read-write |
| reviewer | all files | read-only (creates bug/decision tickets) |

## File Structure

```
stompy-ticketing/
  stompy_ticketing/
    __init__.py          # Package init
    models.py            # Pydantic request/response models
    service.py           # TicketService + state machine logic
    schema.py            # DDL SQL functions with {schema} placeholders
    mcp_tools.py         # 4 MCP tool definitions
    api_routes.py        # FastAPI REST router (10 endpoints)
    migrations.py        # Migration definitions (5 migrations, start_id=26)
    plugin.py            # register_plugin() entry point
  tests/
    __init__.py
    test_state_machine.py  # State machine transition tests
    test_service.py        # TicketService unit tests (mock DB)
    test_api.py            # API route tests
```

## State Machine Reference

### task
```
backlog -> in_progress -> done
                       -> cancelled
backlog -> cancelled
```
Initial: `backlog` | Terminal: `done`, `cancelled`

### bug
```
triage -> confirmed -> in_progress -> resolved
                                   -> wont_fix
triage -> wont_fix
```
Initial: `triage` | Terminal: `resolved`, `wont_fix`

### feature
```
proposed -> approved -> in_progress -> shipped
                                    -> rejected
proposed -> rejected
```
Initial: `proposed` | Terminal: `shipped`, `rejected`

### decision
```
open -> decided
open -> deferred -> open (reopen)
```
Initial: `open` | Terminal: `decided`, `deferred`

## TDD Rules

### Test Constants
```python
FIXED_TIME = 1700000000.0   # Always use this, never time.time()
SCHEMA = "test_project"      # Always use this schema name
```

### Test Helpers
```python
_make_ticket_row(id=1, title="Test ticket", ...)  # Create mock DB rows
_mock_conn_and_cursor()                              # Mock DB connection
```

### Test Pattern
```python
def test_should_[expected_behavior]_when_[condition]():
    # Arrange - fixed data, mock DB
    conn, cursor = _mock_conn_and_cursor()
    cursor.fetchone.return_value = _make_ticket_row(status="backlog")

    # Act
    svc = TicketService(conn, SCHEMA)
    result = svc.transition_ticket(1, "in_progress")

    # Assert
    assert result["status"] == "in_progress"
```

### Immutable Test Rules
- No `datetime.now()` or `time.time()` — use `FIXED_TIME`
- No shared mutable fixtures — each test creates its own data
- No random values — all test data is deterministic
- Mock all DB calls via `_mock_conn_and_cursor`

## Integration Contract

### Plugin Registration
```python
from stompy_ticketing.plugin import register_plugin

result = register_plugin(
    mcp_instance=mcp,           # FastMCP server
    api_router=api_router,       # FastAPI APIRouter
    get_db_func=get_db,          # func(project=None) -> ctx mgr connection
    check_project_func=check,    # func(project=None) -> error str | None
    get_project_func=get_proj,   # func(project=None) -> project name str
    resolve_schema_func=resolve, # Optional: project -> schema name
)
# result = {"migrations": [...], "schema_sql_func": callable}
```

### REST Mount Path
Routes mount at `/projects/{name}/tickets`

### Migration Format
5 CUSTOM migrations starting at ID 26 (after Stompy's last migration 25).
Schema uses `{schema}` placeholder for multi-tenant project schemas.

## Commit Format

```
type(scope): subject

# types: feat, fix, test, refactor, docs, style, perf, chore
# scope: ticketing, service, api, models, tests, plugin, etc.
```

Examples:
- `test(mcp): add unit tests for ticket tool`
- `feat(service): add changed_by to transition history`
- `fix(api): handle missing ticket in GET endpoint`
