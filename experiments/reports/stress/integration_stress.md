# Integration stress suite — generated complex cases

**Verdict: ALL PASS** — 211/211 checks over 10 seeded iterations (176.7s, Scribble judging throughout).

Each iteration generates a fresh 4-7-role protocol with nested choices, then runs the five stages described in `experiments/scripts/integration_stress.py` (round-trip, mutation catch, critic oracle, revisor loop, incremental chain).

| check | pass rate |
|---|---|
| `S1.generated_protocol_scribble_valid` | 10/10 |
| `S1.local_types_recompose` | 10/10 |
| `S1.message_set_preserved` | 10/10 |
| `S1.recomposition_scribble_valid` | 10/10 |
| `S2.mutation_caught` | 10/10 |
| `S3.reversed_precedence_fails` | 10/10 |
| `S3.taint_reachable_flagged` | 8/8 |
| `S3.taint_unreachable_clean` | 3/3 |
| `S3.true_precedence_passes` | 10/10 |
| `S4.critic_fails_broken` | 10/10 |
| `S4.revisor_accepts_scripted_fix` | 10/10 |
| `S5.extension_0_blast_radius` | 10/10 |
| `S5.extension_0_child_cache_hit` | 10/10 |
| `S5.extension_0_monitor_bad` | 10/10 |
| `S5.extension_0_monitor_good` | 10/10 |
| `S5.extension_0_valid` | 10/10 |
| `S5.extension_1_blast_radius` | 10/10 |
| `S5.extension_1_child_cache_hit` | 10/10 |
| `S5.extension_1_monitor_bad` | 10/10 |
| `S5.extension_1_monitor_good` | 10/10 |
| `S5.extension_1_valid` | 10/10 |
| `gen.shape` | 10/10 |

