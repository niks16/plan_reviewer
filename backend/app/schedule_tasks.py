"""Schedule a task list in as few days as possible and check it against the deadline.

The problem is resource-constrained project scheduling: each task has a
duration, waits for its dependencies to end, and two tasks with the same
non-null owner never overlap (one person does one task at a time). Tasks with
owner null have no person limit. The goal is the smallest overall end_day.

How the answer is found, and why it can be trusted:

1. The request is validated; bad input raises ScheduleInputError (the API
   turns that into a 4xx).
2. Circular dependencies are found with a depth-first search and the ids on
   one circle are returned (status "cycle").
3. A greedy list schedule (longest remaining path first) gives a valid
   schedule immediately.
4. OR-Tools CP-SAT then searches for the shortest schedule, seeded with the
   greedy one, within a time limit well inside the 10 second budget.
5. A lower bound (longest dependency chain, or one owner's total workload
   plus the unavoidable time before and after it) is computed independently.
   When the schedule's end_day equals the bound, or CP-SAT proves optimality,
   the response says "optimal": true.
6. The final schedule is re-checked against every rule before it is returned.

Extra response fields ("optimal", "lower_bound", "solver") are allowed by the
contract and tell the reader how strong the answer is.

Usage:
    python -m app.schedule_tasks <tasks.json>      (from backend/)
"""

import json
import sys
from pathlib import Path

MAX_TASKS = 60
SOLVER_TIME_LIMIT_SECONDS = 7.0


class ScheduleInputError(ValueError):
    """The request is not a valid scheduling request."""


# --------------------------------------------------------------------------- validation


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def validate(tasks, deadline_day):
    """Raise ScheduleInputError with a clear message if the request is invalid."""
    if not isinstance(tasks, list):
        raise ScheduleInputError("'tasks' must be a list.")
    if len(tasks) > MAX_TASKS:
        raise ScheduleInputError(f"At most {MAX_TASKS} tasks can be scheduled; got {len(tasks)}.")
    if not _is_int(deadline_day):
        raise ScheduleInputError("'deadline_day' must be an integer.")

    ids = set()
    for i, t in enumerate(tasks):
        where = f"tasks[{i}]"
        if not isinstance(t, dict):
            raise ScheduleInputError(f"{where} must be an object.")
        tid = t.get("id")
        if not isinstance(tid, str) or not tid:
            raise ScheduleInputError(f"{where}.id must be a non-empty string.")
        if tid in ids:
            raise ScheduleInputError(f"Duplicate task id {tid!r}.")
        ids.add(tid)
        if not isinstance(t.get("name"), str):
            raise ScheduleInputError(f"Task {tid!r}: 'name' must be a string.")
        if "owner" not in t or not (t["owner"] is None or isinstance(t["owner"], str)):
            raise ScheduleInputError(f"Task {tid!r}: 'owner' must be a string or null.")
        d = t.get("duration_days")
        if not _is_int(d) or d < 1:
            raise ScheduleInputError(f"Task {tid!r}: 'duration_days' must be an integer of at least 1.")
        deps = t.get("depends_on")
        if not isinstance(deps, list) or not all(isinstance(x, str) for x in deps):
            raise ScheduleInputError(f"Task {tid!r}: 'depends_on' must be a list of task ids.")

    for t in tasks:
        for dep in t["depends_on"]:
            if dep not in ids:
                raise ScheduleInputError(f"Task {t['id']!r} depends on unknown task {dep!r}.")


# --------------------------------------------------------------------------- cycles


def find_cycle(tasks):
    """Return the ids on one circular dependency, or [] if there is none."""
    deps = {t["id"]: list(dict.fromkeys(t["depends_on"])) for t in tasks}
    WHITE, GREY, BLACK = 0, 1, 2
    colour = {tid: WHITE for tid in deps}
    for root in deps:
        if colour[root] != WHITE:
            continue
        # Iterative DFS: the stack holds (node, iterator over its dependencies).
        path = [root]
        stack = [iter(deps[root])]
        colour[root] = GREY
        while stack:
            nxt = next(stack[-1], None)
            if nxt is None:
                colour[path.pop()] = BLACK
                stack.pop()
            elif colour[nxt] == GREY:
                return path[path.index(nxt):]
            elif colour[nxt] == WHITE:
                colour[nxt] = GREY
                path.append(nxt)
                stack.append(iter(deps[nxt]))
    return []


def _topological_order(tasks):
    deps = {t["id"]: set(t["depends_on"]) for t in tasks}
    order, done = [], set()
    remaining = [t["id"] for t in tasks]
    while remaining:
        ready = [tid for tid in remaining if deps[tid] <= done]
        order += ready
        done.update(ready)
        remaining = [tid for tid in remaining if tid not in done]
    return order


# --------------------------------------------------------------------------- bounds and greedy


def _heads_and_tails(tasks, order):
    """head: earliest possible start from dependencies alone.
    tail: longest chain from a task's start to the end (its own duration included)."""
    by_id = {t["id"]: t for t in tasks}
    successors = {tid: [] for tid in by_id}
    for t in tasks:
        for dep in t["depends_on"]:
            successors[dep].append(t["id"])
    head = {}
    for tid in order:
        head[tid] = max((head[d] + by_id[d]["duration_days"] for d in by_id[tid]["depends_on"]), default=0)
    tail = {}
    for tid in reversed(order):
        tail[tid] = by_id[tid]["duration_days"] + max((tail[s] for s in successors[tid]), default=0)
    return head, tail


def lower_bound(tasks, head, tail):
    """No schedule can end before this day."""
    bound = max((head[t["id"]] + tail[t["id"]] for t in tasks), default=0)
    by_owner = {}
    for t in tasks:
        if t["owner"] is not None:
            by_owner.setdefault(t["owner"], []).append(t)
    for group in by_owner.values():
        # The owner cannot start before the earliest head, works through every
        # task one at a time, and after the last one the shortest tail remains.
        load = sum(t["duration_days"] for t in group)
        first = min(head[t["id"]] for t in group)
        after = min(tail[t["id"]] - t["duration_days"] for t in group)
        bound = max(bound, first + load + after)
    return bound


def greedy_schedule(tasks, tail):
    """Serial list scheduling: repeatedly place the ready task with the longest
    remaining chain at its earliest start that fits its owner's free time."""
    by_id = {t["id"]: t for t in tasks}
    end = {}
    start = {}
    busy = {}  # owner -> sorted list of (start, end)
    position = {t["id"]: i for i, t in enumerate(tasks)}

    while len(start) < len(tasks):
        ready = [t for t in tasks if t["id"] not in start and all(d in end for d in t["depends_on"])]
        task = max(ready, key=lambda t: (tail[t["id"]], -position[t["id"]]))
        tid, d, owner = task["id"], task["duration_days"], task["owner"]
        s = max((end[x] for x in task["depends_on"]), default=0)
        if owner is not None:
            for b_start, b_end in busy.get(owner, []):
                if s + d <= b_start:
                    break
                s = max(s, b_end)
            busy.setdefault(owner, []).append((s, s + d))
            busy[owner].sort()
        start[tid], end[tid] = s, s + d

    return {tid: start[tid] for tid in by_id}


# --------------------------------------------------------------------------- CP-SAT


def solve_cp_sat(tasks, hint, bound, time_limit=SOLVER_TIME_LIMIT_SECONDS):
    """Return (starts, proven_optimal), or None if OR-Tools is unavailable or finds nothing."""
    try:
        from ortools.sat.python import cp_model
    except ImportError:
        return None

    horizon = sum(t["duration_days"] for t in tasks)
    model = cp_model.CpModel()
    starts, ends, intervals = {}, {}, {}
    for t in tasks:
        tid, d = t["id"], t["duration_days"]
        starts[tid] = model.NewIntVar(0, horizon - d, f"s_{tid}")
        ends[tid] = model.NewIntVar(d, horizon, f"e_{tid}")
        intervals[tid] = model.NewIntervalVar(starts[tid], d, ends[tid], f"i_{tid}")
        model.AddHint(starts[tid], hint[tid])

    for t in tasks:
        for dep in t["depends_on"]:
            model.Add(starts[t["id"]] >= ends[dep])

    by_owner = {}
    for t in tasks:
        if t["owner"] is not None:
            by_owner.setdefault(t["owner"], []).append(intervals[t["id"]])
    for group in by_owner.values():
        if len(group) > 1:
            model.AddNoOverlap(group)

    makespan = model.NewIntVar(bound, horizon, "makespan")
    model.AddMaxEquality(makespan, list(ends.values()))
    model.Minimize(makespan)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit
    solver.parameters.num_search_workers = 8
    status = solver.Solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None
    return {tid: solver.Value(v) for tid, v in starts.items()}, status == cp_model.OPTIMAL


# --------------------------------------------------------------------------- checking and output


def check_schedule(tasks, starts):
    """Return a list of rule violations (empty if the schedule is valid)."""
    errors = []
    by_id = {t["id"]: t for t in tasks}
    end = {tid: starts[tid] + by_id[tid]["duration_days"] for tid in by_id}
    for t in tasks:
        if starts[t["id"]] < 0:
            errors.append(f"{t['id']} starts before day 0")
        for dep in t["depends_on"]:
            if starts[t["id"]] < end[dep]:
                errors.append(f"{t['id']} starts before {dep} ends")
    for i, a in enumerate(tasks):
        for b in tasks[i + 1:]:
            if a["owner"] is not None and a["owner"] == b["owner"]:
                if starts[a["id"]] < end[b["id"]] and starts[b["id"]] < end[a["id"]]:
                    errors.append(f"{a['id']} and {b['id']} overlap for owner {a['owner']}")
    return errors


def schedule(tasks, deadline_day, time_limit=SOLVER_TIME_LIMIT_SECONDS):
    """Return the schedule response for a request (see docs/contracts.md).

    Raises ScheduleInputError for invalid input.
    """
    validate(tasks, deadline_day)

    cycle = find_cycle(tasks)
    if cycle:
        return {
            "status": "cycle",
            "schedule": [],
            "end_day": None,
            "deadline_day": deadline_day,
            "meets_deadline": None,
            "days_late": None,
            "cycle": cycle,
        }

    order = _topological_order(tasks)
    head, tail = _heads_and_tails(tasks, order)
    bound = lower_bound(tasks, head, tail)
    starts = greedy_schedule(tasks, tail)
    greedy_end = max((starts[t["id"]] + t["duration_days"] for t in tasks), default=0)
    solver_name, proven = "greedy", greedy_end == bound

    if not proven:
        solved = solve_cp_sat(tasks, starts, bound, time_limit)
        if solved is not None:
            cp_starts, cp_optimal = solved
            cp_end = max(cp_starts[t["id"]] + t["duration_days"] for t in tasks)
            if cp_end <= greedy_end and not check_schedule(tasks, cp_starts):
                starts, solver_name, proven = cp_starts, "cp-sat", cp_optimal or cp_end == bound

    errors = check_schedule(tasks, starts)
    if errors:  # Should never happen; refuse to return a wrong schedule silently.
        raise RuntimeError("Scheduler produced an invalid schedule: " + "; ".join(errors))

    items = [
        {"id": t["id"], "start_day": starts[t["id"]], "end_day": starts[t["id"]] + t["duration_days"]}
        for t in tasks
    ]
    end_day = max((i["end_day"] for i in items), default=0)
    return {
        "status": "scheduled",
        "schedule": items,
        "end_day": end_day,
        "deadline_day": deadline_day,
        "meets_deadline": end_day <= deadline_day,
        "days_late": max(0, end_day - deadline_day),
        "cycle": [],
        "optimal": proven,
        "lower_bound": bound,
        "solver": solver_name,
    }


def main(argv):
    if len(argv) not in (2, 3):
        print("usage: python -m app.schedule_tasks <tasks.json>", file=sys.stderr)
        return 2
    try:
        request = json.loads(Path(argv[1]).read_text())
        result = schedule(request.get("tasks"), request.get("deadline_day"))
    except (OSError, json.JSONDecodeError, AttributeError, ScheduleInputError) as e:
        print(e, file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    if len(argv) == 3:
            Path(argv[2]).write_text(json.dumps(result, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
