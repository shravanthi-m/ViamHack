import copy
import json
import tempfile
import unittest
from pathlib import Path

from runtime.compaction import (COMPACT_TRACK, TRACK, compact_episode, compact_track,
                                deviation_deg, latest_episode, simplify, step_deg, verify)
from runtime.demonstrations import PHASES, write

POSE = dict(x=10, y=20, z=100, o_x=1, o_y=0, o_z=0, theta=90)


def waypoint(t, phase, joints):
    pose = dict(POSE, x=10 + joints[0], z=100 + joints[1])
    return dict(t=t, phase=phase, attempt='a1', timestamp='2026-09-19T15:00:00+00:00',
                completed_at='2026-09-19T15:00:00+00:00', pose=pose, joints=list(joints))


def track(rows):
    return dict(format='joint-track/1', name='coconut_water', arm='arm', units='degrees',
                frame='world', joint_count=6, waypoint_count=len(rows),
                duration_s=rows[-1]['t'], stats={}, waypoints=rows)


def straight_phase(phase, start_t, count=10, per_step=1.0, base=0.0):
    """A constant-velocity ramp on joint 0: geometrically redundant between endpoints."""
    return [waypoint(start_t + i * 0.2, phase, [base + i * per_step, 0, 0, 0, 0, 0])
            for i in range(count)]


def demo_track(**kwargs):
    rows, t, base = [], 0.0, 0.0
    for phase in PHASES:
        block = straight_phase(phase, t, base=base, **kwargs)
        rows += block
        t = block[-1]['t'] + 1.0
        base = block[-1]['joints'][0]
    return track(rows)


class Geometry(unittest.TestCase):
    def test_straight_run_collapses_to_its_endpoints(self):
        rows = straight_phase('lift', 0)
        self.assertEqual(simplify(rows, tol_deg=1.0, budget_deg=20), [0, len(rows) - 1])

    def test_a_corner_is_kept(self):
        rows = straight_phase('lift', 0, count=5)
        rows += [waypoint(1.0 + 0.2 * i, 'lift', [4 - i, 0, 0, 0, 0, 0]) for i in range(1, 5)]
        self.assertIn(4, simplify(rows, tol_deg=1.0, budget_deg=20))

    def test_step_budget_splits_a_long_straight_chord(self):
        rows = straight_phase('lift', 0, count=21, per_step=1.0)
        kept = simplify(rows, tol_deg=1.0, budget_deg=5)
        self.assertTrue(all(step_deg(rows[a], rows[b]) <= 5 for a, b in zip(kept, kept[1:])))

    def test_deviation_measures_the_worst_joint_against_the_chord(self):
        a, b = waypoint(0, 'lift', [0] * 6), waypoint(1, 'lift', [10, 0, 0, 0, 0, 0])
        self.assertAlmostEqual(deviation_deg(a, b, waypoint(0.5, 'lift', [5, 0, 0, 0, 0, 0])), 0)
        self.assertAlmostEqual(deviation_deg(a, b, waypoint(0.5, 'lift', [5, 3, 0, 0, 0, 0])), 3)


class Compaction(unittest.TestCase):
    def test_output_is_a_subsequence_of_the_taught_waypoints(self):
        original = demo_track()
        compact, report = compact_track(original)
        verify(original, compact)
        self.assertLess(report['after']['waypoints'], report['before']['waypoints'])
        taught = [(row['joints'], row['phase']) for row in original['waypoints']]
        for row in compact['waypoints']:
            self.assertIn((row['joints'], row['phase']), taught)

    def test_created_steps_stay_within_the_budget(self):
        compact, report = compact_track(demo_track(count=40), budget_deg=6)
        self.assertLessEqual(report['worst_created_step_deg'], 6)
        self.assertTrue(compact['compaction']['subsequence_of_taught_waypoints'])

    def test_every_phase_survives_in_order(self):
        compact, _ = compact_track(demo_track())
        seen = [row['phase'] for row in compact['waypoints']]
        self.assertEqual([p for i, p in enumerate(seen) if i == 0 or seen[i - 1] != p], list(PHASES))

    def test_release_waypoint_and_envelope_are_preserved(self):
        original = demo_track()
        compact, _ = compact_track(original)
        taught_release = next(r for r in reversed(original['waypoints']) if r['phase'] == 'place')
        self.assertEqual(next(r for r in reversed(compact['waypoints'])
                              if r['phase'] == 'place')['pose'], taught_release['pose'])
        self.assertEqual(compact['waypoint_count'], len(compact['waypoints']))
        self.assertEqual(compact['duration_s'], compact['waypoints'][-1]['t'])
        for key in ('format', 'arm', 'units', 'frame', 'joint_count'):
            self.assertEqual(compact[key], original[key])

    def test_taught_time_is_kept_and_inspectable(self):
        compact, _ = compact_track(demo_track())
        for row in compact['waypoints']:
            self.assertIn('source_t', row)
            self.assertIn('source_index', row)
        times = [row['t'] for row in compact['waypoints']]
        self.assertEqual(times, sorted(times))


class Dwell(unittest.TestCase):
    def hold_block(self, phase, start_t, base, dwell, samples=20):
        """Motion, a long stationary hold, then motion again, inside one phase."""
        rows = straight_phase(phase, start_t, count=5, base=base)
        held, t = list(rows[-1]['joints']), rows[-1]['t']
        rows += [waypoint(t + dwell * i / samples, phase, list(held)) for i in range(1, samples + 1)]
        t = rows[-1]['t']
        rows += [waypoint(t + 0.2 * i, phase, [held[0] + i, 0, 0, 0, 0, 0]) for i in range(1, 5)]
        return rows

    def still_track(self, phase='lift', dwell=10.0):
        rows, t, base = [], 0.0, 0.0
        for name in PHASES:
            block = (self.hold_block(name, t, base, dwell) if name == phase
                     else straight_phase(name, t, count=4, base=base))
            rows += block
            t = block[-1]['t'] + 1.0
            base = block[-1]['joints'][0]
        return track(rows)

    def moving_seconds(self, original, phase):
        return sum(b['t'] - a['t'] for a, b in zip(original['waypoints'], original['waypoints'][1:])
                   if a['phase'] == b['phase'] == phase and step_deg(a, b) >= 0.1)

    def test_a_hesitation_is_capped_not_the_motion_around_it(self):
        original = self.still_track()
        compact, report = compact_track(original, max_dwell_s=0.25)
        self.assertGreater(report['idle_seconds_removed'], 9)
        lift = [row for row in compact['waypoints'] if row['phase'] == 'lift']
        self.assertAlmostEqual(lift[-1]['t'] - lift[0]['t'],
                               self.moving_seconds(original, 'lift') + 0.25, places=6)

    def test_pour_keeps_its_taught_timing(self):
        original = self.still_track(phase='pour')
        compact, _ = compact_track(original, hold_phases=('pour',))
        pour = [row for row in compact['waypoints'] if row['phase'] == 'pour']
        taught = [row for row in original['waypoints'] if row['phase'] == 'pour']
        self.assertAlmostEqual(pour[-1]['t'] - pour[0]['t'], taught[-1]['t'] - taught[0]['t'], places=6)

    def test_pour_dwell_is_capped_when_it_is_not_held(self):
        original = self.still_track(phase='pour')
        compact, _ = compact_track(original, hold_phases=())
        pour = [row for row in compact['waypoints'] if row['phase'] == 'pour']
        self.assertAlmostEqual(pour[-1]['t'] - pour[0]['t'],
                               self.moving_seconds(original, 'pour') + 0.25, places=6)


class Blocking(unittest.TestCase):
    def over_limit_track(self):
        original = demo_track()
        rows = original['waypoints']
        index = next(i for i, row in enumerate(rows) if row['phase'] == 'transport_back')
        for row in rows[index + 1:]:
            row['joints'][1] += 25.0          # An adjacent pair no subsequence can subdivide.
        return original

    def test_an_adjacent_over_limit_pair_is_reported(self):
        _, report = compact_track(self.over_limit_track())
        self.assertEqual(len(report['carried']), 1)
        self.assertEqual(report['carried'][0]['phase'], 'transport_back')
        self.assertAlmostEqual(report['carried'][0]['step_deg'], 25.0)

    def test_verify_rejects_a_track_that_still_exceeds_the_limit(self):
        original = self.over_limit_track()
        compact, _ = compact_track(original)
        with self.assertRaises(ValueError):
            verify(original, compact)

    def test_verify_rejects_an_invented_waypoint(self):
        original = demo_track()
        compact, _ = compact_track(original)
        compact['waypoints'][2]['joints'] = [99.0] * 6
        with self.assertRaises(ValueError):
            verify(original, compact)

    def test_pose_track_episodes_are_refused(self):
        original = demo_track()
        original['format'] = 'pose-track/1'
        with self.assertRaises(ValueError):
            compact_track(original)


class Episodes(unittest.TestCase):
    def episode(self, root, skill, original, success=True):
        path = Path(root) / skill / 'episode_20260919T150501_7cc56960'
        path.mkdir(parents=True)
        write(path / TRACK, original)
        write(path / 'metadata.json', dict(object=skill, status='complete', success=success))
        return path

    def test_writes_a_sidecar_and_leaves_the_taught_track_untouched(self):
        with tempfile.TemporaryDirectory() as root:
            original = demo_track()
            path = self.episode(root, 'coconut_water', original)
            before = (path / TRACK).read_text()
            report = compact_episode(path)
            self.assertIsNone(report['blocked'])
            self.assertTrue((path / COMPACT_TRACK).exists())
            self.assertEqual((path / TRACK).read_text(), before)
            self.assertLess(report['bytes']['after'], report['bytes']['before'])
            saved = json.loads((path / COMPACT_TRACK).read_text())
            self.assertEqual(saved['compaction']['derived_from'], TRACK)
            verify(original, saved)

    def test_a_blocked_episode_writes_nothing(self):
        with tempfile.TemporaryDirectory() as root:
            original = Blocking().over_limit_track()
            path = self.episode(root, 'pitcher', original)
            report = compact_episode(path)
            self.assertIn('Re-record', report['blocked'])
            self.assertFalse((path / COMPACT_TRACK).exists())

    def test_latest_episode_skips_failed_recordings(self):
        with tempfile.TemporaryDirectory() as root:
            path = self.episode(root, 'coconut_water', demo_track())
            self.assertEqual(latest_episode(root, 'coconut_water'), path)
            write(path / 'metadata.json', dict(object='coconut_water', status='complete', success=False))
            with self.assertRaises(ValueError):
                latest_episode(root, 'coconut_water')


if __name__ == '__main__':
    unittest.main()


class ReplayContract(unittest.IsolatedAsyncioTestCase):
    """A compacted track must still satisfy everything replay validates."""

    def setUp(self):
        from tests.test_teach_replay import config
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = config()

    async def test_compacted_episode_loads_and_adapts(self):
        from runtime.replay import adapted_plan, load_episode
        from runtime.teaching import teach_skill
        from tests.test_teach_replay import FakeIO, yes
        await teach_skill('coconut_water', FakeIO(), self.config, root=self.root, ask_fn=yes)
        path, _, taught = load_episode(self.root, 'coconut_water', self.config)
        report = compact_episode(path)
        self.assertIsNone(report['blocked'])
        _, meta, compact = load_episode(self.root, 'coconut_water', self.config,
                                        track_name=COMPACT_TRACK)
        self.assertLessEqual(compact['waypoint_count'], taught['waypoint_count'])
        plan = adapted_plan(meta, compact, dict(dx=1, dy=1, dz=0), self.config)
        self.assertEqual(len(plan['waypoints']), compact['waypoint_count'])
