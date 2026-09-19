import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from runtime.compaction import compact_episode, compact_track, verify
from runtime.demonstrations import PHASES, write
from runtime.reference import TRACE, pair_scene, reference_trace, write_reference
from tests.test_compaction import Blocking, demo_track

START = datetime(2026, 9, 19, 15, 0, 0, tzinfo=timezone.utc)


def stamp(offset):
    return (START + timedelta(seconds=offset)).isoformat()


def taught_track():
    """demo_track with the absolute timestamps teaching records beside every sample."""
    original = demo_track()
    for row in original['waypoints']:
        row['timestamp'] = stamp(row['t'])
        row['completed_at'] = stamp(row['t'] + 0.02)
    return original


def frame(phase, offset, *, attempt='a1', pose=None, joints=None):
    state = dict(timestamp=stamp(offset), completed_at=stamp(offset + 0.05),
                 pose=pose or dict(x=10, y=20, z=100, o_x=1, o_y=0, o_z=0, theta=90),
                 joints=joints or [0.0] * 6)
    images = {view: dict(view=view, camera=view, captured_at=stamp(offset),
                         images=[dict(path=f'frames/{phase}/{attempt}/{view}_{offset}.jpg')])
              for view in ('wrist', 'overhead')}
    # The frame log runs on its own clock; only the absolute stamps line up.
    return dict(phase=phase, attempt=attempt, t=offset + 5.8, completed_t=offset + 5.9,
                observation=dict(timestamp=stamp(offset), frames=images, state=state))


def metadata(**overrides):
    base = dict(object='coconut_water', status='complete', success=True, grasp_success=True,
                grasp_type='side', station='station', validation_status='candidate',
                gripper_force_percent=10.0,
                gripper_force_evidence=dict(source='operator_entered', hardware_verified=False),
                pregrasp_pose={}, grasp_pose={}, place_pose={},
                observations=dict(before=dict(timestamp=stamp(0), frames={})))
    base.update(overrides)
    return base


class Pairing(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)

    def episode(self, frames, track=None):
        original = track or taught_track()
        write(self.path / 'trajectory.json', original)
        write(self.path / 'metadata.json', metadata())
        (self.path / 'frames.jsonl').write_text(''.join(json.dumps(f) + '\n' for f in frames))
        return original

    def test_scenes_are_joined_on_absolute_time_not_the_frame_clock(self):
        lift_rows = [row for row in taught_track()['waypoints'] if row['phase'] == 'lift']
        original = self.episode([frame('lift', row['t']) for row in lift_rows])
        trace = reference_trace(self.path, metadata(), original)
        lift = [entry for entry in trace['waypoints'] if entry['phase'] == 'lift']
        self.assertTrue(all(entry['scene'] for entry in lift))
        self.assertLess(max(abs(entry['scene']['offset_s']) for entry in lift), 0.01)

    def test_a_distant_scene_is_left_unpaired(self):
        original = self.episode([frame('lift', 500.0)])
        trace = reference_trace(self.path, metadata(), original, max_pair_age_s=1.0)
        self.assertTrue(all(entry['scene'] is None for entry in trace['waypoints']))
        self.assertEqual(trace['scene_pairing']['paired'], 0)

    def test_scenes_do_not_cross_phase_or_attempt(self):
        row = taught_track()['waypoints'][0]      # lift, attempt a1
        captured = [dict(phase='transport', attempt='a1', captured_at=stamp(0),
                         at=START.timestamp(), state={}, frames={}),
                    dict(phase='lift', attempt='other', captured_at=stamp(0),
                         at=START.timestamp(), state={}, frames={})]
        self.assertIsNone(pair_scene(row, captured, 1.0))

    def test_pairing_reports_how_far_the_arm_had_moved(self):
        original = taught_track()
        row = original['waypoints'][3]
        moved = dict(row['pose'], x=row['pose']['x'] + 3.0)
        captured = [dict(phase=row['phase'], attempt=row['attempt'], captured_at=row['timestamp'],
                         at=datetime.fromisoformat(row['timestamp']).timestamp(),
                         state=dict(pose=moved, joints=[j + 0.5 for j in row['joints']]), frames={})]
        pairing = pair_scene(row, captured, 1.0)
        self.assertAlmostEqual(pairing['pose_error_mm'], 3.0, places=2)
        self.assertAlmostEqual(pairing['joint_error_deg'], 0.5, places=3)

    def test_trace_is_advisory_and_states_its_limits(self):
        original = self.episode([frame('lift', 0.0)])
        trace = reference_trace(self.path, metadata(), original)
        self.assertTrue(trace['advisory'])
        self.assertEqual(trace['schema'], 'reference-trace/1')
        joined = ' '.join(trace['unverified'])
        self.assertIn('localization is unimplemented', joined)
        self.assertIn('hardware_verified false', joined)
        self.assertIn('candidate', joined)

    def test_every_phase_is_summarised_with_its_intent(self):
        original = self.episode([frame('lift', 0.0)])
        trace = reference_trace(self.path, metadata(), original)
        self.assertEqual([phase['phase'] for phase in trace['phases']], list(PHASES))
        self.assertTrue(all(phase['intent'] for phase in trace['phases']))
        self.assertTrue(all(phase['taught_motion'] for phase in trace['phases']))

    def test_a_phase_that_teaches_no_motion_is_flagged(self):
        original = taught_track()
        held = original['waypoints'][0]['joints']
        for row in original['waypoints']:
            if row['phase'] == 'place':
                row['joints'] = list(held)
        self.episode([], track=original)
        trace = reference_trace(self.path, metadata(), original)
        place = next(phase for phase in trace['phases'] if phase['phase'] == 'place')
        self.assertFalse(place['taught_motion'])

    def test_write_reference_saves_the_trace(self):
        self.episode([frame('lift', 0.0)])
        trace = write_reference(self.path, 'trajectory.json')
        saved = json.loads((self.path / TRACE).read_text())
        self.assertEqual(saved['schema'], trace['schema'])
        self.assertEqual(len(saved['waypoints']), len(trace['waypoints']))


class PoseTrack(unittest.TestCase):
    def test_emitting_pose_track_changes_only_the_declared_format(self):
        original = demo_track()
        compact, report = compact_track(original, emit='pose-track/1')
        self.assertEqual(compact['format'], 'pose-track/1')
        self.assertEqual(report['emit'], 'pose-track/1')
        joints, _ = compact_track(original)
        self.assertEqual([row['joints'] for row in compact['waypoints']],
                         [row['joints'] for row in joints['waypoints']])

    def test_pose_track_does_not_apply_the_joint_step_limit(self):
        original = Blocking().over_limit_track()
        compact, _ = compact_track(original, emit='pose-track/1')
        verify(original, compact)

    def test_an_over_limit_pose_track_warns_instead_of_blocking(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)
            write(path / 'trajectory.json', Blocking().over_limit_track())
            write(path / 'metadata.json', metadata(object='pitcher'))
            report = compact_episode(path, emit='pose-track/1')
            self.assertIsNone(report['blocked'])
            self.assertIn('planner chooses the route', report['warning'])
            self.assertTrue((path / 'trajectory_compact.json').exists())

    def test_an_unknown_emit_format_is_refused(self):
        with self.assertRaises(ValueError):
            compact_track(demo_track(), emit='spline/1')


if __name__ == '__main__':
    unittest.main()
