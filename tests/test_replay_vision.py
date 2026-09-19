import asyncio
import copy
import json
import random
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

from PIL import Image

from primitives import replay_vision as rv
from runtime.config import read_json
from runtime.demonstrations import write
from runtime.replay import supervised_scene, run_demo
from tests.test_teach_replay import config, FakeIO


class VisionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = config()
        self.patches = [[20, 20, 36, 36], [50, 20, 66, 36]]
        self.ref = Image.new('L', (100, 80), 80)
        rng = random.Random(8)
        for l, t, r, b in self.patches:
            crop = Image.new('L', (r-l, b-t))
            crop.putdata([rng.randrange(256) for _ in range(crop.width*crop.height)])
            self.ref.paste(crop, (l, t))
        self.ref.save(self.root/'ref.png')
        self.meta = {'observations': {'before': {'frames': {'overhead': {'images': [
            {'path': 'ref.png', 'mime_type': 'image/png', 'sha256': rv.digest(self.root/'ref.png')} ]}}}}}
        write(self.root/'metadata.json', self.meta)
        self.episode = (self.root, self.meta, {})
        self.data = dict(measurement_source='operator_measured_task_frame_mm', patches=self.patches,
                         search_px=8, tolerance_mm=.1, max_offset_mm=10,
                         fit=[], check=[], reject=[])
        for group, shifts in [('fit', [(4, 0), (0, 4)]),
                              ('check', [(-4, -4), (-4, 4), (4, -4), (4, 4)])]:
            for index, shift in enumerate(shifts):
                name = f'{group}{index}.png'
                self.shifted(*shift).save(self.root/name)
                self.data[group].append(dict(image=name, offset_mm=list(shift)))
        Image.new('L', self.ref.size, 80).save(self.root/'absent.png')
        occluded = self.shifted(0, 0)
        occluded.paste(80, tuple(self.patches[0]))
        occluded.save(self.root/'occluded.png')
        self.data['reject'] = [dict(image=k+'.png', reason=k) for k in ('absent', 'occluded')]
        self.input = self.root/'measurements.json'
        self.profile = self.root/'profile.json'
        write(self.input, self.data)
        self.config['primitive_settings']['replay_vision'] = {'profiles': {'coconut_water': str(self.profile)}}

    def shifted(self, dx, dy):
        im = Image.new('L', self.ref.size, 80)
        for box in self.patches:
            im.paste(self.ref.crop(box), (box[0]+dx, box[1]+dy))
        return im

    def build(self):
        write(self.input, self.data)
        return rv.build_profile(self.config, 'coconut_water', self.episode, self.input, self.profile)

    def test_measured_profile_to_offset_preserves_axes_and_fixed_height(self):
        # A rotated camera's pixel axes are not assumed to be robot axes.
        for group in ('fit', 'check'):
            for s in self.data[group]:
                x, y = s['offset_mm']
                s['offset_mm'] = [-y, x]
        p = self.build()
        self.assertEqual(p['xy_mm_per_pixel'], [[0, -1], [1, 0]])
        self.shifted(2, -3).save(self.root/'current.png')
        offset, evidence = rv.offset(self.config, 'coconut_water', self.episode, self.root/'current.png',
                                     datetime.now(timezone.utc).isoformat())
        self.assertEqual(offset, dict(dx=3, dy=2, dz=0))
        self.assertEqual(len(evidence['patches']), 2)

    def test_missing_occluded_and_ambiguous_objects_fail(self):
        for name in ('absent', 'occluded'):
            with self.assertRaises(ValueError):
                rv.match(self.root/'ref.png', self.root/(name+'.png'), self.patches, 8)
        im = self.shifted(-9, 0)
        for box in self.patches:
            im.paste(self.ref.crop(box), (box[0]+9, box[1]))
        im.save(self.root/'duplicate.png')
        with self.assertRaisesRegex(ValueError, 'ambiguous'):
            rv.match(self.root/'ref.png', self.root/'duplicate.png', self.patches, 10)

    def test_patch_disagreement_and_wrong_resolution_fail(self):
        im = self.shifted(3, 0)
        box = self.patches[1]
        im.paste(80, (box[0]+3, box[1], box[2]+3, box[3]))
        im.paste(self.ref.crop(box), (box[0], box[1]+3))
        im.save(self.root/'disagree.png')
        with self.assertRaisesRegex(ValueError, 'disagree'):
            rv.match(self.root/'ref.png', self.root/'disagree.png', self.patches, 8)
        self.ref.resize((50, 40)).save(self.root/'small.png')
        with self.assertRaisesRegex(ValueError, 'resolution'):
            rv.match(self.root/'ref.png', self.root/'small.png', self.patches, 8)

    def test_bad_measurements_fail_instead_of_relaxing_tolerance(self):
        self.data['check'][0]['offset_mm'] = [-2, -4]
        with self.assertRaisesRegex(ValueError, 'exceeds tolerance'):
            self.build()
        self.assertFalse(self.profile.exists())

    def test_cannot_reuse_fit_image_as_independent_check(self):
        self.data['check'][0]['image'] = self.data['fit'][0]['image']
        with self.assertRaisesRegex(ValueError, 'distinct'):
            self.build()

    def test_no_extrapolation_stale_images_or_camera_change(self):
        self.build()
        self.shifted(5, 0).save(self.root/'far.png')
        with self.assertRaisesRegex(ValueError, 'region'):
            rv.offset(self.config, 'coconut_water', self.episode, self.root/'far.png', datetime.now(timezone.utc).isoformat())
        for stamp in ((datetime.now(timezone.utc)-timedelta(seconds=31)).isoformat(), 'bad', '2026-01-01'):
            with self.assertRaises(ValueError):
                rv.offset(self.config, 'coconut_water', self.episode, self.root/'ref.png', stamp)
        self.config['primitive_settings']['camera']['views']['overhead'] = 'different'
        with self.assertRaisesRegex(ValueError, 'station'):
            rv.load_profile(self.config, 'coconut_water', self.episode)

    def test_changed_measurements_or_episode_block(self):
        self.build()
        self.input.write_text(self.input.read_text()+'\n')
        with self.assertRaisesRegex(ValueError, 'evidence changed'):
            rv.load_profile(self.config, 'coconut_water', self.episode)
        write(self.input, self.data)
        (self.root/'metadata.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'episode'):
            rv.load_profile(self.config, 'coconut_water', self.episode)

    def test_degenerate_calibration_and_accepted_negative_block(self):
        for i, shift in enumerate((2, 3)):
            self.shifted(shift, shift).save(self.root/f'fit{i}.png')
            self.data['fit'][i]['offset_mm'] = [shift, shift]
        with self.assertRaisesRegex(ValueError, 'independent image directions'):
            self.build()

    def test_negative_images_must_actually_be_rejected(self):
        self.shifted(1, 1).save(self.root/'absent.png')
        with self.assertRaisesRegex(ValueError, 'Negative image was accepted'):
            self.build()


class SceneIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_vision_replaces_manual_offset_entry_and_uses_current_capture_time(self):
        c = config()
        c['primitive_settings']['replay_vision'] = {'profiles': {'coconut_water': 'measured.json'}}
        now = datetime.now(timezone.utc).isoformat()
        observation = dict(timestamp=now, frames={'overhead': dict(camera='overhead-cam', captured_at=now,
                           images=[dict(path='current.png', mime_type='image/png')])})
        io = FakeIO()
        io.capture = AsyncMock(return_value=observation)
        prompts = []
        async def yes(prompt):
            prompts.append(prompt)
            return 'yes'
        with tempfile.TemporaryDirectory() as root:
            with patch.object(rv, 'offset', return_value=(dict(dx=2, dy=-3, dz=0), {'score': 1})):
                scene = await supervised_scene('coconut_water', (Path(root), {}, {}), io, Path(root), c, yes)
        self.assertEqual(scene['source'], 'measured_patch_translation')
        self.assertEqual(scene['timestamp'], now)
        self.assertFalse(any('Measured dx,dy,dz' in p for p in prompts))
        self.assertEqual(scene['offset'], dict(dx=2, dy=-3, dz=0))

    async def test_missing_vision_profile_blocks_before_io(self):
        c = config()
        c['primitive_settings']['replay_vision'] = {'profiles': {'coconut_water': 'missing.json'}}
        episodes = {name: (Path('/tmp'), {'object': name}, {}) for name in ('coconut_water', 'pitcher')}
        io = FakeIO()
        with patch('runtime.replay.load_episode', side_effect=lambda root, name, *a, **kw: episodes[name]), \
             patch('runtime.replay.validate_episode'), patch('runtime.replay.adapted_plan'):
            with self.assertRaises(FileNotFoundError):
                await run_demo(io, c, execute=True)
        self.assertEqual(io.calls, [])


if __name__ == '__main__':
    unittest.main()
