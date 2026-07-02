"""Run a subset of benchmark arms for one case at a given n.

Usage: run_subset.py <case_id> <n_trials> <arm_key> [<arm_key> ...]

Filters the SCENARIOS registry in place to just the requested arms, then
delegates to case_runner.run_case. The wave logic, summary.json, and
print_summary all cover only the selected arms.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import case_runner  # noqa: E402  (path wiring lives inside case_runner)


def main():
    args = sys.argv[1:]
    all_keys = [k for k, _, _ in case_runner.SCENARIOS]
    if len(args) < 3:
        print("usage: run_subset.py <case_id> <n_trials> <arm_key> [<arm_key> ...]")
        print(f"  known arms: {all_keys}")
        sys.exit(2)

    case_id = args[0]
    n = int(args[1])
    wanted = args[2:]

    unknown = [k for k in wanted if k not in all_keys]
    if unknown:
        print(f"unknown arm(s): {unknown}")
        print(f"  known arms: {all_keys}")
        sys.exit(2)

    wanted_set = set(wanted)
    filtered = [s for s in case_runner.SCENARIOS if s[0] in wanted_set]
    # Mutate the shared list object in place so registry.make_runner,
    # case_runner, and the summary code all observe the filtered set.
    case_runner.SCENARIOS[:] = filtered
    print(f"running {case_id} at n={n} with arms: {[s[0] for s in filtered]}")
    case_runner.run_case(case_id, n)


if __name__ == "__main__":
    main()
